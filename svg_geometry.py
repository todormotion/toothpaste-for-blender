"""
SVG geometry helpers.

Shape-to-path conversion, path/shape bounding boxes, bounds transforms and the
geometry signatures used to match clip/mask definitions to visible geometry.
Pure Python: this module must never import bpy.
"""

import re
import xml.etree.ElementTree as ET
from typing import Dict, List, Optional, Set, Tuple

from .constants import SUPPORTED_GEOMETRY, SUPPORTED_SHAPES, SVG_NS
from .svg_utils import (
    _clone_xml_element,
    _local_name,
    _matrix_apply_point,
    _matrix_multiply,
    _parse_svg_number,
    _parse_transform_attr,
)


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
