"""
Add-on properties.

Every ToothPaste option is defined exactly once here, as a factory that returns
a fresh property definition. The Scene registers them for the sidebar panel and
the file import operator registers the same set for its dialog, which is what
keeps both UIs identical.
"""

from typing import Dict, List, Optional

import bpy

from .gp_tools import on_thickness_changed

# Logical option key -> Scene property name. The import operator uses the bare
# logical key as its own property name, so one draw function can serve both by
# swapping the prefix.
SCENE_PREFIX = "toothpaste_"

OPTION_KEYS = (
    "import_mode",
    "text_mode",
    "split_into_gp_layers",
    "create_gp_groups",
    "curve_stack_enabled",
    "curve_stack_axis",
    "curve_stack_depth",
    "curve_stack_keep_groups",
    "image_paste_mode",
    "paste_location",
)


def make_split_into_gp_layers_prop():
    return bpy.props.BoolProperty(
        name="Split into GP Layers",
        description="Split imported SVG geometry into separate GP layers",
        default=True,
    )


def make_create_gp_groups_prop():
    return bpy.props.BoolProperty(
        name="Create GP Groups",
        description="Create Grease Pencil layer groups from SVG groups when supported by Blender",
        default=True,
    )


def make_curve_stack_enabled_prop():
    return bpy.props.BoolProperty(
        name="Stack Curve Objects",
        description="Offset imported curve objects into a viewport stack",
        default=False,
    )


def make_curve_stack_axis_prop():
    return bpy.props.EnumProperty(
        name="Stack Axis",
        description="Axis used to offset the curve stack",
        items=[
            ("X", "X", "Stack imported curves on the X axis"),
            ("Y", "Y", "Stack imported curves on the Y axis"),
            ("Z", "Z", "Stack imported curves on the Z axis"),
        ],
        default="Z",
    )


def make_curve_stack_depth_prop():
    return bpy.props.FloatProperty(
        name="Depth",
        description="Distance between imported curve objects in the stack",
        default=0.1,
        min=-10000.0,
        soft_min=-10.0,
        soft_max=10.0,
    )


def make_curve_stack_keep_groups_prop():
    return bpy.props.BoolProperty(
        name="Keep Same SVG Group Level",
        description="Objects from the same SVG group share one stack level",
        default=True,
    )


def make_image_paste_mode_prop():
    return bpy.props.EnumProperty(
        name="Image Paste Mode",
        description="Create images as reference empties or mesh planes",
        items=[
            ("MESH_PLANE", "Mesh Plane", "Paste images as textured mesh planes"),
            ("REFERENCE", "Reference", "Paste images as reference empties"),
        ],
        default="REFERENCE",
    )


def make_paste_location_prop():
    return bpy.props.EnumProperty(
        name="Paste Location",
        description="Placement target for pasted objects",
        items=[
            ("ORIGIN", "World Origin", "Place pasted objects centered at world origin"),
            ("CURSOR", "Cursor", "Place pasted objects centered at 3D cursor"),
        ],
        default="ORIGIN",
    )


def make_text_mode_prop():
    return bpy.props.EnumProperty(
        name="Text",
        description="How SVG text is brought in",
        items=[
            ("TEXT", "Editable Text", "Keep text editable as Blender text objects, using the installed font the SVG names"),
            (
                "OUTLINES",
                "Outlines",
                "Convert text to shapes (Grease Pencil strokes or curves, matching the output type) so it looks the same on any machine",
            ),
        ],
        default="TEXT",
    )


def make_paste_type_prop():
    """What the sidebar pastes: Grease Pencil, Curves or a clipboard image."""
    return bpy.props.EnumProperty(
        name="Paste As",
        description="What to create from the clipboard",
        items=[
            ("GP", "Grease Pencil", "Paste the SVG as a Grease Pencil object"),
            ("CURVE", "Curves", "Paste the SVG as Curve objects"),
            ("IMAGE", "Image", "Paste a bitmap image from the clipboard"),
        ],
        default="GP",
    )


def make_import_mode_prop():
    return bpy.props.EnumProperty(
        name="Import As",
        description="Object type created from the SVG",
        items=[
            ("GP", "Grease Pencil", "Import the SVG as a Grease Pencil object"),
            ("CURVE", "Curves", "Import the SVG as Curve objects"),
        ],
        default="GP",
    )


# The sidebar's Paste As tab also offers Image, which the file import dialog
# (SVG only) does not, so the scene and the dialog use different enums.
PROPERTY_FACTORIES = {
    "import_mode": make_paste_type_prop,
    "text_mode": make_text_mode_prop,
    "split_into_gp_layers": make_split_into_gp_layers_prop,
    "create_gp_groups": make_create_gp_groups_prop,
    "curve_stack_enabled": make_curve_stack_enabled_prop,
    "curve_stack_axis": make_curve_stack_axis_prop,
    "curve_stack_depth": make_curve_stack_depth_prop,
    "curve_stack_keep_groups": make_curve_stack_keep_groups_prop,
    "image_paste_mode": make_image_paste_mode_prop,
    "paste_location": make_paste_location_prop,
}

# Grease Pencil edit controls. These live on the Scene because they are a
# staging area: changing one applies it to the selected points in edit mode.
GP_TOOL_PROPERTY_NAMES = (
    "toothpaste_gp_thickness",
)


class ImportSettings:
    """Resolved option values for one import run."""

    __slots__ = tuple(OPTION_KEYS)

    def __init__(self, **values):
        for key in OPTION_KEYS:
            setattr(self, key, values.get(key))

    @classmethod
    def from_data(cls, data: object, prefix: str = "") -> "ImportSettings":
        """Read options off a Scene (prefixed) or an operator (unprefixed)."""
        values = {}
        for key in OPTION_KEYS:
            values[key] = getattr(data, f"{prefix}{key}", None)
        return cls(**values)

    @classmethod
    def from_scene(cls, scene: bpy.types.Scene) -> "ImportSettings":
        return cls.from_data(scene, SCENE_PREFIX)


def register():
    for key, factory in PROPERTY_FACTORIES.items():
        setattr(bpy.types.Scene, f"{SCENE_PREFIX}{key}", factory())

    bpy.types.Scene.toothpaste_gp_thickness = bpy.props.FloatProperty(
        name="Thickness",
        description="Line thickness applied to the selected points while in Grease Pencil edit mode",
        subtype="DISTANCE",
        unit="LENGTH",
        min=0.0,
        max=1000.0,
        soft_max=0.05,
        default=0.01,
        precision=4,
        update=on_thickness_changed,
    )


def unregister():
    names = [f"{SCENE_PREFIX}{key}" for key in PROPERTY_FACTORIES] + list(GP_TOOL_PROPERTY_NAMES)
    for name in names:
        if hasattr(bpy.types.Scene, name):
            try:
                delattr(bpy.types.Scene, name)
            except Exception:
                pass
