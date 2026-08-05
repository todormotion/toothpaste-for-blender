"""
SVG document parsing.

Turns SVG text into an XML tree and collects the metadata the importers need:
layers, mask/clip definitions, gradients, text nodes and image nodes.
Pure Python: this module must never import bpy.
"""

import re
import xml.etree.ElementTree as ET
from typing import Dict, List, Optional, Set, Tuple

from .constants import INKSCAPE_NS, NS, SUPPORTED_GEOMETRY, SVG_NS, XLINK_NS
from .exceptions import InvalidSVGError
from .svg_geometry import convert_shape_to_path
from .svg_utils import (
    _clamp01,
    _clone_xml_element,
    _extract_mask_clip_refs,
    _first_list_number,
    _inline_css_class_styles,
    _is_identity_matrix,
    _local_name,
    _parse_gradient_offset,
    _parse_opacity,
    _parse_style,
    _parse_svg_color,
    _parse_svg_number,
    _parse_transform_attr,
)


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
