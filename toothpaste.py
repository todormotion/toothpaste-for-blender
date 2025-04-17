
bl_info = {
    "name": "ToothPaste for Blender",
    "author": "Cosmonavt",
    "version": (2, 1, 0),
    "blender": (4, 4, 0),
    "category": "Import-Export",
    "location": "View3D > Sidebar > ToothPaste",
    "description": "Paste SVG from clipboard as layered Grease Pencil with optional split into multiple objects.",
}

import bpy
import os
import tempfile
import xml.etree.ElementTree as ET
from bpy.props import BoolProperty
from bpy.types import Operator, Panel, AddonPreferences

# --- Core SVG Clipboard Import Logic (from __init__.py, adapted) ---

svg_string = """<?xml version="1.0" encoding="UTF-8" standalone="no"?>
<!DOCTYPE svg PUBLIC "-//W3C//DTD SVG 1.1//EN" "http://www.w3.org/Graphics/SVG/1.1/DTD/svg11.dtd">
<svg width="957px" height="570px" version="1.1" xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" xml:space="preserve" xmlns:serif="http://www.serif.com/" style="fill-rule:evenodd;clip-rule:evenodd;stroke-linejoin:round;stroke-miterlimit:2;">
    <g transform="matrix(1,0,0,1,-61.5093,-1866.07)">
        <g transform="matrix(1,0,-0.45568,1,116.024,1610.95)">
            <path d="M868.795,607.303L868.795,824.88L763.012,824.88L763.012,255.12L868.795,255.12L868.795,440.04L763.015,375.167L868.795,607.303Z" style="fill:white;"/>
        </g>
        <g transform="matrix(1,0,-0.45568,1,-17.6466,1610.95)">
            <path d="M868.795,255.12L868.795,824.88L805.789,824.88L549.309,255.12L655.092,255.12L763.012,494.86L763.012,255.12L868.795,255.12Z" style="fill:white;"/>
        </g>
        <g transform="matrix(-1,0,0.45568,-1,814.051,2690.95)">
            <path d="M868.795,255.12L868.795,824.88L805.789,824.88L549.309,255.12L655.092,255.12L763.012,494.86L763.012,255.12L868.795,255.12Z" style="fill:white;"/>
        </g>
        <g transform="matrix(1,0,0,1,0,704.235)">
            <path d="M878.828,1427.21L802.607,1362.33L893.971,1161.83L1018.49,1161.83L1018.49,1514.46L763.385,1731.59L634.343,1731.59L708.083,1569.77L708.083,1668.74L807.216,1584.36L807.149,1584.51L935.418,1475.33L878.828,1427.21Z" style="fill:white;"/>
        </g>
        <g transform="matrix(1.53168e-16,2.50142,-1.47013,9.00197e-17,2963.3,197.865)">
            <path d="M792.235,1402.96L849.575,1517.64L734.895,1517.64L792.235,1402.96Z" style="fill:rgb(195,247,58);"/>
        </g>
    </g>
</svg>
"""

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

    # Step 1: Extract mask and clipPath definitions
    mask_defs = {el.attrib["id"]: el for el in root.findall(".//svg:mask", ns) if "id" in el.attrib}
    clip_defs = {el.attrib["id"]: el for el in root.findall(".//svg:clipPath", ns) if "id" in el.attrib}

    # Step 2: Process top-level groups only
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


    return layers, mask_defs, clip_defs



# Placeholder logic for the actual import functionality (should match working logic in original __init__)
def import_svg_as_gp(svg_data, split_layers=False):
    root = parse_svg(svg_data)

    # Here should be the logic for creating Grease Pencil objects based on the SVG
    # For example purposes, we'll simulate it:
    if split_layers:
        print("Importing each top-level layer as separate GP object...")
    else:
        print("Importing all layers into a single GP object...")

    # Simulate the import process
    print("Imported SVG successfully.")

# --- Operator Definition ---

class TOOTHPASTE_OT_paste_svg(Operator):
    bl_idname = "toothpaste.paste_svg"
    bl_label = "Paste SVG from Clipboard"
    bl_description = "Paste SVG content from the clipboard and convert it to Grease Pencil"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        try:
            svg_data = context.window_manager.clipboard
            if "<svg" not in svg_data:
                self.report({'ERROR'}, "Clipboard does not contain SVG data.")
                return {'CANCELLED'}

            layers, mask_defs, clip_defs = parse_svg(svg_data)
            if not layers:
                self.report({'ERROR'}, "No valid SVG layers found.")
                return {'CANCELLED'}

            # Warn about multiple masks
            for layer in layers:
                if len(layer['masks']) > 1:
                    self.report({'WARNING'}, f"Layer '{layer['name']}' uses multiple masks. Only the first will be applied.")

            split = context.scene.toothpaste_split_layers
            if split:
                # Create individual SVGs per top-level group and preserve paths as full XML
                for i, layer in enumerate(layers):
                    group_svg = f"""<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" version="1.1">
    {layer['xml']}
</svg>"""
                    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".svg", mode='w', encoding='utf-8')
                    tmp.write(group_svg)
                    tmp.close()
                    bpy.ops.wm.grease_pencil_import_svg(filepath=tmp.name)
                    os.remove(tmp.name)

                    gp_obj = context.active_object
                    if gp_obj and gp_obj.type == 'GPENCIL':
                        gp_obj.name = layer["name"]
                        if gp_obj.data.layers:
                            gp_obj.data.layers[0].info = layer["name"]

            else:
                # Standard import
                tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".svg", mode='w', encoding='utf-8')
                tmp.write(svg_data)
                tmp.close()
                tmp_path = tmp.name

                bpy.ops.wm.grease_pencil_import_svg(filepath=tmp_path)
                os.remove(tmp_path)

                gp_obj = context.active_object
                if gp_obj and gp_obj.type == 'GPENCIL':
                    for i, layer in enumerate(gp_obj.data.layers):
                        if i < len(layers):
                            layer.info = layers[i]["name"]

            self.report({'INFO'}, "SVG imported successfully.")
        except Exception as e:
            self.report({'ERROR'}, str(e))
        return {'FINISHED'}

# --- UI Panel ---

class TOOTHPASTE_PT_panel(Panel):
    bl_label = "ToothPaste"
    bl_idname = "TOOTHPASTE_PT_panel"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'ToothPaste'

    def draw(self, context):
        layout = self.layout
        layout.prop(context.scene, "toothpaste_split_layers")
        layout.operator("toothpaste.paste_svg", text="Paste SVG")

# --- Register/Unregister ---

classes = (TOOTHPASTE_OT_paste_svg, TOOTHPASTE_PT_panel)

def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    bpy.types.Scene.toothpaste_split_layers = BoolProperty(
        name="Split Layers",
        description="Split SVG layers into separate GP objects",
        default=False
    )

def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
    del bpy.types.Scene.toothpaste_split_layers

if __name__ == "__main__":
    register()
