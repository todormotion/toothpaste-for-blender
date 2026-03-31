"""
ToothPaste for Blender - SVG Clipboard Tools for Grease Pencil and Curves.

V1.1 highlights:
- Paste SVG as Grease Pencil or Curve objects
- Optional split mode for SVG layers on paste
- GP layer mask and clip-path reconstruction using GP masks + holdout layers
- SVG text parsing to Blender FONT objects
- SVG <image> support for embedded/local images
- Layer splitting without bpy.ops object duplication
"""

bl_info = {
    "name": "ToothPaste for Blender",
    "author": "Cosmonavt",
    "version": (1, 1, 5),
    "blender": (4, 3, 0),
    "category": "Import-Export",
    "location": "View3D > Sidebar > ToothPaste",
    "description": "Paste SVG from clipboard as Grease Pencil or Curves, with layer split, masks, text, and image support.",
}

import base64
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from typing import Dict, List, Optional, Set, Tuple

import bpy

try:
    from .exceptions import InvalidSVGError, ClipboardError
except ImportError:
    from exceptions import InvalidSVGError, ClipboardError


GP_TYPE = "GREASEPENCIL"
SVG_NS = "http://www.w3.org/2000/svg"
XLINK_NS = "http://www.w3.org/1999/xlink"
INKSCAPE_NS = "http://www.inkscape.org/namespaces/inkscape"
NS = {
    "svg": SVG_NS,
    "xlink": XLINK_NS,
    "inkscape": INKSCAPE_NS,
}
SUPPORTED_SHAPES = {"rect", "circle", "ellipse", "line", "polyline", "polygon"}
SUPPORTED_GEOMETRY = {"path"} | SUPPORTED_SHAPES
MASK_LAYER_PREFIX = "__TP_MASK__"
CLIP_LAYER_PREFIX = "__TP_CLIP__"
RESOURCE_TAGS = {
    "defs",
    "style",
    "clipPath",
    "mask",
    "linearGradient",
    "radialGradient",
    "pattern",
    "symbol",
}

ET.register_namespace("", SVG_NS)
ET.register_namespace("xlink", XLINK_NS)


def _local_name(tag: str) -> str:
    """Return local XML tag name without namespace."""
    return tag.split("}")[-1] if "}" in tag else tag


def _clone_xml_element(element: ET.Element) -> ET.Element:
    """Deep clone XML element via serialization."""
    return ET.fromstring(ET.tostring(element, encoding="unicode"))


def _parse_svg_number(value: Optional[str], default: float = 0.0) -> float:
    """Parse numeric SVG values including unit-suffixed values."""
    if value is None:
        return default
    text = str(value).strip()
    if not text:
        return default
    match = re.match(r"^[\s]*([+-]?\d*\.?\d+(?:[eE][+-]?\d+)?)", text)
    if not match:
        return default
    try:
        return float(match.group(1))
    except ValueError:
        return default


def _first_list_number(value: Optional[str], default: float = 0.0) -> float:
    """Parse first number from SVG list-style attributes like x/y."""
    if value is None:
        return default
    token = str(value).replace(",", " ").strip().split()
    if not token:
        return default
    return _parse_svg_number(token[0], default=default)


def _parse_style(style_text: Optional[str]) -> Dict[str, str]:
    """Parse CSS style declaration string."""
    style: Dict[str, str] = {}
    if not style_text:
        return style
    for chunk in style_text.split(";"):
        if ":" not in chunk:
            continue
        key, value = chunk.split(":", 1)
        key = key.strip().lower()
        value = value.strip()
        if key:
            style[key] = value
    return style


def _style_to_string(style: Dict[str, str]) -> str:
    """Serialize style dictionary to SVG style declaration string."""
    return ";".join(f"{key}:{value}" for key, value in style.items())


def _collect_css_class_styles(root: ET.Element) -> Dict[str, Dict[str, str]]:
    """Parse <style> blocks and collect simple class selectors (.class)."""
    class_styles: Dict[str, Dict[str, str]] = {}
    for node in root.iter():
        if not isinstance(node.tag, str) or _local_name(node.tag) != "style":
            continue
        css_text = "".join(node.itertext())
        if not css_text:
            continue
        css_text = re.sub(r"/\*.*?\*/", "", css_text, flags=re.DOTALL)
        for selector_group, declarations in re.findall(r"([^{}]+)\{([^}]*)\}", css_text):
            props = _parse_style(declarations)
            if not props:
                continue
            selectors = [selector.strip() for selector in selector_group.split(",")]
            for selector in selectors:
                if not selector.startswith("."):
                    continue
                class_name = selector[1:].strip()
                if not class_name:
                    continue
                target = class_styles.setdefault(class_name, {})
                target.update(props)
    return class_styles


def _inline_css_class_styles(root: ET.Element) -> None:
    """Inline class-based CSS styles into style attributes for parser compatibility."""
    class_styles = _collect_css_class_styles(root)
    if not class_styles:
        return

    for node in root.iter():
        if not isinstance(node.tag, str):
            continue
        class_attr = node.get("class")
        if not class_attr:
            continue

        merged_style: Dict[str, str] = {}
        for class_name in class_attr.replace(",", " ").split():
            class_props = class_styles.get(class_name.strip())
            if class_props:
                merged_style.update(class_props)

        if not merged_style:
            continue

        inline_style = _parse_style(node.get("style"))
        merged_style.update(inline_style)
        node.set("style", _style_to_string(merged_style))


def _clamp01(value: float) -> float:
    """Clamp number to [0, 1]."""
    return max(0.0, min(1.0, value))


def _parse_opacity(value: Optional[str], default: float = 1.0) -> float:
    """Parse opacity-like value in [0, 1]."""
    if value is None:
        return default
    text = str(value).strip()
    if not text:
        return default
    if text.endswith("%"):
        try:
            return _clamp01(float(text[:-1]) / 100.0)
        except ValueError:
            return default
    try:
        return _clamp01(float(text))
    except ValueError:
        return default


def _srgb_channel_to_linear(channel: float) -> float:
    """Convert sRGB channel in [0, 1] to Blender's linear color space."""
    channel = _clamp01(channel)
    if channel <= 0.04045:
        return channel / 12.92
    return ((channel + 0.055) / 1.055) ** 2.4


def _srgb_rgba_to_linear(
    rgba: Tuple[float, float, float, float],
) -> Tuple[float, float, float, float]:
    """Convert sRGB RGBA tuple to linear RGB with unchanged alpha."""
    return (
        _srgb_channel_to_linear(float(rgba[0])),
        _srgb_channel_to_linear(float(rgba[1])),
        _srgb_channel_to_linear(float(rgba[2])),
        _clamp01(float(rgba[3])),
    )


def _parse_svg_color(value: Optional[str]) -> Optional[Tuple[float, float, float, float]]:
    """Parse SVG color string into RGBA."""
    if value is None:
        return None
    text = str(value).strip().lower()
    if not text or text in {"none", "transparent"}:
        return None

    named = {
        "black": (0.0, 0.0, 0.0, 1.0),
        "white": (1.0, 1.0, 1.0, 1.0),
        "red": (1.0, 0.0, 0.0, 1.0),
        "green": (0.0, 1.0, 0.0, 1.0),
        "blue": (0.0, 0.0, 1.0, 1.0),
        "yellow": (1.0, 1.0, 0.0, 1.0),
        "gray": (0.5, 0.5, 0.5, 1.0),
        "grey": (0.5, 0.5, 0.5, 1.0),
    }
    if text in named:
        return _srgb_rgba_to_linear(named[text])

    if text.startswith("#"):
        hexv = text[1:]
        if len(hexv) == 3:
            r = int(hexv[0] * 2, 16)
            g = int(hexv[1] * 2, 16)
            b = int(hexv[2] * 2, 16)
            return _srgb_rgba_to_linear((r / 255.0, g / 255.0, b / 255.0, 1.0))
        if len(hexv) == 4:
            r = int(hexv[0] * 2, 16)
            g = int(hexv[1] * 2, 16)
            b = int(hexv[2] * 2, 16)
            a = int(hexv[3] * 2, 16)
            return _srgb_rgba_to_linear((r / 255.0, g / 255.0, b / 255.0, a / 255.0))
        if len(hexv) == 6:
            r = int(hexv[0:2], 16)
            g = int(hexv[2:4], 16)
            b = int(hexv[4:6], 16)
            return _srgb_rgba_to_linear((r / 255.0, g / 255.0, b / 255.0, 1.0))
        if len(hexv) == 8:
            r = int(hexv[0:2], 16)
            g = int(hexv[2:4], 16)
            b = int(hexv[4:6], 16)
            a = int(hexv[6:8], 16)
            return _srgb_rgba_to_linear((r / 255.0, g / 255.0, b / 255.0, a / 255.0))
        return None

    rgb_match = re.match(r"rgba?\(([^)]+)\)", text)
    if rgb_match:
        tokens = [token.strip() for token in rgb_match.group(1).split(",")]
        if len(tokens) < 3:
            return None

        def channel(tok: str) -> float:
            if tok.endswith("%"):
                return _clamp01(float(tok[:-1]) / 100.0)
            return _clamp01(float(tok) / 255.0)

        try:
            r = channel(tokens[0])
            g = channel(tokens[1])
            b = channel(tokens[2])
            a = _parse_opacity(tokens[3], 1.0) if len(tokens) >= 4 else 1.0
            return _srgb_rgba_to_linear((r, g, b, a))
        except ValueError:
            return None
    return None


def _presentation_style(element: ET.Element) -> Dict[str, str]:
    """Extract style dictionary from style attribute + presentation attrs."""
    style = _parse_style(element.get("style"))
    for key in (
        "fill",
        "fill-opacity",
        "fill-rule",
        "stroke",
        "stroke-opacity",
        "opacity",
        "stroke-width",
        "stroke-linecap",
        "stroke-linejoin",
        "clip-path",
        "mask",
    ):
        value = element.get(key)
        if value is not None:
            style[key] = value
    return style


def _resolve_fill_spec(style: Dict[str, str]) -> Dict[str, object]:
    """Resolve fill style into solid/gradient spec."""
    fill_value = style.get("fill", "#000000")
    opacity = _parse_opacity(style.get("opacity"), 1.0) * _parse_opacity(style.get("fill-opacity"), 1.0)

    gradient_ref = _extract_url_ref(fill_value)
    if gradient_ref:
        return {"kind": "GRADIENT", "gradient_id": gradient_ref, "opacity": opacity}

    color = _parse_svg_color(fill_value)
    if color is None:
        return {"kind": "NONE", "opacity": opacity}
    return {
        "kind": "SOLID",
        "color": (color[0], color[1], color[2], _clamp01(color[3] * opacity)),
        "opacity": opacity,
    }


def _resolve_stroke_spec(style: Dict[str, str]) -> Dict[str, object]:
    """Resolve stroke style into solid/none spec."""
    stroke_value = style.get("stroke", "none")
    opacity = _parse_opacity(style.get("opacity"), 1.0) * _parse_opacity(style.get("stroke-opacity"), 1.0)
    width = max(_parse_svg_number(style.get("stroke-width"), 0.0), 0.0)

    color = _parse_svg_color(stroke_value)
    if color is None or width <= 0.0:
        return {"kind": "NONE", "width": width, "opacity": opacity}

    return {
        "kind": "SOLID",
        "color": (color[0], color[1], color[2], _clamp01(color[3] * opacity)),
        "width": width,
        "opacity": opacity,
    }


def _parse_gradient_offset(value: Optional[str]) -> float:
    """Parse SVG stop offset."""
    if value is None:
        return 0.0
    text = str(value).strip()
    if not text:
        return 0.0
    if text.endswith("%"):
        try:
            return _clamp01(float(text[:-1]) / 100.0)
        except ValueError:
            return 0.0
    try:
        return _clamp01(float(text))
    except ValueError:
        return 0.0


def _parse_gradient_coord(
    value: Optional[str],
    default: float,
) -> Tuple[float, bool]:
    """Parse gradient coordinate and report whether input was percentage-based."""
    if value is None:
        return (default, False)
    text = str(value).strip()
    if not text:
        return (default, False)
    if text.endswith("%"):
        try:
            return (_parse_svg_number(text[:-1], default * 100.0) / 100.0, True)
        except ValueError:
            return (default, False)
    return (_parse_svg_number(text, default), False)


def _matrix_identity() -> Tuple[float, float, float, float, float, float]:
    """Return identity affine matrix."""
    return (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)


def _matrix_multiply(
    left: Tuple[float, float, float, float, float, float],
    right: Tuple[float, float, float, float, float, float],
) -> Tuple[float, float, float, float, float, float]:
    """Compose two SVG affine matrices: result = left * right."""
    a1, b1, c1, d1, e1, f1 = left
    a2, b2, c2, d2, e2, f2 = right
    return (
        a1 * a2 + c1 * b2,
        b1 * a2 + d1 * b2,
        a1 * c2 + c1 * d2,
        b1 * c2 + d1 * d2,
        a1 * e2 + c1 * f2 + e1,
        b1 * e2 + d1 * f2 + f1,
    )


def _matrix_apply_point(
    matrix: Tuple[float, float, float, float, float, float],
    x: float,
    y: float,
) -> Tuple[float, float]:
    """Apply affine matrix to point."""
    a, b, c, d, e, f = matrix
    return (a * x + c * y + e, b * x + d * y + f)


def _matrix_to_svg(matrix: Tuple[float, float, float, float, float, float]) -> str:
    """Serialize matrix tuple to SVG matrix(...) string."""
    a, b, c, d, e, f = matrix
    return f"matrix({a},{b},{c},{d},{e},{f})"


def _is_identity_matrix(matrix: Tuple[float, float, float, float, float, float]) -> bool:
    """Check if matrix is approximately identity."""
    identity = _matrix_identity()
    return all(abs(matrix[i] - identity[i]) < 1e-10 for i in range(6))


def _parse_transform_attr(value: Optional[str]) -> Tuple[float, float, float, float, float, float]:
    """Parse SVG transform attribute into one affine matrix."""
    if not value:
        return _matrix_identity()

    transform = _matrix_identity()
    for name, args_text in re.findall(r"([a-zA-Z]+)\(([^)]*)\)", value):
        tokens = [tok for tok in re.split(r"[,\s]+", args_text.strip()) if tok]
        name = name.strip()
        local = _matrix_identity()
        try:
            if name == "matrix" and len(tokens) == 6:
                local = tuple(float(tok) for tok in tokens)  # type: ignore[assignment]
            elif name == "translate":
                tx = float(tokens[0]) if len(tokens) >= 1 else 0.0
                ty = float(tokens[1]) if len(tokens) >= 2 else 0.0
                local = (1.0, 0.0, 0.0, 1.0, tx, ty)
            elif name == "scale":
                sx = float(tokens[0]) if len(tokens) >= 1 else 1.0
                sy = float(tokens[1]) if len(tokens) >= 2 else sx
                local = (sx, 0.0, 0.0, sy, 0.0, 0.0)
            elif name == "rotate":
                angle = math.radians(float(tokens[0])) if len(tokens) >= 1 else 0.0
                cos_a = math.cos(angle)
                sin_a = math.sin(angle)
                rotate_m = (cos_a, sin_a, -sin_a, cos_a, 0.0, 0.0)
                if len(tokens) >= 3:
                    cx = float(tokens[1])
                    cy = float(tokens[2])
                    to_origin = (1.0, 0.0, 0.0, 1.0, -cx, -cy)
                    back = (1.0, 0.0, 0.0, 1.0, cx, cy)
                    local = _matrix_multiply(back, _matrix_multiply(rotate_m, to_origin))
                else:
                    local = rotate_m
            elif name == "skewX" and len(tokens) >= 1:
                t = math.tan(math.radians(float(tokens[0])))
                local = (1.0, 0.0, t, 1.0, 0.0, 0.0)
            elif name == "skewY" and len(tokens) >= 1:
                t = math.tan(math.radians(float(tokens[0])))
                local = (1.0, t, 0.0, 1.0, 0.0, 0.0)
        except ValueError:
            continue
        transform = _matrix_multiply(transform, local)
    return transform


