"""
Grease Pencil materials.

Maps SVG fill/stroke/gradient paint onto Grease Pencil materials and assigns
them to imported layer content, reusing materials where the paint matches.
"""

import math
from typing import Dict, List, Optional, Set, Tuple

import bpy

from .gp_layers import _find_gp_layer, is_valid_gp_object
from .scene_utils import _set_enum_property, _set_vector_property
from .svg_utils import (
    _clamp01,
    _matrix_apply_point,
    _matrix_identity,
    _parse_gradient_coord,
)


def _ensure_gp_material_data(material: bpy.types.Material) -> object:
    """Ensure material has Grease Pencil data and return style handle."""
    if hasattr(bpy.data.materials, "create_gpencil_data"):
        try:
            bpy.data.materials.create_gpencil_data(material)
        except Exception:
            pass
    return getattr(material, "grease_pencil", None)


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
