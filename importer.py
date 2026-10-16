"""
Import pipeline.

The single path from a parsed SVG tree to objects in the scene, shared by the
clipboard paste operators, the File > Import entry and drag-and-drop. Operators
above this module only collect options and report results.
"""

import os
import xml.etree.ElementTree as ET
from typing import Dict, List, Optional, Set, Tuple

import bpy
from mathutils import Vector

from .constants import CURVE_SIZE_FACTOR, GP_IMPORT_SCALE, GP_TYPE, SVG_NS
from .exceptions import ImportContentError
from .gp_layers import _apply_gp_masks_and_holdout
from .gp_materials import _apply_gp_materials, _ensure_gp_draw_uses_material_color
from .obj_images import create_images_from_svg
from .obj_text import create_text_from_svg
from .properties import ImportSettings
from .scene_utils import (
    _move_objects_to_collection,
    _new_scene_collections,
    _new_scene_objects,
    _operator_supported_kwargs,
    _scene_collection_pointers,
    _scene_object_pointers,
    _stack_curve_batches,
    _stack_curve_objects,
    _unlink_collection_from_tree,
    _write_temp_svg,
    place_import_block,
    scale_curve_objects,
)
from .svg_frame import SvgFrame, fallback_frame, view_origin
from .svg_plan import _build_svg_document, _prepare_import_plan


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
                    "scale": GP_IMPORT_SCALE,
                    # keep art at its SVG coordinates so text and images line up with it;
                    # the whole block is centred on the paste target afterwards
                    "recenter_bounds": False,
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


PROBE_SIZE = 100.0


def _gp_corner_and_width(gp_obj: bpy.types.Object) -> Optional[Tuple[float, float, float]]:
    """Top-left corner (X, Y in the flat frame) and width of imported Grease Pencil points."""
    xs: List[float] = []
    zs: List[float] = []
    for layer in gp_obj.data.layers:
        for frame in layer.frames:
            positions = frame.drawing.attributes.get("position")
            if positions is None:
                continue
            for point in positions.data:
                world = gp_obj.matrix_world @ Vector(point.vector)
                xs.append(world[0])
                zs.append(world[2])
    if not xs:
        return None
    return min(xs), max(zs), max(xs) - min(xs)


def _curve_corner_and_width(curve_obj: bpy.types.Object) -> Optional[Tuple[float, float, float]]:
    corners = [curve_obj.matrix_world @ Vector(corner) for corner in curve_obj.bound_box]
    xs = [corner[0] for corner in corners]
    ys = [corner[1] for corner in corners]
    return min(xs), max(ys), max(xs) - min(xs)


def _remove_probe_leftovers(
    context: bpy.types.Context,
    new_objects: List[bpy.types.Object],
    datablocks_before: Dict[str, Set[int]],
    collections_before: Set[int],
) -> None:
    """Delete everything a probe import created, including its data and collections."""
    for obj in new_objects:
        try:
            bpy.data.objects.remove(obj, do_unlink=True)
        except Exception:
            pass
    for collection in _new_scene_collections(context.scene, collections_before):
        try:
            _unlink_collection_from_tree(context.scene.collection, collection)
            bpy.data.collections.remove(collection)
        except Exception:
            pass
    # Object data first: it holds the materials, which are only unused once it is gone.
    for name, collection in _probe_datablock_collections():
        before = datablocks_before.get(name, set())
        for datablock in list(collection):
            if datablock.as_pointer() not in before and datablock.users == 0:
                try:
                    collection.remove(datablock)
                except Exception:
                    pass


def _probe_datablock_collections() -> List[Tuple[str, object]]:
    """(name, bpy.data collection) pairs a probe import can create, ordered object data first."""
    names = ("grease_pencils", "grease_pencils_v3", "curves", "materials")
    return [(name, getattr(bpy.data, name)) for name in names if hasattr(bpy.data, name)]