def _collect_gradient_defs(root: ET.Element) -> Dict[str, Dict[str, object]]:
    """Extract linear/radial gradient definitions."""
    gradients: Dict[str, Dict[str, object]] = {}
    for element in root.iter():
        if not isinstance(element.tag, str):
            continue
        tag = _local_name(element.tag)
        if tag not in {"linearGradient", "radialGradient"}:
            continue

        gradient_id = element.get("id")
        if not gradient_id:
            continue

        stops: List[Tuple[float, Tuple[float, float, float, float]]] = []
        for stop in list(element):
            if not isinstance(stop.tag, str) or _local_name(stop.tag) != "stop":
                continue
            stop_style = _parse_style(stop.get("style"))
            stop_color = stop.get("stop-color") or stop_style.get("stop-color")
            color = _parse_svg_color(stop_color)
            if color is None:
                continue
            stop_opacity = _parse_opacity(stop.get("stop-opacity") or stop_style.get("stop-opacity"), 1.0)
            rgba = (color[0], color[1], color[2], _clamp01(color[3] * stop_opacity))
            stops.append((_parse_gradient_offset(stop.get("offset")), rgba))

        if not stops:
            continue
        stops.sort(key=lambda item: item[0])
        first_color = stops[0][1]
        second_color = stops[-1][1]
        matrix = _parse_transform_attr(element.get("gradientTransform"))
        if _is_identity_matrix(matrix):
            matrix = None
        gradients[gradient_id] = {
            "type": "LINEAR" if tag == "linearGradient" else "RADIAL",
            "units": str(element.get("gradientUnits", "objectBoundingBox")).strip(),
            "x1": element.get("x1"),
            "y1": element.get("y1"),
            "x2": element.get("x2"),
            "y2": element.get("y2"),
            "matrix": matrix,
            "first_color": first_color,
            "second_color": second_color,
        }
    return gradients

def _extract_url_ref(value: Optional[str]) -> Optional[str]:
    """Extract id from url(#id) attribute values."""
    if not value:
        return None
    match = re.search(r"url\(\s*#([^)]+)\s*\)", value.strip())
    if not match:
        return None
    return match.group(1).strip()


def _extract_mask_clip_refs(element: ET.Element) -> Tuple[Set[str], Set[str]]:
    """Extract mask and clip-path id references from element attributes and style."""
    mask_refs: Set[str] = set()
    clip_refs: Set[str] = set()

    mask_attr = _extract_url_ref(element.get("mask"))
    if mask_attr:
        mask_refs.add(mask_attr)

    clip_attr = _extract_url_ref(element.get("clip-path"))
    if clip_attr:
        clip_refs.add(clip_attr)

    style = _parse_style(element.get("style"))
    style_mask = _extract_url_ref(style.get("mask"))
    if style_mask:
        mask_refs.add(style_mask)

    style_clip = _extract_url_ref(style.get("clip-path"))
    if style_clip:
        clip_refs.add(style_clip)

    return mask_refs, clip_refs


def validate_svg_string(svg_string: str) -> bool:
    """Validate that string contains SVG XML."""
    if not svg_string or not svg_string.strip():
        raise InvalidSVGError("SVG data is empty")

    try:
        root = ET.fromstring(svg_string)
    except ET.ParseError as exc:
        raise InvalidSVGError(f"Invalid XML format: {exc}") from exc

    tag = _local_name(root.tag).lower()
    if tag != "svg":
        raise InvalidSVGError(f"Root element is not SVG (found: {tag})")
    return True


def _extract_svg_fragment(text: Optional[str]) -> str:
    """Extract the actual SVG XML fragment from clipboard-like text."""
    if not text:
        return ""

    cleaned = str(text).replace("\x00", "").lstrip("\ufeff").strip()
    if not cleaned:
        return ""

    try:
        validate_svg_string(cleaned)
        return cleaned
    except InvalidSVGError:
        pass

    start = cleaned.find("<svg")
    if start == -1:
        return cleaned

    end_match = re.search(r"</svg\s*>", cleaned[start:], re.IGNORECASE)
    if end_match:
        end = start + end_match.end()
        return cleaned[start:end].strip()
    return cleaned[start:].strip()


def _read_platform_clipboard_text() -> str:
    """Best-effort fallback clipboard text reader when Blender clipboard is empty."""
    commands: List[List[str]] = []
    if sys.platform == "darwin":
        commands.append(["pbpaste"])
    elif sys.platform.startswith("win"):
        commands.append(["powershell", "-NoProfile", "-Command", "Get-Clipboard -Raw"])
    else:
        if shutil.which("wl-paste"):
            commands.append(["wl-paste", "-n"])
        if shutil.which("xclip"):
            commands.append(["xclip", "-selection", "clipboard", "-o"])
        if shutil.which("xsel"):
            commands.append(["xsel", "--clipboard", "--output"])

    for command in commands:
        try:
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                check=False,
            )
        except Exception:
            continue
        if result.returncode != 0:
            continue
        fragment = _extract_svg_fragment(result.stdout)
        if fragment:
            return fragment
    return ""


def parse_svg_root(svg_data: str) -> ET.Element:
    """Parse SVG string to XML root."""
    svg_fragment = _extract_svg_fragment(svg_data)
    validate_svg_string(svg_fragment)
    try:
        root = ET.fromstring(svg_fragment)
    except ET.ParseError as exc:
        raise InvalidSVGError(f"Invalid XML format: {exc}") from exc
    _inline_css_class_styles(root)
    return root


def get_clipboard_svg() -> str:
    """Get SVG from clipboard and validate."""
    try:
        clipboard_text = bpy.context.window_manager.clipboard
    except Exception as exc:
        raise ClipboardError(f"Failed to access clipboard: {exc}") from exc

    svg_fragment = _extract_svg_fragment(clipboard_text)
    if not svg_fragment:
        svg_fragment = _read_platform_clipboard_text()

    try:
        validate_svg_string(svg_fragment)
    except InvalidSVGError as exc:
        raise ClipboardError(f"Clipboard does not contain valid SVG: {exc}") from exc
    return svg_fragment


def convert_shape_to_path(element: ET.Element) -> Optional[str]:
    """Convert supported SVG shape elements to path `d` data."""
    tag = _local_name(element.tag)
    if tag == "rect":
        x = _parse_svg_number(element.get("x"), 0.0)
        y = _parse_svg_number(element.get("y"), 0.0)
        width = _parse_svg_number(element.get("width"), 0.0)
        height = _parse_svg_number(element.get("height"), 0.0)
        return f"M{x},{y} h{width} v{height} h{-width} Z"

    if tag == "circle":
        cx = _parse_svg_number(element.get("cx"), 0.0)
        cy = _parse_svg_number(element.get("cy"), 0.0)
        radius = _parse_svg_number(element.get("r"), 0.0)
        return f"M{cx - radius},{cy} A{radius},{radius} 0 1,0 {cx + radius},{cy} A{radius},{radius} 0 1,0 {cx - radius},{cy} Z"

    if tag == "ellipse":
        cx = _parse_svg_number(element.get("cx"), 0.0)
        cy = _parse_svg_number(element.get("cy"), 0.0)
        rx = _parse_svg_number(element.get("rx"), 0.0)
        ry = _parse_svg_number(element.get("ry"), 0.0)
        return f"M{cx - rx},{cy} A{rx},{ry} 0 1,0 {cx + rx},{cy} A{rx},{ry} 0 1,0 {cx - rx},{cy} Z"

    if tag == "line":
        x1 = _parse_svg_number(element.get("x1"), 0.0)
        y1 = _parse_svg_number(element.get("y1"), 0.0)
        x2 = _parse_svg_number(element.get("x2"), 0.0)
        y2 = _parse_svg_number(element.get("y2"), 0.0)
        return f"M{x1},{y1} L{x2},{y2}"

    if tag in {"polyline", "polygon"}:
        points = (element.get("points") or "").strip().replace(",", " ")
        coords = points.split()
        if len(coords) < 4:
            return None
        path_data = f"M{coords[0]},{coords[1]}" + "".join(
            f" L{coords[i]},{coords[i + 1]}" for i in range(2, len(coords) - 1, 2)
        )
        return f"{path_data} Z" if tag == "polygon" else path_data

    return None


def _layer_name_from_group(group: ET.Element, index: int) -> str:
    """Extract preferred layer name from SVG group metadata."""
    return (
        group.attrib.get("id")
        or group.attrib.get(f"{{{INKSCAPE_NS}}}label")
        or f"Group_{index + 1}"
    )


def _group_name_from_node(node: ET.Element) -> str:
    """Extract preferred grouping name from an SVG group node."""
    return (
        node.attrib.get("id")
        or node.attrib.get(f"{{{INKSCAPE_NS}}}label")
        or ""
    ).strip()


def _collect_top_level_groups(root: ET.Element) -> List[ET.Element]:
    """Return SVG groups used as logical layers plus any ungrouped root content."""
    groups = root.findall("./svg:g", NS)
    fallback_group = ET.Element(f"{{{SVG_NS}}}g", {"id": "Layer_1"})
    has_content = False
    for child in list(root):
        tag = _local_name(child.tag)
        if tag in {"defs", "metadata", "title", "desc"}:
            continue
        if tag == "g":
            continue
        fallback_group.append(_clone_xml_element(child))
        has_content = True

    if groups and has_content:
        ungrouped = _clone_xml_element(fallback_group)
        ungrouped.set("id", "Ungrouped")
        return groups + [ungrouped]
    if groups:
        return groups
    return [fallback_group] if has_content else []


def _collect_layer_payload(group: ET.Element, index: int) -> Dict[str, object]:
    """Extract layer metadata and supported path data from one SVG group."""
    paths: List[str] = []
    mask_refs: Set[str] = set()
    clip_refs: Set[str] = set()
    has_content = False
    has_geometry = False

    for element in group.iter():
        tag = _local_name(element.tag)
        if tag in SUPPORTED_GEOMETRY:
            has_content = True
            has_geometry = True
            if tag == "path":
                path_data = element.attrib.get("d")
            else:
                path_data = convert_shape_to_path(element)
            if path_data:
                paths.append(path_data)
        elif tag in {"text", "image"}:
            has_content = True

        layer_masks, layer_clips = _extract_mask_clip_refs(element)
        mask_refs.update(layer_masks)
        clip_refs.update(layer_clips)

    return {
        "name": _layer_name_from_group(group, index),
        "element": _clone_xml_element(group),
        "paths": paths,
        "mask_refs": sorted(mask_refs),
        "clip_refs": sorted(clip_refs),
        "has_content": has_content,
        "has_geometry": has_geometry,
    }


def _collect_defs_maps(root: ET.Element) -> Tuple[Dict[str, ET.Element], Dict[str, ET.Element]]:
    """Extract mask and clipPath definitions from anywhere in the SVG tree."""
    mask_defs: Dict[str, ET.Element] = {}
    clip_defs: Dict[str, ET.Element] = {}

    for child in root.iter():
        if not isinstance(child.tag, str):
            continue
        tag = _local_name(child.tag)
        definition_id = child.get("id")
        if not definition_id:
            continue
        if tag == "mask":
            mask_defs[definition_id] = _clone_xml_element(child)
        elif tag == "clipPath":
            clip_defs[definition_id] = _clone_xml_element(child)
    return mask_defs, clip_defs


def _collect_text_items(root: ET.Element) -> List[Dict[str, object]]:
    """Extract SVG text nodes for FONT object creation."""
    text_items: List[Dict[str, object]] = []
    for index, text_node in enumerate(root.findall(".//svg:text", NS)):
        body = "".join(text_node.itertext()).strip()
        if not body:
            continue
        style = _parse_style(text_node.get("style"))
        font_size = _first_list_number(
            text_node.get("font-size") or style.get("font-size"),
            default=16.0,
        )
        text_items.append(
            {
                "name": text_node.get("id") or f"Text_{index + 1}",
                "body": body,
                "x": _first_list_number(text_node.get("x"), default=0.0),
                "y": _first_list_number(text_node.get("y"), default=0.0),
                "font_size": font_size,
                "font_family": (text_node.get("font-family") or style.get("font-family") or "").strip(),
            }
        )
    return text_items


def _collect_image_items(root: ET.Element) -> List[Dict[str, object]]:
    """Extract SVG image nodes."""
    image_items: List[Dict[str, object]] = []
    image_index = 0

    def walk(node: ET.Element, definition_context: bool = False) -> None:
        nonlocal image_index
        if not isinstance(node.tag, str):
            return
        tag = _local_name(node.tag)
        in_definition = definition_context or tag in {"defs", "mask", "clipPath", "symbol", "pattern"}
        if tag == "image" and not in_definition:
            href = node.get(f"{{{XLINK_NS}}}href") or node.get("href")
            if href:
                image_index += 1
                image_items.append(
                    {
                        "name": node.get("id") or f"Image_{image_index}",
                        "href": href.strip(),
                        "x": _parse_svg_number(node.get("x"), 0.0),
                        "y": _parse_svg_number(node.get("y"), 0.0),
                        "width": _parse_svg_number(node.get("width"), 1.0),
                        "height": _parse_svg_number(node.get("height"), 1.0),
                    }
                )
        for child in list(node):
            walk(child, in_definition)

    walk(root, False)
    return image_items


def _collect_svg_metadata(root: ET.Element) -> Dict[str, object]:
    """Collect parsed SVG data used by GP/Curve import operators."""
    groups = _collect_top_level_groups(root)
    layers: List[Dict[str, object]] = []
    for index, group in enumerate(groups):
        payload = _collect_layer_payload(group, index)
        if payload["has_content"]:
            layers.append(payload)

    mask_defs, clip_defs = _collect_defs_maps(root)
    gradient_defs = _collect_gradient_defs(root)
    return {
        "layers": layers,
        "mask_defs": mask_defs,
        "clip_defs": clip_defs,
        "gradient_defs": gradient_defs,
        "texts": _collect_text_items(root),
        "images": _collect_image_items(root),
        "has_drawables": any(layer["has_geometry"] for layer in layers),
    }


def parse_svg(svg_data: str) -> List[Dict[str, object]]:
    """Backward-compatible public parser output for existing tests/callers."""
    root = parse_svg_root(svg_data)
    metadata = _collect_svg_metadata(root)
    return [
        {"name": layer["name"], "paths": layer["paths"]}
        for layer in metadata["layers"]
        if layer["paths"]
    ]


def _build_svg_document(source_root: ET.Element, group_elements: List[ET.Element]) -> str:
    """Build SVG string with specific top-level groups and source root attributes."""
    root_copy = ET.Element(source_root.tag, dict(source_root.attrib))
    for child in list(source_root):
        if not isinstance(child.tag, str):
            continue
        if _local_name(child.tag) in RESOURCE_TAGS:
            root_copy.append(_clone_xml_element(child))
    for group in group_elements:
        root_copy.append(_clone_xml_element(group))
    return ET.tostring(root_copy, encoding="unicode")


def _strip_mask_clip_references(
    node: ET.Element,
    keep_mask_refs: Optional[Set[str]] = None,
    keep_clip_refs: Optional[Set[str]] = None,
) -> None:
    """Remove only SVG mask/clip refs that ToothPaste is rebuilding in GP."""
    if not isinstance(node.tag, str):
        return

    keep_mask_refs = keep_mask_refs or set()
    keep_clip_refs = keep_clip_refs or set()

    mask_attr = _extract_url_ref(node.get("mask"))
    if mask_attr and mask_attr not in keep_mask_refs and "mask" in node.attrib:
        del node.attrib["mask"]

    clip_attr = _extract_url_ref(node.get("clip-path"))
    if clip_attr and clip_attr not in keep_clip_refs and "clip-path" in node.attrib:
        del node.attrib["clip-path"]

    style = _parse_style(node.get("style"))
    if style:
        style_mask = _extract_url_ref(style.get("mask"))
        if style_mask and style_mask not in keep_mask_refs:
            style.pop("mask", None)
        style_clip = _extract_url_ref(style.get("clip-path"))
        if style_clip and style_clip not in keep_clip_refs:
            style.pop("clip-path", None)
        if style:
            node.set("style", _style_to_string(style))
        elif "style" in node.attrib:
            del node.attrib["style"]

    for child in list(node):
        _strip_mask_clip_references(child, keep_mask_refs, keep_clip_refs)


