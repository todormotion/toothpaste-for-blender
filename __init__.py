"""
ToothPaste for Blender - SVG Clipboard to Grease Pencil Converter

This addon allows users to paste SVG code from clipboard directly into Blender
as Grease Pencil v3 objects. It also provides tools for managing GP layers.

Features:
- Import SVG from clipboard as Grease Pencil objects
- Convert SVG shapes (rect, circle, ellipse, line, polygon, polyline) to paths
- Split Grease Pencil layers into separate objects
- Support for Inkscape and other SVG editors

Limitations:
- No support for SVG transforms (rotation, scale, skew)
- No support for gradients, patterns, or advanced styling
- No support for text elements
- Masks and clips are not imported

Requirements:
- Blender 4.2 or later (Grease Pencil v3)

Author: Cosmonavt
License: GPL-3.0-or-later
"""

bl_info = {
    "name": "ToothPaste for Blender",
    "author": "Cosmonavt",
    "version": (1, 0, 0),
    "blender": (4, 2, 0),
    "category": "Import-Export",
    "location": "View3D > Sidebar > ToothPaste",
    "description": "Paste SVG from clipboard as Grease Pencil, with option to split layers into separate objects.",
}

import bpy
import os
import tempfile
import xml.etree.ElementTree as ET

# Import custom exceptions - handle both package and standalone modes
try:
    from .exceptions import InvalidSVGError, ClipboardError
except ImportError:
    # Standalone mode (for testing)
    from exceptions import InvalidSVGError, ClipboardError

# Constants
GP_TYPE = 'GREASEPENCIL'  # Grease Pencil v3 (Blender 4.2+)


def validate_svg_string(svg_string):
    """
    Validate that a string contains valid SVG XML.

    Args:
        svg_string: String to validate

    Returns:
        True if valid SVG

    Raises:
        InvalidSVGError: If string is empty, not valid XML, or not SVG
    """
    # Check for empty or whitespace-only strings
    if not svg_string or not svg_string.strip():
        raise InvalidSVGError("SVG data is empty")

    # Try to parse as XML
    try:
        root = ET.fromstring(svg_string)
    except ET.ParseError as e:
        raise InvalidSVGError(f"Invalid XML format: {str(e)}")

    # Check if root element is SVG
    # Handle both with and without namespace
    tag = root.tag.split('}')[-1]  # Remove namespace if present
    if tag.lower() != 'svg':
        raise InvalidSVGError(f"Root element is not SVG (found: {tag})")

    return True


def get_clipboard_svg():
    """
    Get SVG data from clipboard and validate it.

    Returns:
        Valid SVG string from clipboard

    Raises:
        ClipboardError: If clipboard doesn't contain valid SVG
    """
    try:
        svg = bpy.context.window_manager.clipboard
    except Exception as e:
        raise ClipboardError(f"Failed to access clipboard: {str(e)}")

    # Validate the SVG
    try:
        validate_svg_string(svg)
    except InvalidSVGError as e:
        raise ClipboardError(f"Clipboard does not contain valid SVG: {str(e)}")

    return svg


def convert_shape_to_path(element):
    """
    Convert SVG shape elements to SVG path data.

    Supports conversion of:
    - rect: Rectangle
    - circle: Circle
    - ellipse: Ellipse
    - line: Line
    - polyline: Polyline (open path)
    - polygon: Polygon (closed path)

    Args:
        element: XML element representing an SVG shape

    Returns:
        SVG path data string, or None if shape type is unsupported

    Note:
        Attributes default to 0 if missing. Does not handle transforms.
    """
    tag = element.tag.split('}')[-1]
    if tag == 'rect':
        x = float(element.get('x', 0))
        y = float(element.get('y', 0))
        width = float(element.get('width', 0))
        height = float(element.get('height', 0))
        d = f"M{x},{y} h{width} v{height} h{-width} Z"
        return d

    elif tag == 'circle':
        cx = float(element.get('cx', 0))
        cy = float(element.get('cy', 0))
        r = float(element.get('r', 0))
        d = f"M{cx - r},{cy} A{r},{r} 0 1,0 {cx + r},{cy} A{r},{r} 0 1,0 {cx - r},{cy} Z"
        return d

    elif tag == 'ellipse':
        cx = float(element.get('cx', 0))
        cy = float(element.get('cy', 0))
        rx = float(element.get('rx', 0))
        ry = float(element.get('ry', 0))
        d = f"M{cx - rx},{cy} A{rx},{ry} 0 1,0 {cx + rx},{cy} A{rx},{ry} 0 1,0 {cx - rx},{cy} Z"
        return d

    elif tag == 'line':
        x1 = float(element.get('x1', 0))
        y1 = float(element.get('y1', 0))
        x2 = float(element.get('x2', 0))
        y2 = float(element.get('y2', 0))
        d = f"M{x1},{y1} L{x2},{y2}"
        return d

    elif tag == 'polyline':
        points = element.get('points', '').strip().replace(',', ' ')
        coords = points.split()
        if len(coords) < 4:
            return None
        d = f"M{coords[0]},{coords[1]}" + "".join(f" L{coords[i]},{coords[i+1]}" for i in range(2, len(coords), 2))
        return d

    elif tag == 'polygon':
        points = element.get('points', '').strip().replace(',', ' ')
        coords = points.split()
        if len(coords) < 4:
            return None
        d = f"M{coords[0]},{coords[1]}" + "".join(f" L{coords[i]},{coords[i+1]}" for i in range(2, len(coords), 2)) + " Z"
        return d

    return None

