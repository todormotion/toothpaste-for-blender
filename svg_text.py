"""
SVG text extraction.

Reads `<text>` elements into plain dictionaries holding everything needed to
rebuild them as editable Blender text: the string (one line per `<tspan>` line),
where its baseline starts, its size after every ancestor transform, rotation,
alignment, and the font as the SVG names it (family list, weight, slant).
Pure Python: this module must never import bpy.
"""

import math
import re
import xml.etree.ElementTree as ET
from typing import Dict, List, Optional, Tuple

from .constants import SODIPODI_NS, XML_NS
from .svg_utils import (
    _clamp01,
    _local_name,
    _matrix_apply_point,
    _matrix_identity,
    _matrix_multiply,
    _parse_opacity,
    _parse_style,
    _parse_svg_color,
    _parse_transform_attr,
    build_stacking_hint,
)

DEFAULT_FONT_SIZE = 16.0
DEFINITION_TAGS = {"defs", "mask", "clipPath", "symbol", "pattern", "marker"}

# Properties that inherit from parent elements down to text and tspan.
TEXT_STYLE_KEYS = (
    "font-family",
    "font-size",
    "font-weight",
    "font-style",
    "text-anchor",
    "fill",
    "fill-opacity",
    "opacity",
    "-inkscape-font-specification",
)

_FONT_SIZE_KEYWORDS = {
    "xx-small": 9.0,
    "x-small": 10.0,
    "small": 13.0,
    "medium": 16.0,
    "large": 18.0,
    "x-large": 24.0,
    "xx-large": 32.0,
}
_UNIT_TO_PX = {
    "px": 1.0,
    "pt": 96.0 / 72.0,
    "pc": 16.0,
    "mm": 96.0 / 25.4,
    "cm": 96.0 / 2.54,
    "in": 96.0,
}
_LINE_EPSILON = 1e-4


def _text_style(element: ET.Element) -> Dict[str, str]:
    """Collect text-related properties; the style attribute beats presentation attributes."""
    style: Dict[str, str] = {}
    for key in TEXT_STYLE_KEYS:
        value = element.get(key)
        if value is not None:
            style[key] = value
    style.update({key: value for key, value in _parse_style(element.get("style")).items() if key in TEXT_STYLE_KEYS})
    return style


def parse_font_size(value: Optional[str], parent_px: float = DEFAULT_FONT_SIZE) -> float:
    """Convert a CSS font-size into SVG user units (px)."""
    if value is None:
        return parent_px
    text = str(value).strip().lower()
    if not text:
        return parent_px
    if text in _FONT_SIZE_KEYWORDS:
        return _FONT_SIZE_KEYWORDS[text]
    if text == "larger":
        return parent_px * 1.2
    if text == "smaller":
        return parent_px / 1.2
    match = re.match(r"^([+-]?\d*\.?\d+(?:e[+-]?\d+)?)\s*([a-z%]*)$", text)
    if not match:
        return parent_px
    number = float(match.group(1))
    unit = match.group(2)
    if unit == "%":
        return parent_px * number / 100.0
    if unit in ("em", "rem"):
        return parent_px * number
    if unit == "ex":
        return parent_px * number * 0.5
    return number * _UNIT_TO_PX.get(unit, 1.0)


def parse_font_weight(value: Optional[str], parent: int = 400) -> Optional[int]:
    """Convert a CSS font-weight to a number, or None when it is not set."""
    if value is None:
        return None
    text = str(value).strip().lower()
    if not text:
        return None
    if text == "normal":
        return 400
    if text == "bold":
        return 700
    if text == "bolder":
        return min(parent + 300, 900)
    if text == "lighter":
        return max(parent - 300, 100)
    try:
        return max(1, min(1000, int(float(text))))
    except ValueError:
        return None


def parse_font_families(value: Optional[str]) -> List[str]:
    """Split a CSS font-family list into names, dropping quotes."""
    if not value:
        return []
    names = []
    for token in re.findall(r"\"[^\"]*\"|'[^']*'|[^,]+", str(value)):
        cleaned = token.strip().strip("'\"").strip()
        if cleaned:
            names.append(cleaned)
    return names