def _flatten_layers_to_group(layers: List[Dict[str, object]], name: str = "Layer_1") -> Optional[ET.Element]:
    """Flatten all layer groups into one group for non-split imports."""
    if not layers:
        return None
    flattened = ET.Element(f"{{{SVG_NS}}}g", {"id": name})
    for layer in layers:
        group = layer["element"]
        flattened.append(_clone_xml_element(group))
    return flattened if len(flattened) else None


def _build_definition_groups(definitions: Dict[str, ET.Element], prefix: str) -> List[ET.Element]:
    """Convert mask/clip definitions to importable groups."""
    groups: List[ET.Element] = []
    for definition_id, definition_node in definitions.items():
        group = ET.Element(f"{{{SVG_NS}}}g", {"id": f"{prefix}{definition_id}"})
        for child in list(definition_node):
            group.append(_clone_xml_element(child))
        if len(group):
            groups.append(group)
    return groups


def _make_unique_names(names: List[str]) -> List[str]:
    """Ensure layer/object names are unique while preserving order."""
    counts: Dict[str, int] = {}
    unique: List[str] = []
    for name in names:
        base = name.strip() or "Layer"
        idx = counts.get(base, 0)
        counts[base] = idx + 1
        unique.append(base if idx == 0 else f"{base}_{idx + 1}")
    return unique


def _normalize_path_data(path_data: Optional[str]) -> str:
    """Normalize path data string for signature matching."""
    if not path_data:
        return ""
    return re.sub(r"[\s,]+", " ", path_data).strip().lower()


def _geometry_signature(
    element: ET.Element,
    transform: Optional[Tuple[float, float, float, float, float, float]] = None,
) -> Optional[str]:
    """Build signature for matching clip geometry to visible geometry."""
    tag = _local_name(element.tag)
    transform_key = ""
    if transform is not None:
        transform_key = "|" + ",".join(f"{value:.6f}" for value in transform)
    if tag == "path":
        signature = _normalize_path_data(element.get("d"))
        return f"{signature}{transform_key}" if signature else None
    if tag in SUPPORTED_SHAPES:
        signature = _normalize_path_data(convert_shape_to_path(element))
        return f"{signature}{transform_key}" if signature else None
    return None


def _geometry_to_import_element(element: ET.Element) -> Optional[ET.Element]:
    """Return importable geometry element, converting primitive shapes to path."""
    tag = _local_name(element.tag)
    if tag == "path":
        path_element = _clone_xml_element(element)
        if "transform" in path_element.attrib:
            del path_element.attrib["transform"]
        return path_element
    if tag not in SUPPORTED_SHAPES:
        return None

    path_data = convert_shape_to_path(element)
    if not path_data:
        return None

    path_element = ET.Element(f"{{{SVG_NS}}}path", {"d": path_data})
    for attr in (
        "style",
        "fill",
        "stroke",
        "stroke-width",
        "stroke-linecap",
        "stroke-linejoin",
        "fill-rule",
        "fill-opacity",
        "opacity",
    ):
        value = element.get(attr)
        if value:
            path_element.set(attr, value)
    return path_element


def _path_bounds(path_data: Optional[str]) -> Optional[Tuple[float, float, float, float]]:
    """Approximate path bounds by walking SVG path commands."""
    if not path_data:
        return None
    tokens = re.findall(r"[MmLlHhVvCcSsQqTtAaZz]|[-+]?(?:\d*\.\d+|\d+)(?:[eE][-+]?\d+)?", path_data)
    if not tokens:
        return None

    index = 0
    command = ""
    x = 0.0
    y = 0.0
    start_x = 0.0
    start_y = 0.0
    points: List[Tuple[float, float]] = []

    def has_number(pos: int) -> bool:
        return pos < len(tokens) and re.match(r"[-+]?(?:\d*\.\d+|\d+)(?:[eE][-+]?\d+)?$", tokens[pos]) is not None

    def take_number() -> Optional[float]:
        nonlocal index
        if not has_number(index):
            return None
        value = float(tokens[index])
        index += 1
        return value

    while index < len(tokens):
        token = tokens[index]
        if re.match(r"[A-Za-z]$", token):
            command = token
            index += 1
        if not command:
            break

        cmd = command
        rel = cmd.islower()
        upper = cmd.upper()

        if upper == "Z":
            x, y = start_x, start_y
            points.append((x, y))
            continue

        if upper == "M":
            first = True
            while True:
                nx = take_number()
                ny = take_number()
                if nx is None or ny is None:
                    break
                if rel:
                    x += nx
                    y += ny
                else:
                    x, y = nx, ny
                points.append((x, y))
                if first:
                    start_x, start_y = x, y
                    first = False
            continue

        if upper == "L":
            while True:
                nx = take_number()
                ny = take_number()
                if nx is None or ny is None:
                    break
                if rel:
                    x += nx
                    y += ny
                else:
                    x, y = nx, ny
                points.append((x, y))
            continue

        if upper == "H":
            while True:
                nx = take_number()
                if nx is None:
                    break
                x = x + nx if rel else nx
                points.append((x, y))
            continue

        if upper == "V":
            while True:
                ny = take_number()
                if ny is None:
                    break
                y = y + ny if rel else ny
                points.append((x, y))
            continue

        if upper == "C":
            while True:
                vals: List[float] = []
                for _ in range(6):
                    number = take_number()
                    if number is None:
                        vals = []
                        break
                    vals.append(number)
                if not vals:
                    break
                x1, y1, x2, y2, ex, ey = vals
                if rel:
                    points.append((x + x1, y + y1))
                    points.append((x + x2, y + y2))
                    x += ex
                    y += ey
                else:
                    points.append((x1, y1))
                    points.append((x2, y2))
                    x, y = ex, ey
                points.append((x, y))
            continue

        if upper in {"S", "Q"}:
            while True:
                vals: List[float] = []
                for _ in range(4):
                    number = take_number()
                    if number is None:
                        vals = []
                        break
                    vals.append(number)
                if not vals:
                    break
                c1x, c1y, ex, ey = vals
                if rel:
                    points.append((x + c1x, y + c1y))
                    x += ex
                    y += ey
                else:
                    points.append((c1x, c1y))
                    x, y = ex, ey
                points.append((x, y))
            continue

        if upper == "T":
            while True:
                nx = take_number()
                ny = take_number()
                if nx is None or ny is None:
                    break
                if rel:
                    x += nx
                    y += ny
                else:
                    x, y = nx, ny
                points.append((x, y))
            continue

        if upper == "A":
            while True:
                vals: List[float] = []
                for _ in range(7):
                    number = take_number()
                    if number is None:
                        vals = []
                        break
                    vals.append(number)
                if not vals:
                    break
                rx, ry, _rot, _laf, _sf, ex, ey = vals
                if rel:
                    x += ex
                    y += ey
                else:
                    x, y = ex, ey
                # Include endpoint and radii extents as approximation.
                points.append((x, y))
                points.append((x + abs(rx), y + abs(ry)))
                points.append((x - abs(rx), y - abs(ry)))
            continue

        # Unsupported command token: break to avoid infinite loop.
        break

    if not points:
        return None
    xs = [point[0] for point in points]
    ys = [point[1] for point in points]
    return (min(xs), min(ys), max(xs), max(ys))


def _geometry_bounds(element: ET.Element) -> Optional[Tuple[float, float, float, float]]:
    """Get local-space bounding box for supported geometry."""
    tag = _local_name(element.tag)
    if tag == "path":
        return _path_bounds(element.get("d"))
    if tag == "rect":
        x = _parse_svg_number(element.get("x"), 0.0)
        y = _parse_svg_number(element.get("y"), 0.0)
        w = _parse_svg_number(element.get("width"), 0.0)
        h = _parse_svg_number(element.get("height"), 0.0)
        return (x, y, x + w, y + h)
    if tag == "circle":
        cx = _parse_svg_number(element.get("cx"), 0.0)
        cy = _parse_svg_number(element.get("cy"), 0.0)
        r = _parse_svg_number(element.get("r"), 0.0)
        return (cx - r, cy - r, cx + r, cy + r)
    if tag == "ellipse":
        cx = _parse_svg_number(element.get("cx"), 0.0)
        cy = _parse_svg_number(element.get("cy"), 0.0)
        rx = _parse_svg_number(element.get("rx"), 0.0)
        ry = _parse_svg_number(element.get("ry"), 0.0)
        return (cx - rx, cy - ry, cx + rx, cy + ry)
    if tag == "line":
        x1 = _parse_svg_number(element.get("x1"), 0.0)
        y1 = _parse_svg_number(element.get("y1"), 0.0)
        x2 = _parse_svg_number(element.get("x2"), 0.0)
        y2 = _parse_svg_number(element.get("y2"), 0.0)
        return (min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2))
    if tag in {"polyline", "polygon"}:
        points = (element.get("points") or "").strip().replace(",", " ")
        coords = points.split()
        if len(coords) < 2:
            return None
        pairs: List[Tuple[float, float]] = []
        for idx in range(0, len(coords) - 1, 2):
            try:
                pairs.append((float(coords[idx]), float(coords[idx + 1])))
            except ValueError:
                continue
        if not pairs:
            return None
        xs = [p[0] for p in pairs]
        ys = [p[1] for p in pairs]
        return (min(xs), min(ys), max(xs), max(ys))
    return None


def _transform_bounds(
    bounds: Tuple[float, float, float, float],
    matrix: Tuple[float, float, float, float, float, float],
) -> Tuple[float, float, float, float]:
    """Apply transform to bounds corners and return axis-aligned bounds."""
    min_x, min_y, max_x, max_y = bounds
    corners = [
        _matrix_apply_point(matrix, min_x, min_y),
        _matrix_apply_point(matrix, min_x, max_y),
        _matrix_apply_point(matrix, max_x, min_y),
        _matrix_apply_point(matrix, max_x, max_y),
    ]
    xs = [c[0] for c in corners]
    ys = [c[1] for c in corners]
    return (min(xs), min(ys), max(xs), max(ys))


def _union_bounds(
    left: Optional[Tuple[float, float, float, float]],
    right: Optional[Tuple[float, float, float, float]],
) -> Optional[Tuple[float, float, float, float]]:
    """Return union of two bounds tuples."""
    if left is None:
        return right
    if right is None:
        return left
    return (
        min(left[0], right[0]),
        min(left[1], right[1]),
        max(left[2], right[2]),
        max(left[3], right[3]),
    )


def _definition_geometry_bounds(
    root: ET.Element,
    base_transform: Tuple[float, float, float, float, float, float] = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0),
) -> Optional[Tuple[float, float, float, float]]:
    """Compute world-space bounds for geometry inside a definition subtree."""
    aggregate: Optional[Tuple[float, float, float, float]] = None

    def walk(
        node: ET.Element,
        inherited_transform: Tuple[float, float, float, float, float, float],
    ) -> None:
        nonlocal aggregate
        local = _parse_transform_attr(node.get("transform"))
        active = _matrix_multiply(inherited_transform, local)
        tag = _local_name(node.tag)
        if tag in SUPPORTED_GEOMETRY:
            local_bounds = _geometry_bounds(node)
            if local_bounds is not None:
                aggregate = _union_bounds(aggregate, _transform_bounds(local_bounds, active))
        for child in list(node):
            if isinstance(child.tag, str):
                walk(child, active)

    walk(root, base_transform)
    return aggregate


def _bounds_match_score(
    left: Optional[Tuple[float, float, float, float]],
    right: Optional[Tuple[float, float, float, float]],
) -> float:
    """Score bounds similarity in [0, 1], preferring near-equal shapes."""
    if left is None or right is None:
        return 0.0
    lx0, ly0, lx1, ly1 = left
    rx0, ry0, rx1, ry1 = right
    iw = max(0.0, min(lx1, rx1) - max(lx0, rx0))
    ih = max(0.0, min(ly1, ry1) - max(ly0, ry0))
    intersection = iw * ih
    if intersection <= 0.0:
        return 0.0
    left_area = max((lx1 - lx0) * (ly1 - ly0), 1e-9)
    right_area = max((rx1 - rx0) * (ry1 - ry0), 1e-9)
    union = max(left_area + right_area - intersection, 1e-9)
    return intersection / union


def _collect_geometry_signatures(
    root: ET.Element,
    base_transform: Tuple[float, float, float, float, float, float] = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0),
    include_transform: bool = True,
) -> Set[str]:
    """Collect geometry signatures from an XML subtree."""
    signatures: Set[str] = set()

    def walk(
        node: ET.Element,
        inherited_transform: Tuple[float, float, float, float, float, float],
    ) -> None:
        local = _parse_transform_attr(node.get("transform"))
        active = _matrix_multiply(inherited_transform, local)

        signature = _geometry_signature(node, active if include_transform else None)
        if signature:
            signatures.add(signature)

        for child in list(node):
            if isinstance(child.tag, str):
                walk(child, active)

    walk(root, base_transform)
    return signatures


def _collect_split_geometry_groups(layer: Dict[str, object]) -> List[Dict[str, object]]:
    """
    Build one temporary SVG group per geometry object.

    This enables one GP layer per SVG object when split mode is enabled, which
    is required for per-object mask/clip assignment.
    """
    layer_group = layer["element"]
    layer_name = str(layer["name"])
    split_items: List[Dict[str, object]] = []
    item_counter = 0
    anonymous_group_counter = 0

    def walk(
        node: ET.Element,
        inherited_masks: Set[str],
        inherited_clips: Set[str],
        inherited_style: Dict[str, str],
        inherited_transform: Tuple[float, float, float, float, float, float],
        inherited_group_path: Tuple[str, ...],
        is_root: bool,
        definition_context: bool = False,
    ) -> None:
        nonlocal item_counter, anonymous_group_counter

        node_masks, node_clips = _extract_mask_clip_refs(node)
        # GP layer masks should form a chain, so each geometry item only keeps the
        # nearest active mask/clip container instead of accumulating all ancestors.
        active_masks = set(node_masks) if node_masks else set(inherited_masks)
        active_clips = set(node_clips) if node_clips else set(inherited_clips)
        active_style = dict(inherited_style)
        active_style.update(_presentation_style(node))
        active_transform = _matrix_multiply(inherited_transform, _parse_transform_attr(node.get("transform")))

        tag = _local_name(node.tag)
        is_definition_container = definition_context or tag in {"clipPath", "mask", "defs", "symbol"}
        active_group_path = inherited_group_path
        if tag == "g" and not is_definition_container and not is_root:
            group_name = _group_name_from_node(node)
            if not group_name:
                anonymous_group_counter += 1
                group_name = f"Group_{anonymous_group_counter}"
            if group_name:
                active_group_path = inherited_group_path + (group_name,)

        if tag in SUPPORTED_GEOMETRY and not is_definition_container:
            geometry = _geometry_to_import_element(node)
            if geometry is not None:
                for style_key, style_value in active_style.items():
                    if style_value:
                        geometry.set(style_key, style_value)
                if "class" in geometry.attrib:
                    del geometry.attrib["class"]
                item_counter += 1
                raw_name = node.get("id") or f"{layer_name}_{item_counter}"
                item_group = ET.Element(f"{{{SVG_NS}}}g", {"id": raw_name})
                if not _is_identity_matrix(active_transform):
                    item_group.set("transform", _matrix_to_svg(active_transform))
                item_group.append(geometry)
                bounds = _geometry_bounds(node)
                world_bounds = _transform_bounds(bounds, active_transform) if bounds is not None else None
                split_items.append(
                    {
                        "name": raw_name,
                        "element": item_group,
                        "signature": _geometry_signature(node, active_transform),
                        "signature_raw": _geometry_signature(node),
                        "fill_spec": _resolve_fill_spec(active_style),
                        "stroke_spec": _resolve_stroke_spec(active_style),
                        "bounds": world_bounds,
                        "transform": active_transform,
                        "mask_refs": sorted(active_masks),
                        "clip_refs": sorted(active_clips),
                        "group_path": list(active_group_path) if active_group_path else [layer_name],
                    }
                )

        for child in list(node):
            if isinstance(child.tag, str):
                walk(
                    child,
                    active_masks,
                    active_clips,
                    active_style,
                    active_transform,
                    active_group_path,
                    False,
                    is_definition_container,
                )

    walk(layer_group, set(), set(), {}, _matrix_identity(), (layer_name,), True, False)
    return split_items


