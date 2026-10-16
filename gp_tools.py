"""
Grease Pencil tools.

- Selecting or deselecting the strokes on the active layer (buttons live in
  Object Data Properties > Layers, see `draw_gp_layer_selection` in ui_panels).
- Editing the selected strokes from the ToothPaste panel: stroke type,
  thickness and direction.

Thickness is written straight to the stroke data (rather than through an
operator) so it can run from a property update callback, which has no reliable
operator context.
"""

from typing import Iterator, List, Optional

import bpy

from .constants import GP_TYPE

EDIT_MODES = {"EDIT_GREASE_PENCIL", "EDIT_GPENCIL"}


def active_gp_object(context: bpy.types.Context) -> Optional[bpy.types.Object]:
    """Return the active object when it is a Grease Pencil object."""
    obj = getattr(context, "active_object", None)
    if obj is not None and getattr(obj, "type", "") == GP_TYPE:
        return obj
    return None


def _active_layer(gp_obj: bpy.types.Object) -> Optional[object]:
    """Return the active layer of a Grease Pencil object."""
    layers = getattr(getattr(gp_obj, "data", None), "layers", None)
    if layers is None:
        return None
    return getattr(layers, "active", None)


def _set_drawing_selection(drawing: object, selected: bool) -> int:
    """Set the selection attribute of every stroke in a drawing."""
    attributes = getattr(drawing, "attributes", None)
    if attributes is None:
        return 0

    attribute = None
    try:
        attribute = attributes.get(".selection")
    except Exception:
        attribute = None

    if attribute is None:
        try:
            attribute = attributes.new(".selection", "BOOLEAN", "CURVE")
        except Exception:
            attribute = None
    if attribute is None:
        return 0

    data_type = str(getattr(attribute, "data_type", "BOOLEAN"))
    if data_type == "BOOLEAN":
        value = bool(selected)
    else:
        value = 1.0 if selected else 0.0

    changed = 0
    for item in getattr(attribute, "data", []):
        try:
            item.value = value
        except Exception:
            continue
        changed += 1
    return changed


def set_layer_selection(gp_obj: bpy.types.Object, layer: object, selected: bool) -> int:
    """Select or deselect all strokes on one Grease Pencil layer."""
    frames = getattr(layer, "frames", None)
    if not frames:
        return 0

    changed = 0
    for frame in frames:
        drawing = getattr(frame, "drawing", None)
        if drawing is None:
            continue
        changed += _set_drawing_selection(drawing, selected)

    if changed:
        try:
            gp_obj.data.update_tag()
        except Exception:
            pass
        try:
            gp_obj.update_tag()
        except Exception:
            pass
    return changed


class TOOTHPASTE_OT_gp_select_layer_strokes(bpy.types.Operator):
    """Select or deselect every stroke on the active Grease Pencil layer."""

    bl_idname = "toothpaste.gp_select_layer_strokes"
    bl_label = "Select Layer Strokes"
    bl_description = "Select or deselect all strokes on the active Grease Pencil layer"
    bl_options = {"REGISTER", "UNDO"}

    action: bpy.props.EnumProperty(
        name="Action",
        items=[
            ("SELECT", "Select", "Select all strokes on the active layer"),
            ("DESELECT", "Deselect", "Deselect all strokes on the active layer"),
        ],
        default="SELECT",
    )

    @classmethod
    def poll(cls, context: bpy.types.Context):
        gp_obj = active_gp_object(context)
        return gp_obj is not None and _active_layer(gp_obj) is not None

    def execute(self, context: bpy.types.Context):
        gp_obj = active_gp_object(context)
        layer = _active_layer(gp_obj) if gp_obj else None
        if gp_obj is None or layer is None:
            self.report({"WARNING"}, "No active Grease Pencil layer.")
            return {"CANCELLED"}

        selected = self.action == "SELECT"
        changed = set_layer_selection(gp_obj, layer, selected)
        if not changed:
            self.report({"WARNING"}, f"No strokes on layer '{getattr(layer, 'name', '')}'.")
            return {"CANCELLED"}

        verb = "Selected" if selected else "Deselected"
        self.report({"INFO"}, f"{verb} {changed} stroke(s) on '{getattr(layer, 'name', '')}'.")
        return {"FINISHED"}


# --------------------------------------------------------------------------- #
# editing the selection from the ToothPaste panel
# --------------------------------------------------------------------------- #

