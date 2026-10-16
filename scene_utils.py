"""
Generic Blender scene helpers.

Temporary files, object/collection bookkeeping around importer calls, object
placement and stacking, plus small defensive property setters shared by the
Grease Pencil modules.
"""

import math
import os
import tempfile
from typing import Dict, List, Optional, Set, Tuple

import bpy
from mathutils import Matrix, Vector


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
    # Compare objects, not names: `target in children` raises on Blender's collection type.
    for child in list(children):
        if child == target:
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


def _object_world_bounds(
    obj: bpy.types.Object,
    depsgraph: bpy.types.Depsgraph,
) -> Optional[Tuple[Vector, Vector]]:
    """World-space bounding box of an object, or None when it has no extent."""
    if obj.type == "EMPTY":
        image = obj.data if getattr(obj, "empty_display_type", "") == "IMAGE" else None
        if image is None or not image.size[1]:
            return None
        # Mirror how Blender draws an Image Empty: the longer side is the display
        # size, the other side follows the image's aspect, placed by the offset.
        size = float(obj.empty_display_size)
        aspect = image.size[0] / image.size[1]
        width, height = (size, size / aspect) if aspect >= 1.0 else (size * aspect, size)
        offset_x, offset_y = obj.empty_image_offset
        local = [
            Vector((offset_x * width, offset_y * height, 0.0)),
            Vector(((offset_x + 1.0) * width, (offset_y + 1.0) * height, 0.0)),
        ]
        matrix = obj.matrix_world
    else:
        evaluated = obj.evaluated_get(depsgraph)
        local = [Vector(corner) for corner in evaluated.bound_box]
        matrix = evaluated.matrix_world

    points = [matrix @ corner for corner in local]
    if not points:
        return None
    low = Vector([min(point[axis] for point in points) for axis in range(3)])
    high = Vector([max(point[axis] for point in points) for axis in range(3)])
    return low, high


def lay_flat(objects: List[bpy.types.Object], native_gp_objects: List[bpy.types.Object]) -> None:
    """
    Lay Grease Pencil art down so everything faces up (+Z).

    Everything ToothPaste builds, and what the curve importer makes, already lies
    flat in the XY plane facing +Z, while Blender's Grease Pencil importer stands
    its art up in the XZ plane. Only that art needs turning, around the origin of
    the SVG canvas, which is where the content was built.
    """
    native_ids = {obj.as_pointer() for obj in native_gp_objects}
    lie_down = Matrix.Rotation(math.radians(-90.0), 4, "X")
    for obj in objects:
        if obj.as_pointer() in native_ids:
            obj.matrix_basis = lie_down @ obj.matrix_basis


def center_objects_on_target(
    objects: List[bpy.types.Object],
    scene: bpy.types.Scene,
    paste_mode: str,
) -> None:
    """
    Move the objects, keeping their layout, so the block's centre lands on the target.

    The block is centred on the ground plane; height keeps the content's own
    offsets (curve stacking) around the target's height.
    """
    valid = [obj for obj in objects if obj is not None]
    if not valid:
        return

    bpy.context.view_layer.update()
    depsgraph = bpy.context.evaluated_depsgraph_get()
    boxes = [box for box in (_object_world_bounds(obj, depsgraph) for obj in valid) if box is not None]
    if not boxes:
        return

    low = Vector([min(box[0][axis] for box in boxes) for axis in range(3)])
    high = Vector([max(box[1][axis] for box in boxes) for axis in range(3)])
    center = (low + high) * 0.5
    target = Vector(_paste_target_location(scene, paste_mode))

    delta = target - center
    delta[2] = target[2]
    for obj in valid:
        obj.location = obj.location + delta


def place_import_block(
    objects: List[bpy.types.Object],
    native_gp_objects: List[bpy.types.Object],
    scene: bpy.types.Scene,
    paste_mode: str,
) -> None:
    """Lay everything flat, then centre it on the paste target."""
    valid = [obj for obj in objects if obj is not None]
    lay_flat(valid, native_gp_objects)
    center_objects_on_target(valid, scene, paste_mode)


def scale_curve_objects(objects: List[bpy.types.Object], factor: float) -> None:
    """
    Scale curve geometry about the origin, in the curve data.

    The object scale stays (1, 1, 1), as if Object > Apply > Scale had been used.
    """
    done: Set[int] = set()
    for obj in objects:
        if obj is None or obj.type != "CURVE" or obj.data.as_pointer() in done:
            continue
        done.add(obj.data.as_pointer())
        for spline in obj.data.splines:
            for point in spline.bezier_points:
                point.handle_left = point.handle_left * factor
                point.handle_right = point.handle_right * factor
                point.co = point.co * factor
            for point in spline.points:
                x, y, z, w = point.co
                point.co = (x * factor, y * factor, z * factor, w)
        obj.data.update_tag()


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