def _prepare_import_plan(root: ET.Element, split_layers: bool) -> Dict[str, object]:
    """Prepare import variants and mappings for operators."""
    metadata = _collect_svg_metadata(root)
    layers = [layer for layer in metadata["layers"] if layer["has_geometry"]]
    mask_defs = metadata["mask_defs"]
    clip_defs = metadata["clip_defs"]
    gradient_defs = metadata.get("gradient_defs", {})

    visible_groups: List[ET.Element] = []
    visible_names: List[str] = []
    mask_bindings: Dict[str, List[str]] = {}
    clip_bindings: Dict[str, List[str]] = {}
    mask_layer_bindings: Dict[str, List[str]] = {}
    clip_layer_bindings: Dict[str, List[str]] = {}
    layer_fill_specs: Dict[str, Dict[str, object]] = {}
    layer_stroke_specs: Dict[str, Dict[str, object]] = {}
    layer_bounds: Dict[str, Optional[Tuple[float, float, float, float]]] = {}
    layer_transforms: Dict[str, Tuple[float, float, float, float, float, float]] = {}
    layer_group_paths: Dict[str, List[str]] = {}
    split_curve_groups: List[Dict[str, object]] = []
    signature_to_layers: Dict[str, List[str]] = {}
    signature_to_layers_raw: Dict[str, List[str]] = {}

    if split_layers:
        object_items: List[Dict[str, object]] = []
        for layer in layers:
            object_items.extend(_collect_split_geometry_groups(layer))

        if object_items:
            unique_object_names = _make_unique_names([str(item["name"]) for item in object_items])
            for item, unique_name in zip(object_items, unique_object_names):
                group = _clone_xml_element(item["element"])
                group.set("id", unique_name)
                visible_groups.append(group)
                visible_names.append(unique_name)
                mask_bindings[unique_name] = list(item["mask_refs"])
                clip_bindings[unique_name] = list(item["clip_refs"])
                layer_fill_specs[unique_name] = dict(item.get("fill_spec") or {})
                layer_stroke_specs[unique_name] = dict(item.get("stroke_spec") or {})
                layer_bounds[unique_name] = item.get("bounds")
                layer_transforms[unique_name] = item.get("transform") or _matrix_identity()
                layer_group_paths[unique_name] = list(item.get("group_path") or [unique_name])
                split_curve_groups.append(
                    {
                        "name": unique_name,
                        "element": group,
                        "group_path": list(item.get("group_path") or [unique_name]),
                    }
                )
                signature = item.get("signature")
                if signature:
                    signature_to_layers.setdefault(str(signature), []).append(unique_name)
                signature_raw = item.get("signature_raw")
                if signature_raw:
                    signature_to_layers_raw.setdefault(str(signature_raw), []).append(unique_name)
        else:
            unique_layer_names = _make_unique_names([str(layer["name"]) for layer in layers])
            for layer, unique_name in zip(layers, unique_layer_names):
                group = _clone_xml_element(layer["element"])
                group.set("id", unique_name)
                visible_groups.append(group)
                visible_names.append(unique_name)
                mask_bindings[unique_name] = list(layer["mask_refs"])
                clip_bindings[unique_name] = list(layer["clip_refs"])
                layer_fill_specs[unique_name] = {}
                layer_stroke_specs[unique_name] = {}
                layer_bounds[unique_name] = None
                layer_transforms[unique_name] = _matrix_identity()
                layer_group_paths[unique_name] = [unique_name]
                split_curve_groups.append({"name": unique_name, "element": group, "group_path": [unique_name]})
    else:
        flat_group = _flatten_layers_to_group(layers, name="Layer_1")
        if flat_group is not None:
            visible_groups.append(flat_group)
            visible_names.append("Layer_1")
            all_mask_refs = sorted({ref for layer in layers for ref in layer["mask_refs"]})
            all_clip_refs = sorted({ref for layer in layers for ref in layer["clip_refs"]})
            mask_bindings["Layer_1"] = all_mask_refs
            clip_bindings["Layer_1"] = all_clip_refs
            layer_fill_specs["Layer_1"] = {}
            layer_stroke_specs["Layer_1"] = {}
            layer_bounds["Layer_1"] = None
            layer_transforms["Layer_1"] = _matrix_identity()
            layer_group_paths["Layer_1"] = ["Layer_1"]

    def resolve_ref_bindings(
        refs_by_layer: Dict[str, List[str]],
        defs_by_id: Dict[str, ET.Element],
        allow_bounds_fallback: bool = True,
    ) -> Tuple[Dict[str, List[str]], Dict[str, List[str]]]:
        """Resolve refs to visible layers when definitions match imported geometry."""
        resolved_layer_bindings: Dict[str, List[str]] = {}
        for target_layer_name, refs in list(refs_by_layer.items()):
            unresolved_refs: List[str] = []
            target_transform = layer_transforms.get(target_layer_name, _matrix_identity())
            for definition_ref in refs:
                definition_node = defs_by_id.get(definition_ref)
                if definition_node is None:
                    unresolved_refs.append(definition_ref)
                    continue

                transformed_matches: List[str] = []
                signatures = _collect_geometry_signatures(definition_node, target_transform)
                for signature in signatures:
                    transformed_matches.extend(signature_to_layers.get(signature, []))

                raw_matches: List[str] = []
                if not transformed_matches or target_layer_name in transformed_matches:
                    raw_signatures = _collect_geometry_signatures(definition_node, include_transform=False)
                    for signature in raw_signatures:
                        raw_matches.extend(signature_to_layers_raw.get(signature, []))

                matched_all = list(dict.fromkeys(transformed_matches + raw_matches))
                matched_non_self = [name for name in matched_all if name != target_layer_name]
                if allow_bounds_fallback and not matched_non_self:
                    # Fallback for editor exports with tiny geometric deltas
                    # (e.g. viewport clip rect dimensions rounded differently).
                    definition_bounds = _definition_geometry_bounds(definition_node, target_transform)
                    scored_matches: List[Tuple[float, str]] = []
                    for candidate_name, candidate_bounds in layer_bounds.items():
                        score = _bounds_match_score(definition_bounds, candidate_bounds)
                        if score > 0.98:
                            scored_matches.append((score, candidate_name))
                    scored_matches.sort(reverse=True)
                    bounds_matches = [name for _, name in scored_matches]
                    matched_all = list(dict.fromkeys(matched_all + bounds_matches))
                    matched_non_self = [name for name in matched_all if name != target_layer_name]

                if matched_non_self:
                    existing = resolved_layer_bindings.setdefault(target_layer_name, [])
                    for matched_layer in matched_non_self:
                        if matched_layer not in existing:
                            existing.append(matched_layer)
                elif not matched_all:
                    unresolved_refs.append(definition_ref)
                # self-only matches count as resolved and don't require helper layers.

            refs_by_layer[target_layer_name] = unresolved_refs
        return refs_by_layer, resolved_layer_bindings

    if split_layers and signature_to_layers:
        clip_bindings, clip_layer_bindings = resolve_ref_bindings(clip_bindings, clip_defs, allow_bounds_fallback=False)
        mask_bindings, mask_layer_bindings = resolve_ref_bindings(mask_bindings, mask_defs, allow_bounds_fallback=True)

    if not split_layers:
        # Single-layer GP import should not emit extra hidden helper layers.
        mask_bindings = {name: [] for name in visible_names}
        clip_bindings = {name: [] for name in visible_names}
        mask_layer_bindings = {}
        clip_layer_bindings = {}

    referenced_mask_ids = {ref for refs in mask_bindings.values() for ref in refs}
    referenced_clip_ids = {ref for refs in clip_bindings.values() for ref in refs}
    mask_groups = _build_definition_groups(
        {definition_id: mask_defs[definition_id] for definition_id in referenced_mask_ids if definition_id in mask_defs},
        MASK_LAYER_PREFIX,
    )
    clip_groups = _build_definition_groups(
        {definition_id: clip_defs[definition_id] for definition_id in referenced_clip_ids if definition_id in clip_defs},
        CLIP_LAYER_PREFIX,
    )
    mask_names = [group.get("id", "") for group in mask_groups]
    clip_names = [group.get("id", "") for group in clip_groups]

    gp_visible_groups = []
    for group, visible_name in zip(visible_groups, visible_names):
        group_copy = _clone_xml_element(group)
        if split_layers:
            keep_mask_refs = set(mask_bindings.get(visible_name, []))
            keep_clip_refs = set(clip_bindings.get(visible_name, []))
            _strip_mask_clip_references(group_copy, keep_mask_refs, keep_clip_refs)
        gp_visible_groups.append(group_copy)

    gp_groups = list(gp_visible_groups)
    gp_groups.extend(mask_groups)
    gp_groups.extend(clip_groups)
    gp_svg = _build_svg_document(root, gp_groups) if gp_groups else None

    curve_svg = _build_svg_document(root, visible_groups) if visible_groups else None

    return {
        "metadata": metadata,
        "visible_names": visible_names,
        "mask_names": mask_names,
        "clip_names": clip_names,
        "mask_bindings": mask_bindings,
        "clip_bindings": clip_bindings,
        "mask_layer_bindings": mask_layer_bindings,
        "clip_layer_bindings": clip_layer_bindings,
        "layer_fill_specs": layer_fill_specs,
        "layer_stroke_specs": layer_stroke_specs,
        "layer_bounds": layer_bounds,
        "layer_transforms": layer_transforms,
        "layer_group_paths": layer_group_paths,
        "gradient_defs": gradient_defs,
        "split_curve_groups": split_curve_groups,
        "gp_svg": gp_svg,
        "curve_svg": curve_svg,
        "split_layers": split_layers,
    }


def _write_temp_svg(svg_data: str) -> str:
    """Write SVG content to temporary file and return path."""
    temp = tempfile.NamedTemporaryFile(delete=False, suffix=".svg", mode="w", encoding="utf-8")
    temp.write(svg_data)
    temp.close()
    return temp.name


def _scene_object_pointers(scene: bpy.types.Scene) -> Set[int]:
    """Return current object pointer ids in scene."""
    return {obj.as_pointer() for obj in scene.objects}


def _new_scene_objects(scene: bpy.types.Scene, before: Set[int]) -> List[bpy.types.Object]:
    """Return objects added to scene since snapshot."""
    return [obj for obj in scene.objects if obj.as_pointer() not in before]


def _walk_scene_collections(root: bpy.types.Collection) -> List[bpy.types.Collection]:
    """Return all collections reachable from a root collection."""
    result: List[bpy.types.Collection] = [root]
    for child in getattr(root, "children", []):
        result.extend(_walk_scene_collections(child))
    return result


def _scene_collection_pointers(scene: bpy.types.Scene) -> Set[int]:
    """Return current collection pointer ids reachable from the scene root."""
    root = getattr(scene, "collection", None)
    if root is None:
        return set()
    return {collection.as_pointer() for collection in _walk_scene_collections(root)}


def _new_scene_collections(scene: bpy.types.Scene, before: Set[int]) -> List[bpy.types.Collection]:
    """Return collections added under the scene root since snapshot."""
    root = getattr(scene, "collection", None)
    if root is None:
        return []
    return [collection for collection in _walk_scene_collections(root) if collection.as_pointer() not in before]


def _ensure_named_collection(scene: bpy.types.Scene, name: str) -> bpy.types.Collection:
    """Create a new root-level collection for pasted content."""
    root = scene.collection
    collection = bpy.data.collections.new(name)
    root.children.link(collection)
    return collection


def _unlink_collection_from_tree(parent: bpy.types.Collection, target: bpy.types.Collection) -> bool:
    """Unlink a collection from a parent tree if present."""
    children = getattr(parent, "children", None)
    if children is None:
        return False
    if target in children:
        children.unlink(target)
        return True
    for child in list(children):
        if _unlink_collection_from_tree(child, target):
            return True
    return False


def _move_objects_to_collection(
    scene: bpy.types.Scene,
    objects: List[bpy.types.Object],
    collection_name: str,
    cleanup_collections: Optional[List[bpy.types.Collection]] = None,
) -> Optional[bpy.types.Collection]:
    """Move imported objects into one named collection and clean empty import collections."""
    valid = [obj for obj in objects if obj is not None]
    if not valid:
        return None

    target = _ensure_named_collection(scene, collection_name)
    cleanup_targets = list(cleanup_collections or [])

    for obj in valid:
        already_linked = False
        try:
            already_linked = any(existing == obj for existing in target.objects)
        except Exception:
            already_linked = False
        if not already_linked:
            try:
                target.objects.link(obj)
            except Exception:
                pass
        previous_collections = [collection for collection in obj.users_collection if collection != target]
        for collection in previous_collections:
            try:
                collection.objects.unlink(obj)
            except Exception:
                continue
            if collection not in cleanup_targets:
                cleanup_targets.append(collection)

    for collection in cleanup_targets:
        if collection == target or collection == scene.collection:
            continue
        try:
            has_objects = len(collection.objects) > 0
            has_children = len(collection.children) > 0
        except Exception:
            continue
        if has_objects or has_children:
            continue
        try:
            _unlink_collection_from_tree(scene.collection, collection)
        except Exception:
            pass
        try:
            bpy.data.collections.remove(collection)
        except Exception:
            pass

    return target


def _operator_supported_kwargs(operator_handle: object, kwargs: Dict[str, object]) -> Dict[str, object]:
    """Filter kwargs to the subset supported by an operator RNA definition."""
    try:
        rna = operator_handle.get_rna_type()
        supported = {prop.identifier for prop in rna.properties}
    except Exception:
        return kwargs
    return {key: value for key, value in kwargs.items() if key in supported}


def import_svg_via_tempfile(
    context: bpy.types.Context,
    svg_data: str,
    import_mode: str,
    gp_split_layers: bool = False,
) -> List[bpy.types.Object]:
    """
    Import SVG data using Blender's built-in operators.

    import_mode:
    - "GP" for Grease Pencil (`wm.grease_pencil_import_svg`)
    - "CURVE" for curve import (`import_curve.svg`)
    """
    tmp_path = None
    try:
        tmp_path = _write_temp_svg(svg_data)
        before = _scene_object_pointers(context.scene)

        if import_mode == "GP":
            if not hasattr(bpy.ops.wm, "grease_pencil_import_svg"):
                raise RuntimeError("Grease Pencil SVG importer is unavailable.")
            gp_options = _operator_supported_kwargs(
                bpy.ops.wm.grease_pencil_import_svg,
                {
                    "filepath": tmp_path,
                    "split_layers": gp_split_layers,
                    "separate_layers": gp_split_layers,
                    "separate_layer": gp_split_layers,
                    "use_svg_layers": gp_split_layers,
                    "group_to_layer": gp_split_layers,
                    "import_as_layers": gp_split_layers,
                },
            )
            if "filepath" not in gp_options:
                gp_options["filepath"] = tmp_path
            result = bpy.ops.wm.grease_pencil_import_svg(**gp_options)
        elif import_mode == "CURVE":
            if not hasattr(bpy.ops.import_curve, "svg"):
                raise RuntimeError("Curve SVG importer is unavailable.")
            result = bpy.ops.import_curve.svg(filepath=tmp_path)
        else:
            raise RuntimeError(f"Unsupported import mode: {import_mode}")

        if "CANCELLED" in result:
            raise RuntimeError("Blender SVG import operator cancelled.")
        return _new_scene_objects(context.scene, before)
    finally:
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:
                pass


