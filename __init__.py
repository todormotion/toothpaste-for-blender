"""
ToothPaste for Blender - SVG Clipboard Tools for Grease Pencil and Curves.

V1.1.6 highlights:
- Paste SVG as Grease Pencil or Curve objects
- Import SVG files via File > Import and drag-and-drop
- Optional split mode for SVG layers on paste
- GP layer mask and clip-path reconstruction using GP masks + holdout layers
- SVG text parsing to Blender FONT objects
- SVG <image> support for embedded/local images
- Grease Pencil layer stroke selection in Object Data Properties

The add-on is split by functionality: each module owns one concern and this file
only wires them into Blender. Public helpers are re-exported below so existing
scripts and tests that imported them from the package keep working.
"""

bl_info = {
    "name": "ToothPaste for Blender",
    "author": "Cosmonavt",
    "version": (1, 1, 6),
    "blender": (5, 0, 0),
    "category": "Import-Export",
    "location": "View3D > Sidebar > ToothPaste, File > Import > SVG (ToothPaste)",
    "description": "Paste or import SVG as Grease Pencil or Curves, with layer split, masks, text, and image support.",
}

import bpy

from . import properties
from .clipboard import get_clipboard_svg
from .exceptions import ClipboardError, ImportContentError, InvalidSVGError, ToothPasteError
from .gp_layers import is_valid_gp_object, split_gp_object_layers
from .gp_tools import TOOTHPASTE_OT_gp_select_layer_strokes
from .importer import (
    import_svg_as_curves,
    import_svg_as_grease_pencil,
    import_svg_root,
    import_svg_via_tempfile,
)
from .obj_images import create_images_from_svg, paste_image_from_clipboard_builtin
from .obj_text import create_text_from_svg
from .ops_gp import GP_OT_SplitLayers
from .ops_import import (
    TOOTHPASTE_FH_import_svg,
    TOOTHPASTE_OT_import_svg,
    menu_func_import,
)
from .ops_paste import (
    TOOTHPASTE_OT_paste_clipboard_image,
    TOOTHPASTE_OT_paste_svg,
    TOOTHPASTE_OT_paste_svg_curve,
)
from .svg_parser import parse_svg, parse_svg_root, validate_svg_string
from .ui_panels import (
    TOOTHPASTE_PT_panel,
    draw_gp_layer_selection,
    draw_toothpaste_options,
)

__all__ = (
    "bl_info",
    "register",
    "unregister",
    "ClipboardError",
    "ImportContentError",
    "InvalidSVGError",
    "ToothPasteError",
    "create_images_from_svg",
    "create_text_from_svg",
    "draw_gp_layer_selection",
    "draw_toothpaste_options",
    "get_clipboard_svg",
    "import_svg_as_curves",
    "import_svg_as_grease_pencil",
    "import_svg_root",
    "import_svg_via_tempfile",
    "is_valid_gp_object",
    "parse_svg",
    "parse_svg_root",
    "paste_image_from_clipboard_builtin",
    "split_gp_object_layers",
    "validate_svg_string",
)

classes = (
    TOOTHPASTE_OT_paste_svg,
    TOOTHPASTE_OT_paste_svg_curve,
    TOOTHPASTE_OT_paste_clipboard_image,
    TOOTHPASTE_OT_import_svg,
    TOOTHPASTE_FH_import_svg,
    TOOTHPASTE_OT_gp_select_layer_strokes,
    GP_OT_SplitLayers,
    TOOTHPASTE_PT_panel,
)

# Blender's Grease Pencil layer list lives here; the stroke selection buttons
# are appended to it rather than duplicated in the ToothPaste sidebar.
GP_LAYERS_PANEL = "DATA_PT_grease_pencil_layers"


def register():
    properties.register()
    for cls in classes:
        bpy.utils.register_class(cls)
    bpy.types.TOPBAR_MT_file_import.append(menu_func_import)

    layers_panel = getattr(bpy.types, GP_LAYERS_PANEL, None)
    if layers_panel is not None:
        layers_panel.append(draw_gp_layer_selection)


def unregister():
    layers_panel = getattr(bpy.types, GP_LAYERS_PANEL, None)
    if layers_panel is not None:
        try:
            layers_panel.remove(draw_gp_layer_selection)
        except Exception:
            pass
    try:
        bpy.types.TOPBAR_MT_file_import.remove(menu_func_import)
    except Exception:
        pass
    for cls in reversed(classes):
        try:
            bpy.utils.unregister_class(cls)
        except Exception:
            pass
    properties.unregister()


if __name__ == "__main__":
    register()