def _inkscape_spec(style: Dict[str, str]) -> Tuple[List[str], Optional[str]]:
    """Return ([family], style words) from Inkscape's own font specification."""
    spec = style.get("-inkscape-font-specification", "").strip().strip("'\"")
    if not spec:
        return [], None
    if "," in spec:
        family, _, words = spec.partition(",")
        return [family.strip()], words.strip() or None
    return [spec], None


def _first_number(value: Optional[str]) -> Optional[float]:
    """First number in an SVG list attribute such as x or dy, or None."""
    if value is None:
        return None
    tokens = str(value).replace(",", " ").split()
    if not tokens:
        return None
    match = re.match(r"^[+-]?\d*\.?\d+(?:[eE][+-]?\d+)?", tokens[0])
    return float(match.group(0)) if match else None


def _collapse(text: str, preserve: bool) -> str:
    if preserve:
        return text.replace("\r", "").replace("\t", " ")
    return re.sub(r"\s+", " ", text)


class _TextBuilder:
    """Walks one `<text>` element, splitting it into baseline-anchored lines."""

    def __init__(self, text_node: ET.Element, base_style: Dict[str, str]):
        self.lines: List[Dict[str, object]] = []
        self.base_x = _first_number(text_node.get("x")) or 0.0
        self.base_y = _first_number(text_node.get("y")) or 0.0
        base_dy = _first_number(text_node.get("dy")) or 0.0
        self.cursor_y = self.base_y + base_dy
        self.line_x = self.base_x
        self.style_signatures = set()
        self.first_style: Optional[Dict[str, str]] = None
        self._visit(text_node, base_style, True)

    def _start_line(self, x: float, y: float) -> Dict[str, object]:
        line = {"x": x, "y": y, "text": ""}
        self.lines.append(line)
        return line

    def _current_line(self) -> Dict[str, object]:
        if not self.lines:
            return self._start_line(self.line_x, self.cursor_y)
        return self.lines[-1]

    def _append(self, text: Optional[str], style: Dict[str, str], preserve: bool) -> None:
        if not text:
            return
        collapsed = _collapse(text, preserve)
        if not collapsed.strip() and not self.lines:
            return
        line = self._current_line()
        line["text"] = str(line["text"]) + collapsed
        if collapsed.strip():
            if self.first_style is None:
                self.first_style = dict(style)
            self.style_signatures.add(
                (
                    style.get("font-family", ""),
                    style.get("font-weight", ""),
                    style.get("font-style", ""),
                    style.get("font-size", ""),
                )
            )

    def _visit(self, element: ET.Element, style: Dict[str, str], is_root: bool) -> None:
        preserve = element.get(f"{{{XML_NS}}}space") == "preserve"
        if not is_root:
            x = _first_number(element.get("x"))
            y = _first_number(element.get("y"))
            dy = _first_number(element.get("dy")) or 0.0
            role_line = element.get(f"{{{SODIPODI_NS}}}role") == "line"

            new_y = (y if y is not None else self.cursor_y) + dy
            moved = abs(new_y - self.cursor_y) > _LINE_EPSILON
            has_content = bool(self.lines) and str(self.lines[-1]["text"]).strip() != ""
            if (moved or role_line) and (has_content or not self.lines or role_line):
                if x is not None:
                    self.line_x = x
                self.cursor_y = new_y
                if self.lines and not has_content and not role_line:
                    # nothing written on the previous line yet: reuse it
                    self.lines[-1]["x"] = self.line_x
                    self.lines[-1]["y"] = self.cursor_y
                else:
                    self._start_line(self.line_x, self.cursor_y)
            else:
                if x is not None and not self.lines:
                    self.line_x = x
                if y is not None and not self.lines:
                    self.cursor_y = new_y

        self._append(element.text, style, preserve)
        for child in element:
            if not isinstance(child.tag, str) or _local_name(child.tag) not in ("tspan", "textPath", "a"):
                self._append(child.tail, style, preserve)
                continue
            child_style = dict(style)
            child_style.update(_text_style(child))
            self._visit(child, child_style, False)
            self._append(child.tail, style, preserve)


def _median(values: List[float]) -> float:
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / 2.0