def is_valid_gp_object(obj: Optional[bpy.types.Object]) -> bool:
    """Check if object is a valid Grease Pencil v3 object with layers."""
    return bool(
        obj
        and obj.type == GP_TYPE
        and hasattr(obj.data, "layers")
        and len(obj.data.layers) > 0
    )


def _rename_gp_layers(gp_obj: bpy.types.Object, ordered_names: List[str]) -> None:
    """Rename GP layers by index to deterministic names."""
    if not is_valid_gp_object(gp_obj):
        return
    for index, layer in enumerate(gp_obj.data.layers):
        if index >= len(ordered_names):
            break
        try:
            layer.name = ordered_names[index]
        except Exception:
            pass


def _find_gp_layer(gp_obj: bpy.types.Object, name: str) -> Optional[object]:
    """Find GP layer by name."""
    if not is_valid_gp_object(gp_obj):
        return None
    for layer in gp_obj.data.layers:
        if layer.name == name:
            return layer
    return None


def _set_gp_layer_hidden(layer: object) -> None:
    """Hide layer if API exposes hide controls."""
    for attr in ("hide", "hide_render"):
        if hasattr(layer, attr):
            try:
                setattr(layer, attr, True)
            except Exception:
                pass


def _set_gp_layer_holdout(layer: object) -> bool:
    """Mark layer as holdout when available in current Blender API."""
    holdout_attrs = ("use_holdout", "holdout", "is_holdout")
    for attr in holdout_attrs:
        if hasattr(layer, attr):
            try:
                setattr(layer, attr, True)
                return True
            except Exception:
                pass

    if hasattr(layer, "blend_mode"):
        try:
            layer.blend_mode = "HOLDOUT"
            return True
        except Exception:
            return False
    return False


def _set_gp_layer_unlit(layer: object) -> None:
    """Disable GP layer lighting so imported SVG colors remain flat."""
    for attr in ("use_lights", "lights"):
        if hasattr(layer, attr):
            try:
                setattr(layer, attr, False)
            except Exception:
                pass


def _add_gp_layer_mask(gp_obj: bpy.types.Object, target_layer: object, mask_layer: object) -> bool:
    """Attach a GP layer mask relation when supported by API."""
    if not hasattr(target_layer, "mask_layers"):
        return False

    mask_name = getattr(mask_layer, "name", "")
    if not mask_name or getattr(target_layer, "name", "") == mask_name:
        return False

    def has_mask_binding() -> bool:
        try:
            for entry in target_layer.mask_layers:
                existing_name = getattr(entry, "name", "")
                linked_layer = getattr(entry, "layer", None)
                linked_name = getattr(linked_layer, "name", "") if linked_layer else ""
                if existing_name == mask_name or linked_name == mask_name:
                    return True
        except Exception:
            return False
        return False

    if has_mask_binding():
        return True

    for attr in ("use_masks", "use_mask", "use_mask_layer"):
        if hasattr(target_layer, attr):
            try:
                setattr(target_layer, attr, True)
            except Exception:
                pass

    mask_layers = target_layer.mask_layers
    entry = None

    def _last_mask_entry() -> Optional[object]:
        try:
            return mask_layers[-1] if len(mask_layers) > 0 else None
        except Exception:
            return None

    def _try_add_mask_entry() -> Optional[object]:
        add_calls = []
        if hasattr(mask_layers, "add"):
            add_calls.extend(
                [
                    lambda: mask_layers.add(mask_layer),
                    lambda: mask_layers.add(mask_name),
                    lambda: mask_layers.add(name=mask_name),
                    lambda: mask_layers.add(),
                ]
            )
        if hasattr(mask_layers, "new"):
            add_calls.extend(
                [
                    lambda: mask_layers.new(mask_name),
                    lambda: mask_layers.new(),
                ]
            )

        for add_call in add_calls:
            try:
                before_len = len(mask_layers)
            except Exception:
                before_len = -1
            try:
                created = add_call()
            except Exception:
                continue

            if created is not None:
                return created

            try:
                after_len = len(mask_layers)
            except Exception:
                after_len = before_len
            if after_len > before_len:
                newest = _last_mask_entry()
                if newest is not None:
                    return newest
        return None

    entry = _try_add_mask_entry()

    if entry is not None and hasattr(entry, "name"):
        try:
            entry.name = mask_name
        except Exception:
            pass
    if entry is not None and hasattr(entry, "layer"):
        try:
            entry.layer = mask_layer
        except Exception:
            pass

    if has_mask_binding():
        return True

    # Fallback for Blender builds where direct mask_layers mutation is not enough.
    layer_collection = getattr(gp_obj.data, "layers", None)
    previous_active_layer_index = None
    if layer_collection and hasattr(layer_collection, "active"):
        try:
            layer_collection.active = target_layer
        except Exception:
            pass
    if layer_collection and hasattr(layer_collection, "active_index"):
        try:
            previous_active_layer_index = int(layer_collection.active_index)
            target_index = next(
                (idx for idx, layer in enumerate(layer_collection) if getattr(layer, "name", "") == getattr(target_layer, "name", "")),
                -1,
            )
            if target_index >= 0:
                layer_collection.active_index = target_index
        except Exception:
            pass

    op = None
    if hasattr(bpy.ops, "grease_pencil") and hasattr(bpy.ops.grease_pencil, "layer_mask_add"):
        op = bpy.ops.grease_pencil.layer_mask_add
    elif hasattr(bpy.ops, "gpencil") and hasattr(bpy.ops.gpencil, "layer_mask_add"):
        op = bpy.ops.gpencil.layer_mask_add

    if op is not None:
        previous_selection = []
        previous_active_object = None
        try:
            previous_selection = list(bpy.context.selected_objects)
        except Exception:
            previous_selection = []
        try:
            previous_active_object = bpy.context.view_layer.objects.active
        except Exception:
            previous_active_object = None

        for obj in previous_selection:
            try:
                obj.select_set(False)
            except Exception:
                pass
        try:
            gp_obj.select_set(True)
        except Exception:
            pass
        try:
            bpy.context.view_layer.objects.active = gp_obj
        except Exception:
            pass

        kwargs = _operator_supported_kwargs(
            op,
            {
                "name": mask_name,
                "layer": mask_name,
                "mask_name": mask_name,
            },
        )
        try:
            op(**kwargs)
        except Exception:
            pass

        try:
            gp_obj.select_set(False)
        except Exception:
            pass
        for obj in previous_selection:
            try:
                obj.select_set(True)
            except Exception:
                pass
        if previous_active_object is not None:
            try:
                bpy.context.view_layer.objects.active = previous_active_object
            except Exception:
                pass

    if layer_collection and hasattr(layer_collection, "active_index") and previous_active_layer_index is not None:
        try:
            layer_collection.active_index = previous_active_layer_index
        except Exception:
            pass

    return has_mask_binding()


def _apply_gp_masks_and_holdout(gp_obj: bpy.types.Object, plan: Dict[str, object]) -> None:
    """Apply mask and clip-path mappings to imported GP layers."""
    if not is_valid_gp_object(gp_obj):
        return

    try:
        bpy.context.view_layer.objects.active = gp_obj
    except Exception:
        pass

    for attr in ("use_grease_pencil_lights", "use_lights"):
        if hasattr(gp_obj, attr):
            try:
                setattr(gp_obj, attr, False)
            except Exception:
                pass

    visible_names = plan["visible_names"]
    mask_names = plan["mask_names"]
    clip_names = plan["clip_names"]
    ordered_names = visible_names + mask_names + clip_names
    existing_names = {getattr(layer, "name", "") for layer in gp_obj.data.layers}
    expected_names = {name for name in ordered_names if name}
    if expected_names and not expected_names.issubset(existing_names):
        _rename_gp_layers(gp_obj, ordered_names)

    if plan.get("create_gp_groups"):
        _apply_gp_layer_groups(gp_obj, plan)

    for name in mask_names:
        layer = _find_gp_layer(gp_obj, name)
        if layer:
            _set_gp_layer_hidden(layer)
            _set_gp_layer_unlit(layer)
    for name in clip_names:
        layer = _find_gp_layer(gp_obj, name)
        if layer:
            _set_gp_layer_hidden(layer)
            _set_gp_layer_holdout(layer)
            _set_gp_layer_unlit(layer)

    for name in visible_names:
        layer = _find_gp_layer(gp_obj, name)
        if layer:
            _set_gp_layer_unlit(layer)

    mask_bindings = plan["mask_bindings"]
    clip_bindings = plan["clip_bindings"]
    mask_layer_bindings = plan.get("mask_layer_bindings", {})
    clip_layer_bindings = plan.get("clip_layer_bindings", {})

    for target_name, refs in mask_bindings.items():
        target_layer = _find_gp_layer(gp_obj, target_name)
        if not target_layer:
            continue
        for definition_id in refs:
            mask_layer_name = f"{MASK_LAYER_PREFIX}{definition_id}"
            mask_layer = _find_gp_layer(gp_obj, mask_layer_name)
            if mask_layer:
                _add_gp_layer_mask(gp_obj, target_layer, mask_layer)

    for target_name, layer_names in mask_layer_bindings.items():
        target_layer = _find_gp_layer(gp_obj, target_name)
        if not target_layer:
            continue
        for layer_name in layer_names:
            mask_layer = _find_gp_layer(gp_obj, layer_name)
            if mask_layer:
                _add_gp_layer_mask(gp_obj, target_layer, mask_layer)

    for target_name, refs in clip_bindings.items():
        target_layer = _find_gp_layer(gp_obj, target_name)
        if not target_layer:
            continue
        for definition_id in refs:
            clip_layer_name = f"{CLIP_LAYER_PREFIX}{definition_id}"
            clip_layer = _find_gp_layer(gp_obj, clip_layer_name)
            if clip_layer:
                _add_gp_layer_mask(gp_obj, target_layer, clip_layer)

    for target_name, layer_names in clip_layer_bindings.items():
        target_layer = _find_gp_layer(gp_obj, target_name)
        if not target_layer:
            continue
        for layer_name in layer_names:
            clip_layer = _find_gp_layer(gp_obj, layer_name)
            if clip_layer:
                _add_gp_layer_mask(gp_obj, target_layer, clip_layer)


def _apply_gp_layer_groups(gp_obj: bpy.types.Object, plan: Dict[str, object]) -> None:
    """Create GP layer groups from SVG group paths when the API is available."""
    if not is_valid_gp_object(gp_obj):
        return

    group_paths = plan.get("layer_group_paths") or {}
    if not isinstance(group_paths, dict):
        return

    gp_data = getattr(gp_obj, "data", None)
    if gp_data is None:
        return

    layer_groups = getattr(gp_data, "layer_groups", None)
    layers_api = getattr(gp_data, "layers", None)
    if layer_groups is None or layers_api is None:
        return
    if not hasattr(layer_groups, "new") or not hasattr(layers_api, "move_to_layer_group"):
        return

    created_groups: Dict[Tuple[str, ...], object] = {}
    for layer_name, raw_path in group_paths.items():
        layer = _find_gp_layer(gp_obj, layer_name)
        if layer is None:
            continue

        path = tuple(segment for segment in raw_path if isinstance(segment, str) and segment.strip())
        if not path:
            continue

        parent_group = None
        current_path: Tuple[str, ...] = tuple()
        for segment in path:
            current_path = current_path + (segment,)
            current_group = created_groups.get(current_path)
            if current_group is None:
                try:
                    if parent_group is None:
                        current_group = layer_groups.new(segment)
                    else:
                        current_group = layer_groups.new(segment, parent_group=parent_group)
                except Exception:
                    current_group = None
                if current_group is None:
                    break
                created_groups[current_path] = current_group
                for attr_name in ("is_expanded", "expanded"):
                    if hasattr(current_group, attr_name):
                        try:
                            setattr(current_group, attr_name, False)
                        except Exception:
                            pass
            parent_group = current_group

        if parent_group is None:
            continue

        try:
            layers_api.move_to_layer_group(layer, parent_group)
        except Exception:
            continue


def _ensure_gp_material_data(material: bpy.types.Material) -> object:
    """Ensure material has Grease Pencil data and return style handle."""
    if hasattr(bpy.data.materials, "create_gpencil_data"):
        try:
            bpy.data.materials.create_gpencil_data(material)
        except Exception:
            pass
    return getattr(material, "grease_pencil", None)


def _set_vector_property(target: object, prop_name: str, x: float, y: float, z: float = 0.0) -> None:
    """Set vector-like property if available."""
    if not hasattr(target, prop_name):
        return
    try:
        vector = getattr(target, prop_name)
        if hasattr(vector, "__len__"):
            if len(vector) >= 3:
                setattr(target, prop_name, (x, y, z))
            elif len(vector) == 2:
                setattr(target, prop_name, (x, y))
            else:
                setattr(target, prop_name, (x,))
        else:
            setattr(target, prop_name, (x, y, z))
    except Exception:
        pass


def _set_enum_property(target: object, prop_name: str, value: str) -> bool:
    """Best-effort enum assignment when property exists and accepts the value."""
    if target is None or not hasattr(target, prop_name):
        return False
    try:
        setattr(target, prop_name, value)
        return True
    except Exception:
        pass

    try:
        bl_rna = getattr(target, "bl_rna", None)
        if bl_rna is None:
            return False
        prop = bl_rna.properties.get(prop_name)
        if prop is None or not hasattr(prop, "enum_items"):
            return False
        valid = {item.identifier for item in prop.enum_items}
        if value in valid:
            setattr(target, prop_name, value)
            return True
    except Exception:
        return False
    return False


def _ensure_gp_draw_uses_material_color(context: bpy.types.Context) -> None:
    """Try to keep Draw mode color interpretation on material colors."""
    scene = getattr(context, "scene", None)
    tool_settings = getattr(scene, "tool_settings", None) if scene else None
    if tool_settings is None:
        return

    for paint_attr in ("gpencil_paint", "grease_pencil_paint"):
        paint = getattr(tool_settings, paint_attr, None)
        if paint is None:
            continue
        _set_enum_property(paint, "color_mode", "MATERIAL")

        brush = getattr(paint, "brush", None)
        if brush is None:
            continue
        for settings_attr in ("gpencil_settings", "grease_pencil_settings"):
            gp_settings = getattr(brush, settings_attr, None)
            if gp_settings is None:
                continue
            _set_enum_property(gp_settings, "brush_draw_mode", "MATERIAL")
            _set_enum_property(gp_settings, "color_mode", "MATERIAL")


def _gradient_world_endpoints(
    gradient: Dict[str, object],
    bounds: Optional[Tuple[float, float, float, float]],
    layer_transform: Tuple[float, float, float, float, float, float],
) -> Tuple[Tuple[float, float], Tuple[float, float]]:
    """Resolve gradient start/end points from SVG definitions into GP import space."""
    units = str(gradient.get("units", "objectBoundingBox")).strip().lower()
    is_bbox_units = units != "userspaceonuse"

    x1_raw = gradient.get("x1") if isinstance(gradient.get("x1"), str) else None
    y1_raw = gradient.get("y1") if isinstance(gradient.get("y1"), str) else None
    x2_raw = gradient.get("x2") if isinstance(gradient.get("x2"), str) else None
    y2_raw = gradient.get("y2") if isinstance(gradient.get("y2"), str) else None

    x1, x1_pct = _parse_gradient_coord(x1_raw, 0.0)
    y1, y1_pct = _parse_gradient_coord(y1_raw, 0.0)
    x2, x2_pct = _parse_gradient_coord(x2_raw, 1.0)
    y2, y2_pct = _parse_gradient_coord(y2_raw, 0.0)

    if is_bbox_units and bounds is not None:
        min_x, min_y, max_x, max_y = bounds
        width = max(max_x - min_x, 1e-6)
        height = max(max_y - min_y, 1e-6)
        start = (min_x + x1 * width, min_y + y1 * height)
        end = (min_x + x2 * width, min_y + y2 * height)
    else:
        if bounds is not None:
            min_x, min_y, max_x, max_y = bounds
            width = max(max_x - min_x, 1e-6)
            height = max(max_y - min_y, 1e-6)
            start = (
                min_x + x1 * width if x1_pct else x1,
                min_y + y1 * height if y1_pct else y1,
            )
            end = (
                min_x + x2 * width if x2_pct else x2,
                min_y + y2 * height if y2_pct else y2,
            )
        else:
            start = (x1, y1)
            end = (x2, y2)

    matrix = gradient.get("matrix")
    if isinstance(matrix, tuple) and len(matrix) == 6:
        start = _matrix_apply_point(matrix, start[0], start[1])
        end = _matrix_apply_point(matrix, end[0], end[1])

    if not is_bbox_units:
        start = _matrix_apply_point(layer_transform, start[0], start[1])
        end = _matrix_apply_point(layer_transform, end[0], end[1])

    return start, end


