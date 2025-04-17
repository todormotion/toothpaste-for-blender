bl_info = {
    "name": "ToothPaste for Blender",
    "author": "Cosmonavt",
    "version": (0, 9, 2),
    "blender": (4, 4, 0),
    "category": "Import-Export",
    "location": "View3D > Sidebar > ToothPaste",
    "description": "Paste SVG from clipboard as Grease Pencil, with option to split layers into separate objects.",
}

import bpy
import os
import tempfile
import xml.etree.ElementTree as ET


def get_clipboard_svg():
    svg = bpy.context.window_manager.clipboard
    if "<svg" not in svg:
        raise ValueError("Clipboard does not contain valid SVG data.")
    return svg

def parse_svg(svg_data):
    ns = {
        "svg": "http://www.w3.org/2000/svg",
        "inkscape": "http://www.inkscape.org/namespaces/inkscape"
    }
    ET.register_namespace('', ns["svg"])
    root = ET.fromstring(svg_data)

    mask_defs = {el.attrib["id"]: el for el in root.findall(".//svg:mask", ns) if "id" in el.attrib}
    clip_defs = {el.attrib["id"]: el for el in root.findall(".//svg:clipPath", ns) if "id" in el.attrib}

    layers = []
    for idx, group in enumerate(root.findall("svg:g", ns)):
        name = group.attrib.get("id") or group.attrib.get("{http://www.inkscape.org/namespaces/inkscape}label") or f"Layer_{idx+1}"
        paths = [elem.attrib.get("d") for elem in group.findall(".//svg:path", ns) if elem.attrib.get("d")]
        if not paths:
            continue

        masks = []
        mask_ref = group.attrib.get("mask")
        if mask_ref and mask_ref.startswith("url(#"):
            mask_id = mask_ref[5:-1]
            if mask_id in mask_defs:
                masks.append(mask_id)

        group_xml = ET.tostring(group, encoding='unicode')
        layers.append({
            "name": name,
            "paths": paths,
            "masks": masks,
            "xml": group_xml
        })

    return layers, mask_defs, clip_defs

def is_valid_gp_object(obj):
    return obj and obj.type == 'GREASEPENCIL' and hasattr(obj.data, "layers") and len(obj.data.layers) > 0


def split_gp_layers_to_objects(source_obj):
    source_data = source_obj.data
    if len(source_data.layers) < 2:
        return

    for layer in list(source_data.layers):
        bpy.ops.object.select_all(action='DESELECT')
        source_obj.select_set(True)
        bpy.context.view_layer.objects.active = source_obj
        bpy.ops.object.duplicate()
        new_obj = bpy.context.active_object
        new_obj.name = layer.name
        new_obj.data.name = layer.name

        layers_to_remove = [l for l in new_obj.data.layers if l.name != layer.name]
        for l in layers_to_remove:
            new_obj.data.layers.remove(l)

        used_mats = {s.material_index for f in new_obj.data.layers[0].frames for s in f.strokes}
        for i in reversed(range(len(new_obj.data.materials))):
            if i not in used_mats:
                new_obj.data.materials.pop(index=i)

class TOOTHPASTE_OT_paste_svg(bpy.types.Operator):
    bl_idname = "toothpaste.paste_svg"
    bl_label = "Paste SVG (Single Object)"
    bl_description = "Paste SVG code from clipboard into Blender as a Grease Pencil (v3) object."

    def execute(self, context):
        try:
            svg_data = get_clipboard_svg()
            layers, mask_defs, clip_defs = parse_svg(svg_data)
            if not layers:
                self.report({'ERROR'}, "No valid SVG layers found.")
                return {'CANCELLED'}

            tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".svg", mode='w', encoding='utf-8')
            tmp.write(svg_data)
            tmp.close()
            bpy.ops.wm.grease_pencil_import_svg(filepath=tmp.name)
            os.remove(tmp.name)

            gp_obj = context.active_object
            if gp_obj and gp_obj.type == 'GPENCIL':
                for i, layer in enumerate(gp_obj.data.layers):
                    if i < len(layers):
                        layer.info = layers[i]["name"]

            self.report({'INFO'}, "SVG imported successfully.")
        except Exception as e:
            self.report({'ERROR'}, str(e))
        return {'FINISHED'}

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
        # Hide the original object after splitting
        source_obj.hide_set(True)
        source_obj.hide_render = True
        return {'FINISHED'}

class TOOTHPASTE_PT_panel(bpy.types.Panel):
    bl_label = "ToothPaste"
    bl_idname = "TOOTHPASTE_PT_panel"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'ToothPaste'

    def draw(self, context):
        layout = self.layout
        layout.operator("toothpaste.paste_svg", icon="PASTEDOWN")
        layout.operator("gpencil.split_layers_into_objects", icon="GP_SELECT_POINTS")

classes = (
    TOOTHPASTE_OT_paste_svg,
    GP_OT_SplitLayers,
    TOOTHPASTE_PT_panel,
)

def register():
    for cls in classes:
        bpy.utils.register_class(cls)

def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)

if __name__ == "__main__":
    register()
