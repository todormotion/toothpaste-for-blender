"""
Clipboard paste operators.

These only read options, hand the parsed SVG to the shared import pipeline and
report the result.
"""

from typing import List

import bpy

from .clipboard import get_clipboard_svg
from .exceptions import ClipboardError, ImportContentError, InvalidSVGError
from .importer import import_svg_as_curves, import_svg_as_grease_pencil, report_warnings
from .obj_images import _create_image_object, paste_image_from_clipboard_builtin
from .properties import ImportSettings
from .scene_utils import _move_objects_to_collection, place_import_block
from .svg_frame import CLIPBOARD_FRAME
from .svg_parser import parse_svg_root


class TOOTHPASTE_OT_paste_svg(bpy.types.Operator):
    """Paste SVG as Grease Pencil object and rebuild masks/holdout where possible."""

    bl_idname = "toothpaste.paste_svg"
    bl_label = "Paste as Grease Pencil"
    bl_description = "Paste SVG code from clipboard as Grease Pencil (v3)"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context: bpy.types.Context):
        try:
            svg_data = get_clipboard_svg()
            root = parse_svg_root(svg_data)
        except (ClipboardError, InvalidSVGError) as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}

        settings = ImportSettings.from_scene(context.scene)
        try:
            result = import_svg_as_grease_pencil(context, root, settings)
        except ImportContentError as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        except Exception as exc:
            self.report({"ERROR"}, f"Import failed: {exc}")
            return {"CANCELLED"}

        report_warnings(self, result.get("warnings", []))
        self.report(
            {"INFO"},
            "Imported GP: {}, Text: {}, Images: {}".format(
                len(result["grease_pencil"]),
                len(result["texts"]),
                len(result["images"]),
            ),
        )
        return {"FINISHED"}


class TOOTHPASTE_OT_paste_svg_curve(bpy.types.Operator):
    """Paste SVG as Curve objects with optional split by SVG layers."""

    bl_idname = "toothpaste.paste_svg_curve"
    bl_label = "Paste as Curves"
    bl_description = "Paste SVG code from clipboard as Curve objects"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context: bpy.types.Context):
        try:
            svg_data = get_clipboard_svg()
            root = parse_svg_root(svg_data)
        except (ClipboardError, InvalidSVGError) as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}

        settings = ImportSettings.from_scene(context.scene)
        try:
            result = import_svg_as_curves(context, root, settings)
        except ImportContentError as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        except Exception as exc:
            self.report({"ERROR"}, f"Curve import failed: {exc}")
            return {"CANCELLED"}

        report_warnings(self, result.get("warnings", []))
        self.report(
            {"INFO"},
            "Imported Curves: {}, Text: {}, Images: {}".format(
                len(result["curves"]),
                len(result["texts"]),
                len(result["images"]),
            ),
        )
        return {"FINISHED"}


class TOOTHPASTE_OT_paste_clipboard_image(bpy.types.Operator):
    """Paste bitmap image from clipboard using Blender's built-in image operator."""

    bl_idname = "toothpaste.paste_clipboard_image"
    bl_label = "Paste Image (Clipboard)"
    bl_description = "Paste image from clipboard using Blender built-in functionality"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context: bpy.types.Context):
        try:
            image = paste_image_from_clipboard_builtin(context)
            if image is None:
                self.report({"ERROR"}, "No image was created from clipboard.")
                return {"CANCELLED"}

            item = {
                "name": image.name,
                "x": 0.0,
                "y": 0.0,
                "width": float(image.size[0]) if image.size and image.size[0] else 1.0,
                "height": float(image.size[1]) if image.size and image.size[1] else 1.0,
            }
            obj = _create_image_object(
                context.scene,
                image,
                item,
                context.scene.toothpaste_image_paste_mode,
                CLIPBOARD_FRAME,
            )
            if obj is None:
                self.report({"ERROR"}, "Failed to create image object.")
                return {"CANCELLED"}
            place_import_block(
                [obj],
                [],
                context.scene,
                context.scene.toothpaste_paste_location,
            )
            _move_objects_to_collection(context.scene, [obj], "Pasted Image")
            self.report({"INFO"}, f"Pasted image: {image.name}")
            return {"FINISHED"}
        except Exception as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
