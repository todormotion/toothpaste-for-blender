"""
Grease Pencil object operators that are not part of the paste flow.
"""

import bpy

from .gp_layers import is_valid_gp_object, split_gp_object_layers


class GP_OT_SplitLayers(bpy.types.Operator):
    """Split active GP object layers into separate objects without bpy.ops duplicate."""

    bl_idname = "gpencil.split_layers_into_objects"
    bl_label = "Split layers into objects"
    bl_description = "Split selected Grease Pencil layers into separate objects"

    @classmethod
    def poll(cls, context: bpy.types.Context):
        return is_valid_gp_object(context.active_object)

    def execute(self, context: bpy.types.Context):
        source_obj = context.active_object
        if len(source_obj.data.layers) < 2:
            self.report({"WARNING"}, "Selected Grease Pencil object contains fewer than 2 layers.")
            return {"CANCELLED"}

        new_objects = split_gp_object_layers(source_obj, context.scene)
        if not new_objects:
            self.report({"ERROR"}, "Failed to split layers.")
            return {"CANCELLED"}

        self.report({"INFO"}, f"Created {len(new_objects)} objects from layers.")
        return {"FINISHED"}