def parse_svg(svg_data):
    """
    Parse SVG data and extract layer information.

    Args:
        svg_data: Valid SVG XML string

    Returns:
        List of layer dictionaries, each containing:
        - name: Layer name (from id or inkscape:label)
        - paths: List of path data strings

    Note:
        Only parses <g> elements as layers. Shapes are converted to paths.
        Masks, clips, and transforms are not currently supported.
    """
    ns = {
        "svg": "http://www.w3.org/2000/svg",
        "inkscape": "http://www.inkscape.org/namespaces/inkscape"
    }
    ET.register_namespace('', ns["svg"])
    root = ET.fromstring(svg_data)

    layers = []
    for idx, group in enumerate(root.findall("svg:g", ns)):
        name = group.attrib.get("id") or group.attrib.get("{http://www.inkscape.org/namespaces/inkscape}label") or f"Layer_{idx+1}"
        paths = []
        for elem in group.iter():
            tag = elem.tag.split('}')[-1]
            if tag == 'path':
                d = elem.attrib.get("d")
                if d:
                    paths.append(d)
            elif tag in ['rect', 'circle', 'ellipse', 'line', 'polyline', 'polygon']:
                d = convert_shape_to_path(elem)
                if d:
                    paths.append(d)

        if not paths:
            continue

        layers.append({
            "name": name,
            "paths": paths
        })

    return layers

def import_svg_via_tempfile(svg_data):
    """
    Import SVG data via temporary file with proper cleanup.

    Args:
        svg_data: Valid SVG string to import

    Returns:
        The created Grease Pencil object (or None if import failed)

    Raises:
        Any exceptions from the Blender import operation
    """
    tmp_path = None
    try:
        # Create temporary file
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".svg", mode='w', encoding='utf-8')
        tmp_path = tmp.name
        tmp.write(svg_data)
        tmp.close()

        # Import the SVG
        bpy.ops.wm.grease_pencil_import_svg(filepath=tmp_path)

        # Return the created object
        return bpy.context.active_object

    finally:
        # Always clean up temp file, even if import fails
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except Exception as e:
                # Log but don't raise - cleanup failure shouldn't break the operator
                print(f"Warning: Failed to remove temp file {tmp_path}: {e}")


def is_valid_gp_object(obj):
    """Check if object is a valid Grease Pencil v3 object with layers."""
    if not obj:
        return False
    if obj.type != GP_TYPE:
        return False
    if not hasattr(obj.data, "layers"):
        return False
    if len(obj.data.layers) == 0:
        return False
    return True


class TOOTHPASTE_OT_paste_svg(bpy.types.Operator):
    """
    Operator to paste SVG from clipboard as Grease Pencil object.

    This operator:
    1. Retrieves SVG data from clipboard
    2. Validates the SVG format
    3. Parses SVG layers
    4. Imports as Grease Pencil v3 object via Blender's built-in importer
    5. Attempts to preserve layer names from SVG

    Limitations:
    - Only <g> elements are treated as layers
    - Transforms, masks, and clips are not supported
    - Some GP v3 API features may vary across Blender versions
    """
    bl_idname = "toothpaste.paste_svg"
    bl_label = "Paste SVG (Single Object)"
    bl_description = "Paste SVG code from clipboard into Blender as a Grease Pencil (v3) object."

    def execute(self, context):
        try:
            svg_data = get_clipboard_svg()
        except ClipboardError as e:
            self.report({'ERROR'}, str(e))
            return {'CANCELLED'}

        try:
            layers = parse_svg(svg_data)
            if not layers:
                self.report({'ERROR'}, "No valid SVG layers found.")
                return {'CANCELLED'}
        except InvalidSVGError as e:
            self.report({'ERROR'}, f"Invalid SVG: {str(e)}")
            return {'CANCELLED'}

        try:
            # Import via temp file (with automatic cleanup)
            gp_obj = import_svg_via_tempfile(svg_data)

            if gp_obj and gp_obj.type == GP_TYPE:
                # Note: In Blender 4.2+ GP v3, layer naming is handled by the importer
                # Layer renaming may not be supported the same way as GP v2
                try:
                    for i, layer in enumerate(gp_obj.data.layers):
                        if i < len(layers):
                            # Try to set layer name if API supports it
                            if hasattr(layer, 'name'):
                                layer.name = layers[i]["name"]
                except (AttributeError, IndexError) as e:
                    # Layer API may have changed or layer count mismatch, skip renaming
                    pass

            self.report({'INFO'}, "SVG imported successfully.")
        except Exception as e:
            # Catch any unexpected errors during import
            self.report({'ERROR'}, f"Import failed: {str(e)}")
            return {'CANCELLED'}

        return {'FINISHED'}

class GP_OT_SplitLayers(bpy.types.Operator):
    """
    Operator to split Grease Pencil layers into separate objects.

    Creates a new object for each layer in the selected Grease Pencil object.
    Each new object contains only one layer. The original object is hidden
    but not deleted.

    Requirements:
    - Active object must be a Grease Pencil v3 object
    - Object must have at least 2 layers

    Usage:
    - Select a Grease Pencil object
    - Run this operator to create separate objects per layer
    """
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
    """
    UI Panel for ToothPaste addon.

    Provides buttons for:
    - Pasting SVG from clipboard
    - Splitting GP layers into separate objects

    Location: 3D View > Sidebar > ToothPaste tab
    """
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
