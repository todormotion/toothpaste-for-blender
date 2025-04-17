bl_info = {
    "name": "Split Grease Pencil Layers (GPv3)",
    "author": "ChatGPT & Cosmonavt",
    "version": (1, 2),
    "blender": (4, 4, 0),
    "location": "3D View > Sidebar > Splitting tools",
    "description": "Splits GPv3 object layers into separate objects",
    "category": "Grease Pencil",
}

import bpy

def is_valid_gp_object(obj):
    return obj and obj.type == 'GREASEPENCIL' and hasattr(obj.data, "layers") and len(obj.data.layers) > 0

class GP_OT_SplitLayers(bpy.types.Operator):
    bl_idname = "gpencil.split_layers_into_objects"
    bl_label = "Split layers into objects"
    bl_description = "Split the selected Grease Pencil object's layers into separate objects"

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return is_valid_gp_object(obj)

    def execute(self, context):
        source_obj = context.active_object
        source_data = source_obj.data

        if len(source_data.layers) < 2:
            self.report({'WARNING'}, "Selected Grease Pencil object contains fewer than 2 layers.")
            return {'CANCELLED'}

        for layer in source_data.layers:
            # Duplicate the object
            bpy.ops.object.select_all(action='DESELECT')
            source_obj.select_set(True)
            context.view_layer.objects.active = source_obj
            bpy.ops.object.duplicate()
            new_obj = context.active_object
            new_obj.name = layer.name
            new_obj.data.name = layer.name

            # Remove all layers except the one we want
            layers_to_remove = [l for l in new_obj.data.layers if l.name != layer.name]
            for l in layers_to_remove:
                new_obj.data.layers.remove(l)

            # (Optional) Remove unused materials
            used_mats = {s.material_index for f in new_obj.data.layers[0].frames for s in f.strokes}
            for i in reversed(range(len(new_obj.data.materials))):
                if i not in used_mats:
                    new_obj.data.materials.pop(index=i)


        return {'FINISHED'}

class GP_PT_SplitLayerPanel(bpy.types.Panel):
    bl_label = "Grease Pencil Splitter"
    bl_idname = "GP_PT_split_layer_panel"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "Splitting tools"

    def draw(self, context):
        layout = self.layout
        obj = context.active_object

        if is_valid_gp_object(obj):
            layout.operator("gpencil.split_layers_into_objects", icon="GP_SELECT_POINTS")
        else:
            layout.label(text="Select a Grease Pencil object.", icon='INFO')

classes = (
    GP_OT_SplitLayers,
    GP_PT_SplitLayerPanel,
)

def register():
    for cls in classes:
        bpy.utils.register_class(cls)

def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)

if __name__ == "__main__":
    register()
