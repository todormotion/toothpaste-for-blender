"""
Grease Pencil layer operations.

Layer naming, visibility, holdout, mask relations, layer groups and splitting a
GP object into one object per layer.
"""

from typing import Dict, List, Optional, Set, Tuple

import bpy

from .constants import CLIP_LAYER_PREFIX, GP_TYPE, MASK_LAYER_PREFIX
from .scene_utils import _link_object_like, _operator_supported_kwargs


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