def _normalized_bounds_point(
    point: Tuple[float, float],
    bounds: Optional[Tuple[float, float, float, float]],
) -> Tuple[float, float]:
    """Map world point into normalized GP texture coordinates around bounds center."""
    if bounds is None:
        return (point[0], -point[1])

    min_x, min_y, max_x, max_y = bounds
    width = max(max_x - min_x, 1e-6)
    height = max(max_y - min_y, 1e-6)
    center_x = (min_x + max_x) * 0.5
    center_y = (min_y + max_y) * 0.5
    return ((point[0] - center_x) / width, -(point[1] - center_y) / height)


def _gradient_mapping_from_endpoints(
    start: Tuple[float, float],
    end: Tuple[float, float],
    bounds: Optional[Tuple[float, float, float, float]],
) -> Tuple[Tuple[float, float], float, Tuple[float, float]]:
    """Convert SVG gradient line to GP texture offset, angle, and scale."""
    start_n = _normalized_bounds_point(start, bounds)
    end_n = _normalized_bounds_point(end, bounds)
    vec_x = end_n[0] - start_n[0]
    vec_y = end_n[1] - start_n[1]
    length = max(math.hypot(vec_x, vec_y), 1e-6)
    # GP fill mapping behaves closer to a bottom-left anchored texture space.
    # Shift midpoint into that space for more faithful SVG positioning.
    mid_x = (start_n[0] + end_n[0]) * 0.5
    mid_y = (start_n[1] + end_n[1]) * 0.5
    location = (mid_x - 0.375, mid_y - 0.5)
    angle = float(math.atan2(vec_y, vec_x))
    # GP uses inverse-like scale behavior for gradient spread.
    scale = (0.44 / length, 1.0)
    return location, angle, scale


def _apply_style_to_gp_material(
    material: bpy.types.Material,
    fill_spec: Dict[str, object],
    stroke_spec: Dict[str, object],
    gradient_defs: Dict[str, Dict[str, object]],
    bounds: Optional[Tuple[float, float, float, float]],
    layer_transform: Tuple[float, float, float, float, float, float],
) -> None:
    """Apply parsed SVG fill + stroke style to Grease Pencil material."""
    style = _ensure_gp_material_data(material)
    if style is None:
        return

    if hasattr(material, "use_nodes"):
        try:
            material.use_nodes = False
        except Exception:
            pass

    fill_kind = str(fill_spec.get("kind", "SOLID"))
    stroke_kind = str(stroke_spec.get("kind", "NONE"))

    for flag_name, flag_value in (("show_fill", fill_kind != "NONE"), ("show_stroke", stroke_kind != "NONE")):
        if hasattr(style, flag_name):
            try:
                setattr(style, flag_name, flag_value)
            except Exception:
                pass

    solid_color = fill_spec.get("color") or (0.0, 0.0, 0.0, 1.0)
    if fill_kind == "SOLID":
        if hasattr(style, "fill_style"):
            try:
                style.fill_style = "SOLID"
            except Exception:
                pass
        for prop in ("fill_color", "base_color"):
            if hasattr(style, prop):
                try:
                    setattr(style, prop, solid_color)
                except Exception:
                    pass
        if hasattr(style, "mix_factor"):
            try:
                style.mix_factor = 0.0
            except Exception:
                pass

    if fill_kind == "GRADIENT":
        gradient_id = str(fill_spec.get("gradient_id", ""))
        gradient = gradient_defs.get(gradient_id, {})
        fill_opacity = _clamp01(float(fill_spec.get("opacity", 1.0)))
        first_raw = gradient.get("first_color", (1.0, 1.0, 1.0, 1.0))
        second_raw = gradient.get("second_color", (0.0, 0.0, 0.0, 1.0))
        first_color = (
            float(first_raw[0]),
            float(first_raw[1]),
            float(first_raw[2]),
            _clamp01(float(first_raw[3]) * fill_opacity),
        )
        second_color = (
            float(second_raw[0]),
            float(second_raw[1]),
            float(second_raw[2]),
            _clamp01(float(second_raw[3]) * fill_opacity),
        )

        if hasattr(style, "fill_style"):
            try:
                style.fill_style = "GRADIENT"
            except Exception:
                pass

        for prop in ("gradient_type", "fill_gradient_type"):
            if hasattr(style, prop):
                try:
                    setattr(style, prop, "LINEAR" if gradient.get("type") == "LINEAR" else "RADIAL")
                except Exception:
                    pass

        for prop in ("fill_color", "base_color"):
            if hasattr(style, prop):
                try:
                    setattr(style, prop, first_color)
                except Exception:
                    pass
        for prop in ("mix_color", "fill_color2", "secondary_color"):
            if hasattr(style, prop):
                try:
                    setattr(style, prop, second_color)
                    break
                except Exception:
                    pass

        if hasattr(style, "mix_factor"):
            try:
                # SVG gradients visually map closer around the mid blend range in GP.
                style.mix_factor = 0.5
            except Exception:
                pass

        start, end = _gradient_world_endpoints(gradient, bounds, layer_transform)
        location, angle, scale = _gradient_mapping_from_endpoints(start, end, bounds)

        # Convert from SVG-derived normalized space into GP material control space.
        # This aligns imported values with what users get from Blender's gradient tool.
        loc_x = location[0] - 0.1
        loc_y = -location[1] + 0.302
        gp_angle = angle + (math.pi * 0.5)
        gp_scale_x = 1.0

        _set_vector_property(style, "texture_offset", loc_x, loc_y, 0.0)
        _set_vector_property(style, "texture_scale", gp_scale_x, scale[1], 1.0)
        if hasattr(style, "texture_angle"):
            try:
                style.texture_angle = gp_angle
            except Exception:
                pass

    if fill_kind == "NONE" and hasattr(style, "show_fill"):
        try:
            style.show_fill = False
        except Exception:
            pass

    if stroke_kind == "SOLID":
        stroke_color = stroke_spec.get("color") or (0.0, 0.0, 0.0, 1.0)
        for prop in ("color", "stroke_color", "line_color"):
            if hasattr(style, prop):
                try:
                    setattr(style, prop, stroke_color)
                    break
                except Exception:
                    pass

        for prop in ("stroke_style", "line_style"):
            if hasattr(style, prop):
                try:
                    setattr(style, prop, "SOLID")
                    break
                except Exception:
                    pass

        width = max(float(stroke_spec.get("width", 0.0)), 0.0)
        if width > 0.0:
            for prop in ("pixel_size", "stroke_thickness", "line_width", "stroke_width"):
                if hasattr(style, prop):
                    try:
                        setattr(style, prop, width)
                        break
                    except Exception:
                        pass

    display_color = None
    if fill_kind == "GRADIENT":
        gradient_id = str(fill_spec.get("gradient_id", ""))
        gradient = gradient_defs.get(gradient_id, {})
        display_color = gradient.get("first_color")
    elif fill_kind == "SOLID":
        display_color = fill_spec.get("color")
    elif stroke_kind == "SOLID":
        display_color = stroke_spec.get("color")

    if isinstance(display_color, tuple) and len(display_color) >= 4:
        rgba = (
            float(display_color[0]),
            float(display_color[1]),
            float(display_color[2]),
            float(display_color[3]),
        )
        if hasattr(material, "diffuse_color"):
            try:
                material.diffuse_color = rgba
            except Exception:
                pass
        if hasattr(material, "surface_render_method"):
            try:
                material.surface_render_method = "BLENDED" if rgba[3] < 0.999 else "DITHERED"
            except Exception:
                pass
        if hasattr(material, "use_transparency_overlap"):
            try:
                material.use_transparency_overlap = True
            except Exception:
                pass


def _desired_material_base_name(fill_spec: Dict[str, object], stroke_spec: Dict[str, object]) -> str:
    """Return preferred material base name by role."""
    fill_kind = str(fill_spec.get("kind", "NONE"))
    stroke_kind = str(stroke_spec.get("kind", "NONE"))
    if fill_kind != "NONE":
        return "Fill"
    if stroke_kind != "NONE":
        return "Stroke"
    return "Stroke"


def _remove_drawing_override_attributes(drawing: object) -> None:
    """Remove imported GP color override attributes so material colors stay live."""
    removable_names = {"fill_color", "stroke_color", "vertex_color", "fill_opacity"}
    for group_name in ("color_attributes", "attributes"):
        group = getattr(drawing, group_name, None)
        if group is None or not hasattr(group, "remove"):
            continue
        to_remove = []
        try:
            for attribute in group:
                if getattr(attribute, "name", "") in removable_names:
                    to_remove.append(attribute)
        except Exception:
            continue
        for attribute in to_remove:
            try:
                group.remove(attribute)
            except Exception:
                pass


def _rounded_color_key(color: Optional[Tuple[float, float, float, float]]) -> Optional[Tuple[float, float, float, float]]:
    """Normalize color tuple for material cache keys."""
    if color is None:
        return None
    return tuple(round(float(channel), 6) for channel in color[:4])


def _material_cache_key(
    fill_spec: Dict[str, object],
    stroke_spec: Dict[str, object],
    gradient_defs: Dict[str, Dict[str, object]],
    bounds: Optional[Tuple[float, float, float, float]],
    layer_transform: Tuple[float, float, float, float, float, float],
) -> Tuple[object, ...]:
    """Build stable cache key so identical SVG materials reuse GP materials."""
    fill_kind = str(fill_spec.get("kind", "NONE"))
    stroke_kind = str(stroke_spec.get("kind", "NONE"))

    fill_key: Tuple[object, ...]
    if fill_kind == "SOLID":
        fill_key = ("SOLID", _rounded_color_key(fill_spec.get("color")))  # type: ignore[arg-type]
    elif fill_kind == "GRADIENT":
        gradient_id = str(fill_spec.get("gradient_id", ""))
        gradient = gradient_defs.get(gradient_id, {})
        start, end = _gradient_world_endpoints(gradient, bounds, layer_transform)
        location, angle, scale = _gradient_mapping_from_endpoints(start, end, bounds)
        fill_key = (
            "GRADIENT",
            str(gradient.get("type", "LINEAR")),
            _rounded_color_key(gradient.get("first_color")),  # type: ignore[arg-type]
            _rounded_color_key(gradient.get("second_color")),  # type: ignore[arg-type]
            round(float(location[0]), 6),
            round(float(location[1]), 6),
            round(float(angle), 6),
            round(float(scale[0]), 6),
            round(float(scale[1]), 6),
        )
    else:
        fill_key = ("NONE",)

    if stroke_kind == "SOLID":
        stroke_key = (
            "SOLID",
            _rounded_color_key(stroke_spec.get("color")),  # type: ignore[arg-type]
            round(float(stroke_spec.get("width", 0.0)), 6),
        )
    else:
        stroke_key = ("NONE",)

    return (fill_key, stroke_key)


def _ensure_material_slot(gp_obj: bpy.types.Object, material: bpy.types.Material) -> int:
    """Ensure material is in object material slots and return index."""
    slots = getattr(gp_obj.data, "materials", None)
    if slots is None:
        return -1

    for idx, mat in enumerate(slots):
        if mat == material:
            return idx
    try:
        slots.append(material)
        return len(slots) - 1
    except Exception:
        return -1


def _assign_material_to_layer_content(
    gp_obj: bpy.types.Object,
    layer: object,
    material: bpy.types.Material,
    material_index: int,
    fill_spec: Dict[str, object],
    stroke_spec: Dict[str, object],
    gradient_defs: Dict[str, Dict[str, object]],
) -> None:
    """Best-effort assignment of material to GP layer geometry."""
    for attr in ("material", "active_material"):
        if hasattr(layer, attr):
            try:
                setattr(layer, attr, material)
            except Exception:
                pass
    for attr in ("material_index", "active_material_index"):
        if hasattr(layer, attr):
            try:
                setattr(layer, attr, material_index)
            except Exception:
                pass

    frames = getattr(layer, "frames", None)
    if not frames:
        return
    for frame in frames:
        drawing = getattr(frame, "drawing", None)
        if drawing is None:
            continue

        strokes = getattr(drawing, "strokes", None)
        if strokes:
            for stroke in strokes:
                if hasattr(stroke, "material_index"):
                    try:
                        stroke.material_index = material_index
                    except Exception:
                        pass
        _remove_drawing_override_attributes(drawing)

        attributes = getattr(drawing, "attributes", None)
        if attributes is not None:
            try:
                material_attr = attributes.get("material_index")
            except Exception:
                material_attr = None
            if material_attr is not None:
                for item in getattr(material_attr, "data", []):
                    if hasattr(item, "value"):
                        try:
                            item.value = material_index
                        except Exception:
                            pass


def _layer_used_material_indices(layer: object) -> List[int]:
    """Collect material indices referenced by a GP layer."""
    indices: Set[int] = set()
    for attr in ("material_index", "active_material_index"):
        if hasattr(layer, attr):
            try:
                value = int(getattr(layer, attr))
                if value >= 0:
                    indices.add(value)
            except Exception:
                pass

    frames = getattr(layer, "frames", None)
    if not frames:
        return sorted(indices)

    for frame in frames:
        drawing = getattr(frame, "drawing", None)
        if drawing is None:
            continue

        strokes = getattr(drawing, "strokes", None)
        if strokes:
            for stroke in strokes:
                if hasattr(stroke, "material_index"):
                    try:
                        value = int(stroke.material_index)
                        if value >= 0:
                            indices.add(value)
                    except Exception:
                        pass

        attributes = getattr(drawing, "attributes", None)
        if attributes is not None:
            try:
                material_attr = attributes.get("material_index")
            except Exception:
                material_attr = None
            if material_attr is not None:
                for item in getattr(material_attr, "data", []):
                    if hasattr(item, "value"):
                        try:
                            value = int(item.value)
                            if value >= 0:
                                indices.add(value)
                        except Exception:
                            pass
    return sorted(indices)