def probe_frame(context: bpy.types.Context, root: ET.Element, import_mode: str) -> SvgFrame:
    """
    Measure where Blender's importer puts this SVG's canvas.

    A square of known size at the canvas corner is imported through the same
    importer, with the same root attributes (viewBox, width, height), and then
    deleted. Where its corner landed, and how large it came out, is exactly how
    the artwork was placed, whatever the file's units or viewBox. Falls back to
    the measured Blender 5.2 values if the probe cannot be read.
    """
    fallback = fallback_frame(root, import_mode)
    view_x, view_y = view_origin(root)

    probe_root = ET.Element(root.tag, dict(root.attrib))
    ET.SubElement(
        probe_root,
        f"{{{SVG_NS}}}rect",
        {"x": repr(view_x), "y": repr(view_y), "width": repr(PROBE_SIZE), "height": repr(PROBE_SIZE), "fill": "#000000"},
    )

    datablocks_before = {
        name: {datablock.as_pointer() for datablock in collection}
        for name, collection in _probe_datablock_collections()
    }
    collections_before = _scene_collection_pointers(context.scene)
    new_objects: List[bpy.types.Object] = []
    try:
        new_objects = import_svg_via_tempfile(
            context, ET.tostring(probe_root, encoding="unicode"), import_mode=import_mode
        )
        if import_mode == "GP":
            target = next((obj for obj in new_objects if obj.type == GP_TYPE), None)
            measured = _gp_corner_and_width(target) if target else None
        else:
            target = next((obj for obj in new_objects if obj.type == "CURVE"), None)
            measured = _curve_corner_and_width(target) if target else None
    except Exception:
        measured = None
    finally:
        _remove_probe_leftovers(context, new_objects, datablocks_before, collections_before)

    if not measured or measured[2] <= 1e-9:
        return fallback
    left, top, width = measured
    # Curves are scaled up to the Grease Pencil size after import (see
    # CURVE_SIZE_FACTOR), about the canvas origin, so the frame scales with them.
    factor = CURVE_SIZE_FACTOR if import_mode == "CURVE" else 1.0
    return SvgFrame(
        units_per_user=width / PROBE_SIZE * factor,
        origin_x=left * factor,
        origin_y=top * factor,
        view_x=view_x,
        view_y=view_y,
    )


def import_svg_as_grease_pencil(
    context: bpy.types.Context,
    root: ET.Element,
    settings: ImportSettings,
) -> Dict[str, object]:
    """Import a parsed SVG tree as a Grease Pencil object plus text/image extras."""
    split_layers = bool(settings.split_into_gp_layers)
    plan = _prepare_import_plan(root, split_layers=split_layers)
    plan["create_gp_groups"] = bool(settings.create_gp_groups and split_layers)

    metadata = plan["metadata"]
    has_importable = bool(plan["gp_svg"])
    has_extras = bool(metadata["texts"] or metadata["images"])
    if not has_importable and not has_extras:
        raise ImportContentError("No valid SVG layers, text, or image content found.")

    frame = probe_frame(context, root, "GP") if has_extras else fallback_frame(root, "GP")
    gp_objects: List[bpy.types.Object] = []
    before_collections = _scene_collection_pointers(context.scene)

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

    warnings: List[str] = []
    text_objects = create_text_from_svg(
        context.scene,
        metadata["texts"],
        frame,
        warnings,
        outlines="GP" if settings.text_mode == "OUTLINES" else None,
    )
    image_objects = create_images_from_svg(
        context.scene,
        metadata["images"],
        settings.image_paste_mode,
        frame,
    )

    place_import_block(
        list(gp_objects) + list(text_objects) + list(image_objects),
        gp_objects,
        context.scene,
        settings.paste_location,
    )

    new_collections = _new_scene_collections(context.scene, before_collections)
    _move_objects_to_collection(
        context.scene,
        gp_objects if has_importable else [],
        "Pasted SVG",
        new_collections,
    )
    _move_objects_to_collection(context.scene, text_objects, "Pasted Text")
    _move_objects_to_collection(context.scene, image_objects, "Pasted Image")

    return {
        "objects": list(gp_objects) + list(text_objects) + list(image_objects),
        "grease_pencil": gp_objects,
        "texts": text_objects,
        "images": image_objects,
        "warnings": warnings,
        "frame": frame,
    }


