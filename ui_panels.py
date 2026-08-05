"""
Sidebar UI.

`draw_toothpaste_options` is the single source of truth for option layout: the
View3D panel, the drag-and-drop dialog and the File > Import sidebar all call
it, so the three stay identical in order and composition.
"""

import bpy

from .gp_tools import active_gp_object


def draw_toothpaste_options(
    layout: bpy.types.UILayout,
    data: object,
    prefix: str = "toothpaste_",
    paste_buttons: bool = True,
    import_mode: str = None,
) -> None:
    """
    Draw the ToothPaste options.

    `data` is either the Scene (with `toothpaste_` prefixed properties) or the
    import operator (unprefixed). `paste_buttons` adds the clipboard actions
    that only make sense in the sidebar. `import_mode` dims the section that
    does not apply to the chosen output type.
    """
    def prop_name(key: str) -> str:
        return f"{prefix}{key}"

    if import_mode is not None:
        layout.label(text="Import as:")
        row = layout.row(align=True)
        row.prop(data, "import_mode", expand=True)
        layout.separator()

    layout.label(text="Grease Pencil")
    if paste_buttons:
        layout.operator("toothpaste.paste_svg", icon="GREASEPENCIL")
    gp_column = layout.column(align=False)
    gp_column.active = import_mode is None or import_mode == "GP"
    gp_column.prop(data, prop_name("split_into_gp_layers"), text="Split into GP Layers")
    if getattr(data, prop_name("split_into_gp_layers"), False):
        gp_column.prop(data, prop_name("create_gp_groups"), text="Create GP Groups")
    if paste_buttons:
        layout.operator("gpencil.split_layers_into_objects", icon="GP_SELECT_POINTS")

    layout.separator()
    layout.label(text="Curves & Text")
    if paste_buttons:
        layout.operator("toothpaste.paste_svg_curve", icon="CURVE_DATA")
    curve_column = layout.column(align=False)
    curve_column.active = import_mode is None or import_mode == "CURVE"
    curve_column.prop(data, prop_name("curve_stack_enabled"), text="Stack Curve Objects")
    if getattr(data, prop_name("curve_stack_enabled"), False):
        row = curve_column.row(align=True)
        row.prop(data, prop_name("curve_stack_axis"), expand=True)
        curve_column.prop(data, prop_name("curve_stack_depth"), text="Depth")
        curve_column.prop(data, prop_name("curve_stack_keep_groups"), text="Keep Same SVG Group Level")

    layout.separator()
    layout.label(text="Images")
    if paste_buttons:
        layout.operator("toothpaste.paste_clipboard_image", icon="IMAGE_DATA")
    row = layout.row(align=True)
    row.prop(data, prop_name("image_paste_mode"), expand=True)

    layout.separator()
    layout.label(text="Paste at:" if paste_buttons else "Place at:")
    row = layout.row(align=True)
    row.prop(data, prop_name("paste_location"), expand=True)


class TOOTHPASTE_PT_panel(bpy.types.Panel):
    """UI panel for ToothPaste tools."""

    bl_label = "ToothPaste"
    bl_idname = "TOOTHPASTE_PT_panel"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "ToothPaste"

    def draw(self, context: bpy.types.Context):
        draw_toothpaste_options(self.layout, context.scene)


def draw_gp_layer_selection(self, context: bpy.types.Context) -> None:
    """
    Layer stroke selection buttons, appended to Object Data Properties > Layers.

    Blender only allows adding to the top or the bottom of a built-in panel, so
    these sit at the end of the Layers panel rather than directly under the
    layer list.
    """
    gp_obj = active_gp_object(context)
    if gp_obj is None:
        return

    layers = getattr(getattr(gp_obj, "data", None), "layers", None)
    if layers is None:
        return

    layout = self.layout
    layout.use_property_split = False
    layout.separator()
    row = layout.row(align=True)
    # Both buttons act on the active layer, so they stay visible but greyed out
    # until the user has one selected in the list above.
    row.enabled = getattr(layers, "active", None) is not None
    row.operator(
        "toothpaste.gp_select_layer_strokes",
        text="Select Strokes",
        icon="RESTRICT_SELECT_OFF",
    ).action = "SELECT"
    row.operator(
        "toothpaste.gp_select_layer_strokes",
        text="Deselect",
        icon="RESTRICT_SELECT_ON",
    ).action = "DESELECT"
