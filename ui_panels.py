"""
Sidebar UI.

`draw_toothpaste_options` is the single source of truth for option layout: the
View3D panel, the drag-and-drop dialog and the File > Import sidebar all call
it, so the three stay identical in order and composition.
"""

import bpy

from .gp_tools import active_gp_object, is_gp_edit_mode


def draw_toothpaste_options(
    layout: bpy.types.UILayout,
    data: object,
    prefix: str = "toothpaste_",
    paste_buttons: bool = True,
) -> None:
    """
    Draw the ToothPaste options.

    `data` is either the Scene (with `toothpaste_` prefixed properties) or the
    import operator (unprefixed). `paste_buttons` marks the sidebar, which adds the
    clipboard actions and the Image tab; the file import dialog has neither. Only
    the options that apply to the chosen tab are shown.
    """
    def prop_name(key: str) -> str:
        return f"{prefix}{key}"

    mode = getattr(data, prop_name("import_mode"), "GP")
    is_image = paste_buttons and mode == "IMAGE"
    is_curve = mode == "CURVE"

    layout.label(text="Paste as:" if paste_buttons else "Import as:")
    tabs = layout.row(align=True)
    tabs.prop_enum(data, prop_name("import_mode"), "GP")
    tabs.prop_enum(data, prop_name("import_mode"), "CURVE")
    if paste_buttons:
        tabs.prop_enum(data, prop_name("import_mode"), "IMAGE")

    if is_image:
        layout.operator("toothpaste.paste_clipboard_image", icon="IMAGE_DATA")
        layout.separator()
        layout.label(text="Image as:")
        layout.row(align=True).prop(data, prop_name("image_paste_mode"), expand=True)
        layout.separator()
        layout.label(text="Paste at:")
        layout.row(align=True).prop(data, prop_name("paste_location"), expand=True)
        return

    if paste_buttons:
        if is_curve:
            layout.operator("toothpaste.paste_svg_curve", icon="CURVE_DATA")
        else:
            layout.operator("toothpaste.paste_svg", icon="GREASEPENCIL")

    layout.separator()
    if is_curve:
        layout.label(text="Curves")
        layout.prop(data, prop_name("curve_stack_enabled"), text="Stack Curve Objects")
        if getattr(data, prop_name("curve_stack_enabled"), False):
            layout.row(align=True).prop(data, prop_name("curve_stack_axis"), expand=True)
            layout.prop(data, prop_name("curve_stack_depth"), text="Depth")
            layout.prop(data, prop_name("curve_stack_keep_groups"), text="Keep Same SVG Group Level")
    else:
        layout.label(text="Grease Pencil")
        layout.prop(data, prop_name("split_into_gp_layers"), text="Split into GP Layers")
        if getattr(data, prop_name("split_into_gp_layers"), False):
            layout.prop(data, prop_name("create_gp_groups"), text="Create GP Groups")
        if paste_buttons:
            layout.operator("gpencil.split_layers_into_objects", icon="GP_SELECT_POINTS")

    layout.separator()
    layout.label(text="Text")
    text_row = layout.row(align=True)
    text_row.prop_enum(data, prop_name("text_mode"), "TEXT", text="Editable Text")
    text_row.prop_enum(data, prop_name("text_mode"), "OUTLINES", text="Curves" if is_curve else "Grease Pencil")

    layout.separator()
    layout.label(text="SVG images as:")
    layout.row(align=True).prop(data, prop_name("image_paste_mode"), expand=True)

    layout.separator()
    layout.label(text="Paste at:" if paste_buttons else "Place at:")
    layout.row(align=True).prop(data, prop_name("paste_location"), expand=True)


class TOOTHPASTE_PT_panel(bpy.types.Panel):
    """UI panel for ToothPaste tools."""

    bl_label = "ToothPaste"
    bl_idname = "TOOTHPASTE_PT_panel"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "ToothPaste"

    def draw(self, context: bpy.types.Context):
        draw_toothpaste_options(self.layout, context.scene)


class TOOTHPASTE_PT_gp_edit(bpy.types.Panel):
    """Stroke type, thickness and direction for the selected strokes."""

    bl_label = "Grease Pencil Edit"
    bl_idname = "TOOTHPASTE_PT_gp_edit"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "ToothPaste"
    bl_parent_id = "TOOTHPASTE_PT_panel"

    @classmethod
    def poll(cls, context: bpy.types.Context):
        return active_gp_object(context) is not None

    def draw(self, context: bpy.types.Context):
        layout = self.layout
        scene = context.scene
        in_edit = is_gp_edit_mode(context)

        if not in_edit:
            layout.label(text="Available in Edit Mode", icon="INFO")

        body = layout.column()
        body.enabled = in_edit

        body.label(text="Show")
        types = body.row(align=True)
        for stroke_type, label in (("STROKE", "Stroke"), ("FILL", "Fill"), ("BOTH", "Both")):
            types.operator("toothpaste.gp_set_stroke_type", text=label).stroke_type = stroke_type

        body.prop(scene, "toothpaste_gp_thickness", text="Thickness")
        body.operator("toothpaste.gp_reverse_direction", icon="ARROW_LEFTRIGHT")


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
