"""
File import: File > Import entry and drag-and-drop.

One operator serves both. Dropping an SVG into the viewport sets a filepath, so
`invoke_popup` shows the options dialog; picking File > Import leaves it unset,
so the file browser opens with the same options in its sidebar. Both routes
render `draw()`, which delegates to the shared panel layout.
"""

import os
from typing import List

import bpy
from bpy.props import CollectionProperty, StringProperty
from bpy_extras.io_utils import ImportHelper

from .exceptions import ImportContentError, InvalidSVGError
from .importer import import_svg_root, report_warnings
from .properties import (
    OPTION_KEYS,
    SCENE_PREFIX,
    ImportSettings,
    make_create_gp_groups_prop,
    make_curve_stack_axis_prop,
    make_curve_stack_depth_prop,
    make_curve_stack_enabled_prop,
    make_curve_stack_keep_groups_prop,
    make_image_paste_mode_prop,
    make_import_mode_prop,
    make_text_mode_prop,
    make_paste_location_prop,
    make_split_into_gp_layers_prop,
)
from .svg_parser import parse_svg_root

try:
    from bpy_extras.io_utils import poll_file_object_drop
except ImportError:  # pragma: no cover - older Blender builds
    poll_file_object_drop = None


def read_svg_file(filepath: str) -> str:
    """Read an SVG file from disk as text."""
    with open(filepath, "r", encoding="utf-8", errors="replace") as handle:
        return handle.read()


class TOOTHPASTE_OT_import_svg(bpy.types.Operator, ImportHelper):
    """Import an SVG file with ToothPaste, as Grease Pencil or Curves."""

    bl_idname = "toothpaste.import_svg"
    bl_label = "Import SVG (ToothPaste)"
    bl_description = "Import an SVG file as Grease Pencil or Curves using ToothPaste"
    bl_options = {"REGISTER", "UNDO", "PRESET"}

    filename_ext = ".svg"
    filter_glob: StringProperty(default="*.svg", options={"HIDDEN"})

    directory: StringProperty(subtype="DIR_PATH", options={"SKIP_SAVE", "HIDDEN"})
    files: CollectionProperty(
        name="File Path",
        type=bpy.types.OperatorFileListElement,
    )

    # Same options as the sidebar panel, in the same order.
    import_mode: make_import_mode_prop()
    text_mode: make_text_mode_prop()
    split_into_gp_layers: make_split_into_gp_layers_prop()
    create_gp_groups: make_create_gp_groups_prop()
    curve_stack_enabled: make_curve_stack_enabled_prop()
    curve_stack_axis: make_curve_stack_axis_prop()
    curve_stack_depth: make_curve_stack_depth_prop()
    curve_stack_keep_groups: make_curve_stack_keep_groups_prop()
    image_paste_mode: make_image_paste_mode_prop()
    paste_location: make_paste_location_prop()

    def _seed_from_scene(self, scene: bpy.types.Scene) -> None:
        """Open with whatever the sidebar panel is currently set to."""
        for key in OPTION_KEYS:
            value = getattr(scene, f"{SCENE_PREFIX}{key}", None)
            if value is None:
                continue
            try:
                setattr(self, key, value)
            except Exception:
                continue

    def invoke(self, context: bpy.types.Context, event: bpy.types.Event):
        self._seed_from_scene(context.scene)
        # Drag-and-drop arrives with a filepath already set: show the options
        # dialog. Otherwise open the file browser, which draws the same options
        # in its sidebar.
        if self.properties.is_property_set("filepath") and hasattr(self, "invoke_popup"):
            return self.invoke_popup(context)
        context.window_manager.fileselect_add(self)
        return {"RUNNING_MODAL"}

    def draw(self, context: bpy.types.Context):
        from .ui_panels import draw_toothpaste_options

        draw_toothpaste_options(self.layout, self, prefix="", paste_buttons=False)

    def _resolve_paths(self) -> List[str]:
        """Collect every SVG path this invocation should import."""
        paths: List[str] = []
        if self.files and self.directory:
            for entry in self.files:
                name = getattr(entry, "name", "")
                if name:
                    paths.append(os.path.join(self.directory, name))
        if not paths and self.filepath:
            paths.append(self.filepath)
        return [path for path in paths if path.lower().endswith(".svg")]

    def execute(self, context: bpy.types.Context):
        paths = self._resolve_paths()
        if not paths:
            self.report({"ERROR"}, "No SVG file selected.")
            return {"CANCELLED"}

        settings = ImportSettings.from_data(self, prefix="")
        imported = 0
        failures: List[str] = []

        for path in paths:
            try:
                root = parse_svg_root(read_svg_file(path))
            except (InvalidSVGError, OSError) as exc:
                failures.append(f"{os.path.basename(path)}: {exc}")
                continue
            try:
                result = import_svg_root(context, root, self.import_mode, settings)
                report_warnings(self, result.get("warnings", []))
            except ImportContentError as exc:
                failures.append(f"{os.path.basename(path)}: {exc}")
                continue
            except Exception as exc:
                failures.append(f"{os.path.basename(path)}: {exc}")
                continue
            imported += 1

        if not imported:
            self.report({"ERROR"}, "; ".join(failures) or "SVG import failed.")
            return {"CANCELLED"}

        if failures:
            self.report({"WARNING"}, f"Imported {imported} file(s); skipped: {'; '.join(failures)}")
        else:
            self.report({"INFO"}, f"Imported {imported} SVG file(s).")
        return {"FINISHED"}


class TOOTHPASTE_FH_import_svg(bpy.types.FileHandler):
    """Offer ToothPaste in Blender's drop menu for .svg files."""

    bl_idname = "TOOTHPASTE_FH_import_svg"
    bl_label = "SVG (ToothPaste)"
    bl_import_operator = "toothpaste.import_svg"
    bl_file_extensions = ".svg"

    @classmethod
    def poll_drop(cls, context: bpy.types.Context):
        if poll_file_object_drop is not None:
            return poll_file_object_drop(context)
        area = getattr(context, "area", None)
        return bool(area and area.type in {"VIEW_3D", "OUTLINER"})


def menu_func_import(self, context: bpy.types.Context):
    self.layout.operator(TOOTHPASTE_OT_import_svg.bl_idname, text="SVG (ToothPaste)")