def _apply_gp_materials(gp_obj: bpy.types.Object, plan: Dict[str, object]) -> None:
    """Create and assign GP materials based on parsed SVG style metadata."""
    if not is_valid_gp_object(gp_obj):
        return

    layer_fill_specs = plan.get("layer_fill_specs", {})
    layer_stroke_specs = plan.get("layer_stroke_specs", {})
    gradient_defs = plan.get("gradient_defs", {})
    layer_bounds = plan.get("layer_bounds", {})
    layer_transforms = plan.get("layer_transforms", {})
    layer_names: List[str] = []
    seen_names: Set[str] = set()
    for name in list(layer_fill_specs.keys()) + list(layer_stroke_specs.keys()):
        if name in seen_names:
            continue
        seen_names.add(name)
        layer_names.append(name)

    if not layer_names:
        return

    claimed_existing_indices: Set[int] = set()
    material_cache: Dict[Tuple[object, ...], Tuple[bpy.types.Material, int]] = {}

    for layer_name in layer_names:
        fill_spec = layer_fill_specs.get(layer_name) or {"kind": "NONE"}
        stroke_spec = layer_stroke_specs.get(layer_name) or {"kind": "NONE"}
        base_material_name = _desired_material_base_name(fill_spec, stroke_spec)
        if "kind" not in fill_spec:
            fill_spec = {"kind": "NONE"}
        if "kind" not in stroke_spec:
            stroke_spec = {"kind": "NONE"}
        if fill_spec.get("kind") == "NONE" and stroke_spec.get("kind") == "NONE":
            continue
        layer = _find_gp_layer(gp_obj, layer_name)
        if layer is None:
            continue

        cache_key = _material_cache_key(
            fill_spec,
            stroke_spec,
            gradient_defs,
            layer_bounds.get(layer_name),
            layer_transforms.get(layer_name, _matrix_identity()),
        )
        cached_entry = material_cache.get(cache_key)
        if cached_entry is not None:
            cached_material, cached_index = cached_entry
            _assign_material_to_layer_content(
                gp_obj,
                layer,
                cached_material,
                cached_index,
                fill_spec,
                stroke_spec,
                gradient_defs,
            )
            continue

        slots = getattr(gp_obj.data, "materials", None)
        used_indices = _layer_used_material_indices(layer)
        if slots and used_indices:
            reusable_indices: List[int] = []
            for index in used_indices:
                if index < 0 or index >= len(slots):
                    continue
                material = slots[index]
                if material is None:
                    continue
                if index in claimed_existing_indices:
                    continue
                reusable_indices.append(index)

            if reusable_indices:
                primary_index = reusable_indices[0]
                material = slots[primary_index]
                _apply_style_to_gp_material(
                    material,
                    fill_spec,
                    stroke_spec,
                    gradient_defs,
                    layer_bounds.get(layer_name),
                    layer_transforms.get(layer_name, _matrix_identity()),
                )
                try:
                    if material.name.startswith("Stroke") and base_material_name == "Fill":
                        material.name = "Fill"
                    elif material.name.startswith("Fill") and base_material_name == "Stroke":
                        material.name = "Stroke"
                except Exception:
                    pass
                claimed_existing_indices.add(primary_index)
                _assign_material_to_layer_content(
                    gp_obj,
                    layer,
                    material,
                    primary_index,
                    fill_spec,
                    stroke_spec,
                    gradient_defs,
                )
                material_cache[cache_key] = (material, primary_index)
                continue

        material = bpy.data.materials.new(base_material_name)
        _apply_style_to_gp_material(
            material,
            fill_spec,
            stroke_spec,
            gradient_defs,
            layer_bounds.get(layer_name),
            layer_transforms.get(layer_name, _matrix_identity()),
        )
        material_index = _ensure_material_slot(gp_obj, material)
        if material_index >= 0:
            claimed_existing_indices.add(material_index)
            _assign_material_to_layer_content(
                gp_obj,
                layer,
                material,
                material_index,
                fill_spec,
                stroke_spec,
                gradient_defs,
            )
            material_cache[cache_key] = (material, material_index)


def _link_object_like(source_obj: bpy.types.Object, new_obj: bpy.types.Object, scene: bpy.types.Scene) -> None:
    """Link a new object to the same collections as source object."""
    collections = list(source_obj.users_collection)
    if not collections:
        scene.collection.objects.link(new_obj)
        return
    for collection in collections:
        collection.objects.link(new_obj)


def split_gp_object_layers(source_obj: bpy.types.Object, scene: bpy.types.Scene) -> List[bpy.types.Object]:
    """Split GP object into one object per layer using low-level data API only."""
    if not is_valid_gp_object(source_obj):
        return []

    source_layers = list(source_obj.data.layers)
    if len(source_layers) < 2:
        return []

    new_objects: List[bpy.types.Object] = []
    for keep_index, source_layer in enumerate(source_layers):
        new_data = source_obj.data.copy()

        # Remove every layer except the indexed one to avoid name collision issues.
        for idx in range(len(new_data.layers) - 1, -1, -1):
            if idx != keep_index:
                new_data.layers.remove(new_data.layers[idx])

        if len(new_data.layers) == 0:
            continue
        layer_name = new_data.layers[0].name or source_layer.name or f"Layer_{keep_index + 1}"

        new_obj = source_obj.copy()
        new_obj.data = new_data
        new_obj.animation_data_clear()
        new_obj.matrix_world = source_obj.matrix_world.copy()
        new_obj.name = layer_name
        new_obj.data.name = f"{layer_name}_GP"
        new_obj.hide_set(False)
        new_obj.hide_render = False
        _link_object_like(source_obj, new_obj, scene)
        new_objects.append(new_obj)

    source_obj.hide_set(True)
    source_obj.hide_render = True
    return new_objects


def _decode_data_uri_to_temp_image(data_uri: str) -> Optional[bpy.types.Image]:
    """Decode base64 data URI image and load into Blender image datablock."""
    if not data_uri.startswith("data:image/"):
        return None
    if "," not in data_uri:
        return None

    header, payload = data_uri.split(",", 1)
    is_base64 = ";base64" in header
    if not is_base64:
        return None

    mime = header.split(";")[0][5:].strip().lower()
    extension_map = {
        "image/png": ".png",
        "image/jpeg": ".jpg",
        "image/jpg": ".jpg",
        "image/webp": ".webp",
        "image/bmp": ".bmp",
        "image/tiff": ".tif",
    }
    suffix = extension_map.get(mime, ".img")

    temp_path = None
    try:
        binary = base64.b64decode(payload)
        temp_file = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
        temp_path = temp_file.name
        temp_file.write(binary)
        temp_file.close()
        image = bpy.data.images.load(temp_path, check_existing=False)
        try:
            image.pack()
        except Exception:
            pass
        return image
    except Exception:
        return None
    finally:
        if temp_path and os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except OSError:
                pass


def _load_svg_image(href: str) -> Optional[bpy.types.Image]:
    """Load image from SVG href (base64 or local path)."""
    href = href.strip()
    if href.startswith("data:image/"):
        return _decode_data_uri_to_temp_image(href)
    if os.path.exists(href):
        try:
            return bpy.data.images.load(href, check_existing=True)
        except Exception:
            return None
    return None


def _create_image_empty(scene: bpy.types.Scene, image: bpy.types.Image, item: Dict[str, object]) -> Optional[bpy.types.Object]:
    """Create IMAGE empty object from parsed SVG image item."""
    try:
        obj = bpy.data.objects.new(item["name"], None)
        obj.empty_display_type = "IMAGE"
        obj.data = image
        obj.location = (float(item["x"]), -float(item["y"]), 0.0)

        width = max(float(item["width"]), 0.001)
        height = max(float(item["height"]), 0.001)
        longest_side = max(width, height)
        obj.empty_display_size = longest_side * 0.5
        obj.scale = (width / longest_side, height / longest_side, 1.0)
        scene.collection.objects.link(obj)
        return obj
    except Exception:
        return None


def _ensure_image_plane_material(image: bpy.types.Image) -> bpy.types.Material:
    """Create or reuse a simple node material for image planes."""
    material_name = f"TP_Image_{image.name}"
    material = bpy.data.materials.get(material_name)
    if material is None:
        material = bpy.data.materials.new(material_name)
    if hasattr(material, "use_nodes"):
        material.use_nodes = True
    if material.node_tree is None:
        return material

    nodes = material.node_tree.nodes
    links = material.node_tree.links
    nodes.clear()

    output_node = nodes.new("ShaderNodeOutputMaterial")
    output_node.location = (300, 0)
    shader_node = nodes.new("ShaderNodeBsdfPrincipled")
    shader_node.location = (0, 0)
    texture_node = nodes.new("ShaderNodeTexImage")
    texture_node.location = (-320, 0)
    texture_node.image = image

    links.new(texture_node.outputs["Color"], shader_node.inputs["Base Color"])
    if "Alpha" in texture_node.outputs and "Alpha" in shader_node.inputs:
        links.new(texture_node.outputs["Alpha"], shader_node.inputs["Alpha"])
    links.new(shader_node.outputs["BSDF"], output_node.inputs["Surface"])

    if hasattr(material, "blend_method"):
        material.blend_method = "BLEND"
    if hasattr(material, "shadow_method"):
        material.shadow_method = "NONE"
    if hasattr(material, "use_backface_culling"):
        material.use_backface_culling = False
    return material


def _create_image_plane(
    scene: bpy.types.Scene,
    image: bpy.types.Image,
    item: Dict[str, object],
) -> Optional[bpy.types.Object]:
    """Create a mesh plane with image material."""
    try:
        width = max(float(item["width"]), 0.001)
        height = max(float(item["height"]), 0.001)
        half_w = width * 0.5
        half_h = height * 0.5

        mesh = bpy.data.meshes.new(f"{item['name']}_Mesh")
        mesh.from_pydata(
            [
                (-half_w, -half_h, 0.0),
                (half_w, -half_h, 0.0),
                (half_w, half_h, 0.0),
                (-half_w, half_h, 0.0),
            ],
            [],
            [(0, 1, 2, 3)],
        )
        mesh.update()

        uv_layer = mesh.uv_layers.new(name="UVMap")
        if uv_layer and len(uv_layer.data) >= 4:
            uv_data = uv_layer.data
            uv_data[0].uv = (0.0, 0.0)
            uv_data[1].uv = (1.0, 0.0)
            uv_data[2].uv = (1.0, 1.0)
            uv_data[3].uv = (0.0, 1.0)

        material = _ensure_image_plane_material(image)
        mesh.materials.append(material)

        obj = bpy.data.objects.new(str(item["name"]), mesh)
        obj.location = (float(item["x"]) + half_w, -float(item["y"]) - half_h, 0.0)
        scene.collection.objects.link(obj)
        return obj
    except Exception:
        return None


def _create_image_object(
    scene: bpy.types.Scene,
    image: bpy.types.Image,
    item: Dict[str, object],
    image_mode: str,
) -> Optional[bpy.types.Object]:
    """Create either reference image or mesh plane."""
    if image_mode == "MESH_PLANE":
        return _create_image_plane(scene, image, item)
    return _create_image_empty(scene, image, item)


def create_images_from_svg(
    scene: bpy.types.Scene,
    image_items: List[Dict[str, object]],
    image_mode: str,
) -> List[bpy.types.Object]:
    """Create Blender objects for SVG <image> items."""
    created: List[bpy.types.Object] = []
    for item in image_items:
        image = _load_svg_image(str(item["href"]))
        if not image:
            continue
        obj = _create_image_object(scene, image, item, image_mode)
        if obj:
            created.append(obj)
    return created


def _resolve_font(font_family: str) -> Optional[object]:
    """Best-effort mapping from SVG font-family to loaded Blender font."""
    if not font_family:
        return None
    first_choice = font_family.split(",")[0].strip().strip("'\"")
    if not first_choice:
        return None

    for font in bpy.data.fonts:
        if first_choice.lower() in font.name.lower():
            return font

    if os.path.exists(first_choice):
        try:
            return bpy.data.fonts.load(first_choice)
        except Exception:
            return None
    return None


def create_text_from_svg(scene: bpy.types.Scene, text_items: List[Dict[str, object]]) -> List[bpy.types.Object]:
    """Create Blender FONT objects for SVG text nodes."""
    created: List[bpy.types.Object] = []
    for item in text_items:
        try:
            curve_data = bpy.data.curves.new(name=f"{item['name']}_Font", type="FONT")
            curve_data.body = str(item["body"])
            font_size = max(float(item["font_size"]) * 0.01, 0.01)
            curve_data.size = font_size

            font = _resolve_font(str(item["font_family"]))
            if font:
                curve_data.font = font

            obj = bpy.data.objects.new(str(item["name"]), curve_data)
            obj.location = (float(item["x"]), -float(item["y"]), 0.0)
            scene.collection.objects.link(obj)
            created.append(obj)
        except Exception:
            continue
    return created


def _find_image_editor_override(context: bpy.types.Context) -> Optional[Dict[str, object]]:
    """Find IMAGE_EDITOR area for clipboard image paste operator."""
    window = context.window
    if not window or not window.screen:
        return None
    screen = window.screen
    for area in screen.areas:
        if area.type != "IMAGE_EDITOR":
            continue
        region = next((region for region in area.regions if region.type == "WINDOW"), None)
        if not region:
            continue
        return {
            "window": window,
            "screen": screen,
            "area": area,
            "region": region,
            "space_data": area.spaces.active,
        }
    return None


def _build_area_override(context: bpy.types.Context, area: object) -> Optional[Dict[str, object]]:
    """Build context override dictionary for a specific area."""
    window = context.window
    if not window or not window.screen or not area:
        return None
    region = next((region for region in area.regions if region.type == "WINDOW"), None)
    if not region:
        return None
    return {
        "window": window,
        "screen": window.screen,
        "area": area,
        "region": region,
        "space_data": area.spaces.active,
    }


def paste_image_from_clipboard_builtin(context: bpy.types.Context) -> Optional[bpy.types.Image]:
    """Use Blender built-in image clipboard paste in an Image Editor context."""
    if not hasattr(bpy.ops.image, "clipboard_paste"):
        raise RuntimeError("Blender image clipboard paste operator is unavailable.")

    override = _find_image_editor_override(context)
    if override:
        with context.temp_override(**override):
            result = bpy.ops.image.clipboard_paste()
            if "CANCELLED" in result:
                raise RuntimeError("Image clipboard paste failed or clipboard has no image data.")
            return override["space_data"].image

    # Fallback: temporarily turn an existing area into IMAGE_EDITOR.
    screen = context.window.screen if context.window else None
    fallback_area = None
    if screen:
        fallback_area = next(
            (area for area in screen.areas if area.type not in {"TOPBAR", "STATUSBAR"}),
            None,
        )
    if fallback_area is None:
        raise RuntimeError("No editable area available to paste image from clipboard.")

    original_type = fallback_area.type
    try:
        fallback_area.type = "IMAGE_EDITOR"
        temp_override = _build_area_override(context, fallback_area)
        if not temp_override:
            raise RuntimeError("Failed to build temporary Image Editor context.")
        with context.temp_override(**temp_override):
            result = bpy.ops.image.clipboard_paste()
            if "CANCELLED" in result:
                raise RuntimeError("Clipboard has no image data to paste.")
            return temp_override["space_data"].image
    finally:
        try:
            fallback_area.type = original_type
        except Exception:
            pass


def _paste_target_location(scene: bpy.types.Scene, paste_mode: str) -> Tuple[float, float, float]:
    """Resolve target location for pasted objects."""
    if paste_mode == "CURSOR":
        cursor = scene.cursor.location
        return (float(cursor.x), float(cursor.y), float(cursor.z))
    return (0.0, 0.0, 0.0)


def _move_objects_to_target(objects: List[bpy.types.Object], scene: bpy.types.Scene, paste_mode: str) -> None:
    """Move a group of objects as a block so their center lands on target."""
    valid = [obj for obj in objects if obj and hasattr(obj, "location")]
    if not valid:
        return

    count = float(len(valid))
    center_x = sum(float(obj.location.x) for obj in valid) / count
    center_y = sum(float(obj.location.y) for obj in valid) / count
    center_z = sum(float(obj.location.z) for obj in valid) / count
    target_x, target_y, target_z = _paste_target_location(scene, paste_mode)
    delta_x = target_x - center_x
    delta_y = target_y - center_y
    delta_z = target_z - center_z

    for obj in valid:
        obj.location.x += delta_x
        obj.location.y += delta_y
        obj.location.z += delta_z


def _stack_curve_objects(objects: List[bpy.types.Object], axis: str, depth: float) -> None:
    """Offset imported curve objects into a stack along one axis."""
    valid = [obj for obj in objects if obj and obj.type == "CURVE" and hasattr(obj, "location")]
    if len(valid) <= 1 or depth == 0.0:
        return

    axis_name = axis.upper()
    for index, obj in enumerate(valid):
        offset = float(index) * float(depth)
        if axis_name == "X":
            obj.location.x += offset
        elif axis_name == "Y":
            obj.location.y += offset
        else:
            obj.location.z += offset