def is_gp_edit_mode(context: bpy.types.Context) -> bool:
    """Report whether the context is in Grease Pencil edit mode."""
    return str(getattr(context, "mode", "")) in EDIT_MODES


def _editable_drawings(gp_obj: bpy.types.Object, scene: bpy.types.Scene) -> Iterator[object]:
    """Drawings Blender would edit now: current frame of every visible, unlocked layer."""
    for layer in getattr(gp_obj.data, "layers", []):
        if getattr(layer, "hide", False) or getattr(layer, "lock", False):
            continue
        try:
            frame = layer.current_frame()
        except Exception:
            frame = None
        drawing = getattr(frame, "drawing", None) if frame is not None else None
        if drawing is not None:
            yield drawing


def apply_thickness(gp_obj: bpy.types.Object, scene: bpy.types.Scene, thickness: float) -> int:
    """
    Give every selected point the same thickness.

    Matches Blender's own Set Uniform Thickness: a point's radius is half of
    the thickness.
    """
    radius = max(float(thickness), 0.0) * 0.5
    changed = 0
    for drawing in _editable_drawings(gp_obj, scene):
        for stroke in drawing.strokes:
            for point in stroke.points:
                if point.select:
                    point.radius = radius
                    changed += 1
    _refresh(gp_obj)
    return changed


def _refresh(gp_obj: bpy.types.Object) -> None:
    try:
        gp_obj.data.update_tag()
    except Exception:
        pass
    try:
        for window in bpy.context.window_manager.windows:
            for area in window.screen.areas:
                if area.type == "VIEW_3D":
                    area.tag_redraw()
    except Exception:
        pass


def _edit_target(context: bpy.types.Context) -> Optional[bpy.types.Object]:
    """The Grease Pencil object to edit, or None outside Grease Pencil edit mode."""
    gp_obj = active_gp_object(context)
    if gp_obj is None or not is_gp_edit_mode(context):
        return None
    return gp_obj


def on_thickness_changed(scene: bpy.types.Scene, context: bpy.types.Context) -> None:
    gp_obj = _edit_target(context)
    if gp_obj is not None:
        apply_thickness(gp_obj, scene, scene.toothpaste_gp_thickness)


class TOOTHPASTE_OT_gp_set_stroke_type(bpy.types.Operator):
    """Set the stroke type of the selected Grease Pencil strokes."""

    bl_idname = "toothpaste.gp_set_stroke_type"
    bl_label = "Set Stroke Type"
    bl_description = "Make the selected strokes show their stroke, their fill, or both (same as Stroke > Set Stroke Type)"
    bl_options = {"REGISTER", "UNDO"}

    stroke_type: bpy.props.EnumProperty(
        name="Type",
        items=[
            ("STROKE", "Stroke", "Selected strokes show their stroke only"),
            ("FILL", "Fill", "Selected strokes show their fill only"),
            ("BOTH", "Both", "Selected strokes show stroke and fill"),
        ],
        default="BOTH",
    )

    @classmethod
    def poll(cls, context: bpy.types.Context):
        return _edit_target(context) is not None

    def execute(self, context: bpy.types.Context):
        operator = getattr(bpy.ops.grease_pencil, "set_stroke_type", None)
        if operator is None:
            self.report({"ERROR"}, "This Blender build has no Set Stroke Type operator.")
            return {"CANCELLED"}
        try:
            result = operator(type=self.stroke_type)
        except Exception as exc:
            self.report({"ERROR"}, f"Failed to set stroke type: {exc}")
            return {"CANCELLED"}
        return {"FINISHED"} if "FINISHED" in result else {"CANCELLED"}


class TOOTHPASTE_OT_gp_reverse_direction(bpy.types.Operator):
    """Reverse the point order of the selected Grease Pencil strokes."""

    bl_idname = "toothpaste.gp_reverse_direction"
    bl_label = "Reverse Direction"
    bl_description = "Reverse the direction of the selected strokes (same as Stroke > Switch Direction)"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context: bpy.types.Context):
        return _edit_target(context) is not None

    def execute(self, context: bpy.types.Context):
        operator = getattr(bpy.ops.grease_pencil, "stroke_switch_direction", None)
        if operator is None:
            self.report({"ERROR"}, "This Blender build has no Switch Direction operator.")
            return {"CANCELLED"}
        try:
            result = operator()
        except Exception as exc:
            self.report({"ERROR"}, f"Failed to reverse direction: {exc}")
            return {"CANCELLED"}
        return {"FINISHED"} if "FINISHED" in result else {"CANCELLED"}