def _build_item(
    text_node: ET.Element,
    matrix: Tuple[float, float, float, float, float, float],
    style: Dict[str, str],
    index: int,
) -> Optional[Dict[str, object]]:
    builder = _TextBuilder(text_node, style)
    lines = [
        {"x": line["x"], "y": line["y"], "text": str(line["text"]).strip()}
        for line in builder.lines
    ]
    while lines and not lines[0]["text"]:
        lines.pop(0)
    while lines and not lines[-1]["text"]:
        lines.pop()
    if not lines:
        return None

    effective = builder.first_style or style
    parent_size = DEFAULT_FONT_SIZE
    font_size = parse_font_size(effective.get("font-size"), parent_size)

    families = parse_font_families(effective.get("font-family"))
    spec_families, spec_words = _inkscape_spec(effective)
    if spec_families:
        for name in reversed(spec_families):
            if name not in families:
                families.insert(0, name)

    weight = parse_font_weight(effective.get("font-weight"))
    italic_value = (effective.get("font-style") or "").strip().lower()
    italic = italic_value in ("italic", "oblique") or italic_value.startswith("oblique")
    if spec_words:
        lowered = spec_words.lower()
        if weight is None:
            weight = _weight_from_words(lowered)
        if not italic and ("italic" in lowered or "oblique" in lowered):
            italic = True
    if weight is None:
        weight = 400

    anchor = (effective.get("text-anchor") or "start").strip().lower()
    if anchor not in ("start", "middle", "end"):
        anchor = "start"

    pitches = [
        abs(float(lines[i + 1]["y"]) - float(lines[i]["y"])) for i in range(len(lines) - 1)
    ]
    pitches = [pitch for pitch in pitches if pitch > _LINE_EPSILON]
    line_spacing = (_median(pitches) / font_size) if (pitches and font_size > 0) else 1.0

    a, b, c, d, _e, _f = matrix
    determinant = abs(a * d - b * c)
    scale = math.sqrt(determinant) if determinant > 1e-12 else 1.0
    rotation = math.atan2(b, a)
    x, y = _matrix_apply_point(matrix, float(lines[0]["x"]), float(lines[0]["y"]))

    fill_value = effective.get("fill", "#000000")
    color = None if str(fill_value).strip().startswith("url(") else _parse_svg_color(fill_value)
    if color is not None:
        alpha = _parse_opacity(effective.get("fill-opacity"), 1.0) * _parse_opacity(effective.get("opacity"), 1.0)
        color = (color[0], color[1], color[2], _clamp01(color[3] * alpha))

    return {
        "name": text_node.get("id") or f"Text_{index}",
        "body": "\n".join(str(line["text"]) for line in lines),
        "x": x,
        "y": y,
        "font_size": font_size * scale,
        "rotation": rotation,
        "line_spacing": line_spacing,
        "anchor": anchor,
        "font_families": families,
        "font_family": ", ".join(families),
        "font_weight": int(weight),
        "font_italic": bool(italic),
        "fill": color,
        "mixed_styles": len(builder.style_signatures) > 1,
    }


def _weight_from_words(lowered: str) -> Optional[int]:
    compact = re.sub(r"[^a-z]", "", lowered)
    for word, value in (
        ("extralight", 200),
        ("ultralight", 200),
        ("extrabold", 800),
        ("ultrabold", 800),
        ("semibold", 600),
        ("demibold", 600),
        ("thin", 100),
        ("light", 300),
        ("medium", 500),
        ("bold", 700),
        ("heavy", 800),
        ("black", 900),
        ("regular", 400),
        ("normal", 400),
    ):
        if word in compact:
            return value
    return None


def collect_text_items(root: ET.Element) -> List[Dict[str, object]]:
    """Extract every visible `<text>` element as an importable item."""
    items: List[Dict[str, object]] = []
    above = build_stacking_hint(root)

    def walk(
        node: ET.Element,
        matrix: Tuple[float, float, float, float, float, float],
        inherited: Dict[str, str],
        in_definition: bool,
    ) -> None:
        if not isinstance(node.tag, str):
            return
        tag = _local_name(node.tag)
        active = _matrix_multiply(matrix, _parse_transform_attr(node.get("transform")))
        style = dict(inherited)
        style.update(_text_style(node))
        hidden = in_definition or tag in DEFINITION_TAGS
        if tag == "text" and not hidden:
            item = _build_item(node, active, style, len(items) + 1)
            if item:
                item["above_art"] = above(node)
                items.append(item)
            return
        for child in list(node):
            walk(child, active, style, hidden)

    walk(root, _matrix_identity(), {}, False)
    return items