def import_svg_as_curves(
    context: bpy.types.Context,
    root: ET.Element,
    settings: ImportSettings,
) -> Dict[str, object]:
    """Import a parsed SVG tree as Curve objects plus text/image extras."""
    plan = _prepare_import_plan(root, split_layers=True)
    metadata = plan["metadata"]
    has_extras = bool(metadata["texts"] or metadata["images"])

    frame = probe_frame(context, root, "CURVE") if has_extras else fallback_frame(root, "CURVE")
    curve_objects: List[bpy.types.Object] = []
    curve_batches: List[Tuple[Tuple[str, ...], List[bpy.types.Object]]] = []
    before_collections = _scene_collection_pointers(context.scene)

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

    scale_curve_objects(curve_objects, CURVE_SIZE_FACTOR)

    warnings: List[str] = []
    text_objects = create_text_from_svg(
        context.scene,
        metadata["texts"],
        frame,
        warnings,
        outlines="CURVE" if settings.text_mode == "OUTLINES" else None,
    )
    image_objects = create_images_from_svg(
        context.scene,
        metadata["images"],
        settings.image_paste_mode,
        frame,
    )

    # Stack before the block is centred, so the stack stays centred on the target.
    if settings.curve_stack_enabled:
        if settings.curve_stack_keep_groups:
            grouped_batches: Dict[Tuple[str, ...], List[bpy.types.Object]] = {}
            ordered_keys: List[Tuple[str, ...]] = []
            for group_key, batch_objects in curve_batches:
                if group_key not in grouped_batches:
                    grouped_batches[group_key] = []
                    ordered_keys.append(group_key)
                grouped_batches[group_key].extend(batch_objects)
            _stack_curve_batches(
                [(group_key, grouped_batches[group_key]) for group_key in ordered_keys],
                settings.curve_stack_axis,
                settings.curve_stack_depth,
            )
        else:
            _stack_curve_objects(
                curve_objects,
                settings.curve_stack_axis,
                settings.curve_stack_depth,
            )

    place_import_block(
        list(curve_objects) + list(text_objects) + list(image_objects),
        [],
        context.scene,
        settings.paste_location,
    )

    new_collections = _new_scene_collections(context.scene, before_collections)
    _move_objects_to_collection(context.scene, curve_objects, "Pasted SVG", new_collections)
    _move_objects_to_collection(context.scene, text_objects, "Pasted Text")
    _move_objects_to_collection(context.scene, image_objects, "Pasted Image")

    if not curve_objects and not text_objects and not image_objects:
        raise ImportContentError("No importable SVG content found.")

    return {
        "objects": list(curve_objects) + list(text_objects) + list(image_objects),
        "curves": curve_objects,
        "texts": text_objects,
        "images": image_objects,
        "warnings": warnings,
        "frame": frame,
    }


def report_warnings(operator: bpy.types.Operator, warnings: List[str], limit: int = 4) -> None:
    """Show import warnings (missing fonts, mixed text styles) without flooding the status bar."""
    unique = list(dict.fromkeys(warnings))
    for message in unique[:limit]:
        operator.report({"WARNING"}, message)
    if len(unique) > limit:
        operator.report({"WARNING"}, f"...and {len(unique) - limit} more import warnings")


def import_svg_root(
    context: bpy.types.Context,
    root: ET.Element,
    import_mode: str,
    settings: ImportSettings,
) -> Dict[str, object]:
    """Dispatch to the Grease Pencil or Curve pipeline."""
    if import_mode == "CURVE":
        return import_svg_as_curves(context, root, settings)
    return import_svg_as_grease_pencil(context, root, settings)
