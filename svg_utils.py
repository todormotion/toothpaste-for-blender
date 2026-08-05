"""
Low-level SVG value parsing helpers.

Numbers, CSS declarations, colors, opacity, paint specs and affine matrices.
Pure Python: this module must never import bpy.
"""

import math
import re
import xml.etree.ElementTree as ET
from typing import Dict, List, Optional, Set, Tuple


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