def _stack_curve_batches(
    batches: List[Tuple[Tuple[str, ...], List[bpy.types.Object]]],
    axis: str,
    depth: float,
) -> None:
    """Offset curve batches so objects sharing one SVG group stay on the same level."""
    if len(batches) <= 1 or depth == 0.0:
        return

    axis_name = axis.upper()
    for batch_index, (_, objects) in enumerate(batches):
        offset = float(batch_index) * float(depth)
        for obj in objects:
            if obj is None or obj.type != "CURVE" or not hasattr(obj, "location"):
                continue
            if axis_name == "X":
                obj.location.x += offset
            elif axis_name == "Y":
                obj.location.y += offset
            else:
                obj.location.z += offset


class TOOTHPASTE_OT_paste_svg(bpy.types.Operator):
    """Paste SVG as Grease Pencil object and rebuild masks/holdout where possible."""

    bl_idname = "toothpaste.paste_svg"
    bl_label = "Paste as Grease Pencil"
    bl_description = "Paste SVG code from clipboard as Grease Pencil (v3)"

    def execute(self, context: bpy.types.Context):
        try:
            svg_data = get_clipboard_svg()
            root = parse_svg_root(svg_data)
            split_layers = context.scene.toothpaste_split_into_gp_layers
            plan = _prepare_import_plan(root, split_layers=split_layers)
            plan["create_gp_groups"] = bool(context.scene.toothpaste_create_gp_groups and split_layers)
        except (ClipboardError, InvalidSVGError) as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}

        metadata = plan["metadata"]
        has_importable = bool(plan["gp_svg"])
        has_extras = bool(metadata["texts"] or metadata["images"])
        if not has_importable and not has_extras:
            self.report({"ERROR"}, "No valid SVG layers, text, or image content found.")
            return {"CANCELLED"}

        gp_created = 0
        gp_objects: List[bpy.types.Object] = []
        text_objects: List[bpy.types.Object] = []
        image_objects: List[bpy.types.Object] = []
        pasted_objects: List[bpy.types.Object] = []
        before_collections = _scene_collection_pointers(context.scene)

        try:
            if has_importable:
                new_objects = import_svg_via_tempfile(
                    context,
                    plan["gp_svg"],
                    import_mode="GP",
                    gp_split_layers=split_layers,
                )
                gp_objects = [obj for obj in new_objects if obj.type == GP_TYPE]
                if gp_objects:
                    _apply_gp_masks_and_holdout(gp_objects[0], plan)
                    _apply_gp_materials(gp_objects[0], plan)
                    _ensure_gp_draw_uses_material_color(context)
                    gp_created = len(gp_objects)
                    pasted_objects.extend(gp_objects)

            text_objects = create_text_from_svg(context.scene, metadata["texts"])
            image_objects = create_images_from_svg(
                context.scene,
                metadata["images"],
                context.scene.toothpaste_image_paste_mode,
            )
            pasted_objects.extend(text_objects)
            pasted_objects.extend(image_objects)
            _move_objects_to_target(
                pasted_objects,
                context.scene,
                context.scene.toothpaste_paste_location,
            )
            new_collections = _new_scene_collections(context.scene, before_collections)
            _move_objects_to_collection(context.scene, gp_objects if has_importable else [], "Pasted SVG", new_collections)
            _move_objects_to_collection(context.scene, text_objects, "Pasted Text")
            _move_objects_to_collection(context.scene, image_objects, "Pasted Image")
        except Exception as exc:
            self.report({"ERROR"}, f"Import failed: {exc}")
            return {"CANCELLED"}

        self.report(
            {"INFO"},
            f"Imported GP: {gp_created}, Text: {len(text_objects)}, Images: {len(image_objects)}",
        )
        return {"FINISHED"}


class TOOTHPASTE_OT_paste_svg_curve(bpy.types.Operator):
    """Paste SVG as Curve objects with optional split by SVG layers."""

    bl_idname = "toothpaste.paste_svg_curve"
    bl_label = "Paste as Curves"
    bl_description = "Paste SVG code from clipboard as Curve objects"

    def execute(self, context: bpy.types.Context):
        try:
            svg_data = get_clipboard_svg()
            root = parse_svg_root(svg_data)
            plan = _prepare_import_plan(root, split_layers=True)
        except (ClipboardError, InvalidSVGError) as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}

        metadata = plan["metadata"]
        curve_objects: List[bpy.types.Object] = []
        curve_batches: List[Tuple[Tuple[str, ...], List[bpy.types.Object]]] = []
        text_objects: List[bpy.types.Object] = []
        image_objects: List[bpy.types.Object] = []
        pasted_objects: List[bpy.types.Object] = []
        before_collections = _scene_collection_pointers(context.scene)

        try:
            if plan["split_curve_groups"]:
                for layer in plan["split_curve_groups"]:
                    layer_svg = _build_svg_document(root, [layer["element"]])
                    new_objects = import_svg_via_tempfile(context, layer_svg, import_mode="CURVE")
                    layer_curves = [obj for obj in new_objects if obj.type == "CURVE"]
                    if len(layer_curves) == 1:
                        layer_curves[0].name = str(layer["name"])
                    elif len(layer_curves) > 1:
                        base = str(layer["name"])
                        for index, obj in enumerate(layer_curves, start=1):
                            obj.name = f"{base}_{index}"
                    curve_objects.extend(layer_curves)
                    curve_batches.append((tuple(layer.get("group_path") or [str(layer["name"])]), list(layer_curves)))
            elif plan["curve_svg"]:
                new_objects = import_svg_via_tempfile(context, plan["curve_svg"], import_mode="CURVE")
                curve_objects = [obj for obj in new_objects if obj.type == "CURVE"]
                if curve_objects:
                    curve_batches.append((("Pasted SVG",), list(curve_objects)))

            text_objects = create_text_from_svg(context.scene, metadata["texts"])
            image_objects = create_images_from_svg(
                context.scene,
                metadata["images"],
                context.scene.toothpaste_image_paste_mode,
            )
            pasted_objects.extend(curve_objects)
            pasted_objects.extend(text_objects)
            pasted_objects.extend(image_objects)
            _move_objects_to_target(
                pasted_objects,
                context.scene,
                context.scene.toothpaste_paste_location,
            )
            if context.scene.toothpaste_curve_stack_enabled:
                if context.scene.toothpaste_curve_stack_keep_groups:
                    grouped_batches: Dict[Tuple[str, ...], List[bpy.types.Object]] = {}
                    ordered_keys: List[Tuple[str, ...]] = []
                    for group_key, batch_objects in curve_batches:
                        if group_key not in grouped_batches:
                            grouped_batches[group_key] = []
                            ordered_keys.append(group_key)
                        grouped_batches[group_key].extend(batch_objects)
                    _stack_curve_batches(
                        [(group_key, grouped_batches[group_key]) for group_key in ordered_keys],
                        context.scene.toothpaste_curve_stack_axis,
                        context.scene.toothpaste_curve_stack_depth,
                    )
                else:
                    _stack_curve_objects(
                        curve_objects,
                        context.scene.toothpaste_curve_stack_axis,
                        context.scene.toothpaste_curve_stack_depth,
                    )
            new_collections = _new_scene_collections(context.scene, before_collections)
            _move_objects_to_collection(context.scene, curve_objects, "Pasted SVG", new_collections)
            _move_objects_to_collection(context.scene, text_objects, "Pasted Text")
            _move_objects_to_collection(context.scene, image_objects, "Pasted Image")
        except Exception as exc:
            self.report({"ERROR"}, f"Curve import failed: {exc}")
            return {"CANCELLED"}

        if len(curve_objects) == 0 and len(text_objects) == 0 and len(image_objects) == 0:
            self.report({"ERROR"}, "No importable SVG content found.")
            return {"CANCELLED"}

        self.report(
            {"INFO"},
            f"Imported Curves: {len(curve_objects)}, Text: {len(text_objects)}, Images: {len(image_objects)}",
        )
        return {"FINISHED"}


class TOOTHPASTE_OT_paste_clipboard_image(bpy.types.Operator):
    """Paste bitmap image from clipboard using Blender's built-in image operator."""

    bl_idname = "toothpaste.paste_clipboard_image"
    bl_label = "Paste Image (Clipboard)"
    bl_description = "Paste image from clipboard using Blender built-in functionality"

    def execute(self, context: bpy.types.Context):
        try:
            image = paste_image_from_clipboard_builtin(context)
            if image is None:
                self.report({"ERROR"}, "No image was created from clipboard.")
                return {"CANCELLED"}

            item = {
                "name": image.name,
                "x": 0.0,
                "y": 0.0,
                "width": float(image.size[0]) if image.size and image.size[0] else 1.0,
                "height": float(image.size[1]) if image.size and image.size[1] else 1.0,
            }
            obj = _create_image_object(
                context.scene,
                image,
                item,
                context.scene.toothpaste_image_paste_mode,
            )
            if obj is None:
                self.report({"ERROR"}, "Failed to create image object.")
                return {"CANCELLED"}
            _move_objects_to_target(
                [obj],
                context.scene,
                context.scene.toothpaste_paste_location,
            )
            _move_objects_to_collection(context.scene, [obj], "Pasted Image")
            self.report({"INFO"}, f"Pasted image: {image.name}")
            return {"FINISHED"}
        except Exception as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}


class GP_OT_SplitLayers(bpy.types.Operator):
    """Split active GP object layers into separate objects without bpy.ops duplicate."""

    bl_idname = "gpencil.split_layers_into_objects"
    bl_label = "Split layers into objects"
    bl_description = "Split selected Grease Pencil layers into separate objects"

    @classmethod
    def poll(cls, context: bpy.types.Context):
        return is_valid_gp_object(context.active_object)

    def execute(self, context: bpy.types.Context):
        source_obj = context.active_object
        if len(source_obj.data.layers) < 2:
            self.report({"WARNING"}, "Selected Grease Pencil object contains fewer than 2 layers.")
            return {"CANCELLED"}

        new_objects = split_gp_object_layers(source_obj, context.scene)
        if not new_objects:
            self.report({"ERROR"}, "Failed to split layers.")
            return {"CANCELLED"}

        self.report({"INFO"}, f"Created {len(new_objects)} objects from layers.")
        return {"FINISHED"}


class TOOTHPASTE_PT_panel(bpy.types.Panel):
    """UI panel for ToothPaste tools."""

    bl_label = "ToothPaste"
    bl_idname = "TOOTHPASTE_PT_panel"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "ToothPaste"

    def draw(self, context: bpy.types.Context):
        layout = self.layout
        layout.label(text="Grease Pencil")
        layout.operator("toothpaste.paste_svg", icon="GREASEPENCIL")
        layout.prop(context.scene, "toothpaste_split_into_gp_layers", text="Split into GP Layers")
        if context.scene.toothpaste_split_into_gp_layers:
            layout.prop(context.scene, "toothpaste_create_gp_groups", text="Create GP Groups")
        layout.operator("gpencil.split_layers_into_objects", icon="GP_SELECT_POINTS")
        layout.separator()
        layout.label(text="Curves & Text")
        layout.operator("toothpaste.paste_svg_curve", icon="CURVE_DATA")
        layout.prop(context.scene, "toothpaste_curve_stack_enabled", text="Stack Curve Objects")
        if context.scene.toothpaste_curve_stack_enabled:
            row = layout.row(align=True)
            row.prop(context.scene, "toothpaste_curve_stack_axis", expand=True)
            layout.prop(context.scene, "toothpaste_curve_stack_depth", text="Depth")
            layout.prop(context.scene, "toothpaste_curve_stack_keep_groups", text="Keep Same SVG Group Level")
        layout.separator()
        layout.label(text="Images")
        layout.operator("toothpaste.paste_clipboard_image", icon="IMAGE_DATA")
        row = layout.row(align=True)
        row.prop(context.scene, "toothpaste_image_paste_mode", expand=True)
        layout.separator()
        layout.label(text="Paste at:")
        row = layout.row(align=True)
        row.prop(context.scene, "toothpaste_paste_location", expand=True)


classes = (
    TOOTHPASTE_OT_paste_svg,
    TOOTHPASTE_OT_paste_svg_curve,
    TOOTHPASTE_OT_paste_clipboard_image,
    GP_OT_SplitLayers,
    TOOTHPASTE_PT_panel,
)


def register():
    bpy.types.Scene.toothpaste_split_into_gp_layers = bpy.props.BoolProperty(
        name="Split into GP Layers",
        description="Split imported SVG geometry into separate GP layers",
        default=True,
    )
    bpy.types.Scene.toothpaste_paste_location = bpy.props.EnumProperty(
        name="Paste Location",
        description="Placement target for pasted objects",
        items=[
            ("ORIGIN", "World Origin", "Place pasted objects centered at world origin"),
            ("CURSOR", "Cursor", "Place pasted objects centered at 3D cursor"),
        ],
        default="ORIGIN",
    )
    bpy.types.Scene.toothpaste_image_paste_mode = bpy.props.EnumProperty(
        name="Image Paste Mode",
        description="Create images as reference empties or mesh planes",
        items=[
            ("MESH_PLANE", "Mesh Plane", "Paste images as textured mesh planes"),
            ("REFERENCE", "Reference", "Paste images as reference empties"),
        ],
        default="REFERENCE",
    )
    bpy.types.Scene.toothpaste_create_gp_groups = bpy.props.BoolProperty(
        name="Create GP Groups",
        description="Create Grease Pencil layer groups from SVG groups when supported by Blender",
        default=True,
    )
    bpy.types.Scene.toothpaste_curve_stack_enabled = bpy.props.BoolProperty(
        name="Stack Curve Objects",
        description="Offset imported curve objects into a viewport stack",
        default=False,
    )
    bpy.types.Scene.toothpaste_curve_stack_axis = bpy.props.EnumProperty(
        name="Stack Axis",
        description="Axis used to offset the curve stack",
        items=[
            ("X", "X", "Stack imported curves on the X axis"),
            ("Y", "Y", "Stack imported curves on the Y axis"),
            ("Z", "Z", "Stack imported curves on the Z axis"),
        ],
        default="Z",
    )
    bpy.types.Scene.toothpaste_curve_stack_depth = bpy.props.FloatProperty(
        name="Depth",
        description="Distance between imported curve objects in the stack",
        default=0.1,
        min=-10000.0,
        soft_min=-10.0,
        soft_max=10.0,
    )
    bpy.types.Scene.toothpaste_curve_stack_keep_groups = bpy.props.BoolProperty(
        name="Keep Same SVG Group Level",
        description="Objects from the same SVG group share one stack level",
        default=True,
    )
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
    if hasattr(bpy.types.Scene, "toothpaste_split_into_gp_layers"):
        del bpy.types.Scene.toothpaste_split_into_gp_layers
    if hasattr(bpy.types.Scene, "toothpaste_paste_location"):
        del bpy.types.Scene.toothpaste_paste_location
    if hasattr(bpy.types.Scene, "toothpaste_image_paste_mode"):
        del bpy.types.Scene.toothpaste_image_paste_mode
    if hasattr(bpy.types.Scene, "toothpaste_create_gp_groups"):
        del bpy.types.Scene.toothpaste_create_gp_groups
    if hasattr(bpy.types.Scene, "toothpaste_curve_stack_enabled"):
        del bpy.types.Scene.toothpaste_curve_stack_enabled
    if hasattr(bpy.types.Scene, "toothpaste_curve_stack_axis"):
        del bpy.types.Scene.toothpaste_curve_stack_axis
    if hasattr(bpy.types.Scene, "toothpaste_curve_stack_depth"):
        del bpy.types.Scene.toothpaste_curve_stack_depth
    if hasattr(bpy.types.Scene, "toothpaste_curve_stack_keep_groups"):
        del bpy.types.Scene.toothpaste_curve_stack_keep_groups


if __name__ == "__main__":
    register()
