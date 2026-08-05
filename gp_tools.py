"""
Grease Pencil tools.

Selecting or deselecting the strokes that live on the active layer. The buttons
for this sit under the layer list in Object Data Properties; see
`draw_gp_layer_selection` in ui_panels.
"""

from typing import List, Optional

import bpy

from .constants import GP_TYPE


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
