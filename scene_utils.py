"""
Generic Blender scene helpers.

Temporary files, object/collection bookkeeping around importer calls, object
placement and stacking, plus small defensive property setters shared by the
Grease Pencil modules.
"""

import os
import tempfile
from typing import Dict, List, Optional, Set, Tuple

import bpy


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


def _link_object_like(source_obj: bpy.types.Object, new_obj: bpy.types.Object, scene: bpy.types.Scene) -> None:
    """Link a new object to the same collections as source object."""
    collections = list(source_obj.users_collection)
    if not collections:
        scene.collection.objects.link(new_obj)
        return
    for collection in collections:
        collection.objects.link(new_obj)


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
