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

from .constants import GP_TYPE
from .exceptions import ImportContentError
from .gp_layers import _apply_gp_masks_and_holdout
from .gp_materials import _apply_gp_materials, _ensure_gp_draw_uses_material_color
from .obj_images import create_images_from_svg
from .obj_text import create_text_from_svg
from .properties import ImportSettings
from .scene_utils import (
    _move_objects_to_collection,
    _move_objects_to_target,
    _new_scene_collections,
    _new_scene_objects,
    _operator_supported_kwargs,
    _scene_collection_pointers,
    _scene_object_pointers,
    _stack_curve_batches,
    _stack_curve_objects,
    _write_temp_svg,
)
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


def _place_created_objects(
    scene: bpy.types.Scene,
    main_objects: List[bpy.types.Object],
    text_objects: List[bpy.types.Object],
    paste_location: str,
) -> None:
    """
    Move created objects to the paste target.

    Geometry and images move together so their relative layout survives. Text is
    centered on its own, because SVG text coordinates routinely place it far
    away from the artwork it belongs to.
    """
    _move_objects_to_target(main_objects, scene, paste_location)
    _move_objects_to_target(text_objects, scene, paste_location)


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

    text_objects = create_text_from_svg(context.scene, metadata["texts"])
    image_objects = create_images_from_svg(
        context.scene,
        metadata["images"],
        settings.image_paste_mode,
    )

    _place_created_objects(
        context.scene,
        list(gp_objects) + list(image_objects),
        text_objects,
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
    }


def import_svg_as_curves(
    context: bpy.types.Context,
    root: ET.Element,
    settings: ImportSettings,
) -> Dict[str, object]:
    """Import a parsed SVG tree as Curve objects plus text/image extras."""
    plan = _prepare_import_plan(root, split_layers=True)
    metadata = plan["metadata"]

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

    text_objects = create_text_from_svg(context.scene, metadata["texts"])
    image_objects = create_images_from_svg(
        context.scene,
        metadata["images"],
        settings.image_paste_mode,
    )

    _place_created_objects(
        context.scene,
        list(curve_objects) + list(image_objects),
        text_objects,
        settings.paste_location,
    )

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
    }


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
