"""
Text objects.

Creates editable Blender FONT objects from SVG <text> items: the string, size,
alignment, rotation, colour and, most importantly, the typeface. An SVG only
names its font, so the name is looked up among the fonts installed on this
machine and that file is loaded into Blender.
"""

import math
import os
import tempfile
from typing import Dict, List, Optional

import bpy

from . import font_resolver
from .svg_frame import SvgFrame

_ALIGN = {"start": "LEFT", "middle": "CENTER", "end": "RIGHT"}

# Blender's built-in font is Inter; its size-1 text measures 0.9375 em, so its
# bounding-box height factor is 1 / 0.9375. Only used when no installed font matched.
BUILTIN_FONT_BBOX_H = 1.067

_font_index: Optional[font_resolver.FontIndex] = None
_loaded_fonts: Dict[str, object] = {}


def _cache_dir(name: str) -> str:
    """Per-user folder for the font cache, falling back to a temp folder."""
    try:
        return bpy.utils.extension_path_user(__package__, path=name, create=True)
    except Exception:
        fallback = os.path.join(tempfile.gettempdir(), "toothpaste_" + name)
        os.makedirs(fallback, exist_ok=True)
        return fallback


def get_font_index(force_rescan: bool = False) -> font_resolver.FontIndex:
    """Installed-font index, scanned once per session and cached on disk."""
    global _font_index
    if _font_index is None or force_rescan:
        cache_file = os.path.join(_cache_dir("font_cache"), "fonts.json")
        _font_index = font_resolver.build_index(cache_path=cache_file)
    return _font_index


def reset_font_cache() -> None:
    """Forget the scanned index and the fonts loaded from it (used by tests)."""
    global _font_index
    _font_index = None
    _loaded_fonts.clear()


def _load_face(face: Optional[dict]) -> Optional[object]:
    """Load one font face into Blender, splitting it out of a collection if needed."""
    if face is None:
        return None
    path = font_resolver.extract_face(face, _cache_dir("extracted_fonts"))
    if not path:
        return None
    cached = _loaded_fonts.get(path)
    if cached is not None:
        try:
            cached.name  # raises ReferenceError if the datablock was removed
            return cached
        except Exception:
            _loaded_fonts.pop(path, None)
    try:
        font = bpy.data.fonts.load(path, check_existing=True)
    except Exception:
        return None
    _loaded_fonts[path] = font
    return font


def _text_material(rgba) -> Optional[bpy.types.Material]:
    """One shared flat-colour material per distinct text colour."""
    if rgba is None:
        return None
    key = "".join(f"{int(round(max(0.0, min(1.0, c)) * 255)):02X}" for c in rgba[:3])
    name = f"TP_Text_{key}"
    material = bpy.data.materials.get(name)
    if material is None:
        material = bpy.data.materials.new(name)
        material.diffuse_color = (rgba[0], rgba[1], rgba[2], 1.0)
        try:
            material.use_nodes = True
            principled = next(
                (node for node in material.node_tree.nodes if node.type == "BSDF_PRINCIPLED"), None
            )
            if principled is not None:
                principled.inputs["Base Color"].default_value = (rgba[0], rgba[1], rgba[2], 1.0)
        except Exception:
            pass
    return material


def _apply_fonts(curve_data: bpy.types.Curve, item: Dict[str, object], report: Optional[List[str]]) -> Optional[dict]:
    """Point the text at the installed font the SVG asked for. Returns the matched face, if any."""
    families = list(item.get("font_families") or [])
    if not families:
        return None

    index = get_font_index()
    face = index.resolve(families, int(item.get("font_weight", 400)), bool(item.get("font_italic", False)))
    if face is None:
        if report is not None:
            report.append(
                f"Font '{families[0]}' (used by '{item['name']}') is not installed - using Blender's default font"
            )
        return None

    font = _load_face(face)
    if font is None:
        if report is not None:
            report.append(f"Font '{face.get('full') or families[0]}' could not be loaded - using Blender's default font")
        return None

    curve_data.font = font
    # Fill the style slots too, so Bold/Italic toggles in text edit mode switch
    # to real faces of the same family instead of faking them.
    variants = index.variants(face)
    for slot, attribute in (
        ("bold", "font_bold"),
        ("italic", "font_italic"),
        ("bold_italic", "font_bold_italic"),
    ):
        variant = _load_face(variants.get(slot))
        if variant is not None:
            setattr(curve_data, attribute, variant)
    return face


def convert_to_outlines(
    objects: List[bpy.types.Object],
    target: str,
    report: Optional[List[str]] = None,
) -> List[bpy.types.Object]:
    """
    Turn text objects into shapes, in place.

    `target` is "CURVE" (text -> curve) or "GP" (text -> curve -> Grease Pencil,
    which keeps letter holes through Blender's fill grouping). Location,
    rotation and material carry over, and the objects keep their names.
    """
    if not objects:
        return objects

    view_layer = bpy.context.view_layer
    previous_active = view_layer.objects.active
    previous_selection = [obj for obj in view_layer.objects if obj.select_get()]
    try:
        for obj in view_layer.objects:
            obj.select_set(False)
        for obj in objects:
            obj.select_set(True)
        view_layer.objects.active = objects[0]
        bpy.ops.object.convert(target="CURVE")
        if target == "GP":
            bpy.ops.object.convert(target="GREASEPENCIL")
            for obj in objects:
                layers = getattr(obj.data, "layers", None)
                if layers is not None and len(layers) == 1:
                    layers[0].name = obj.name
    except Exception as exc:
        if report is not None:
            report.append(f"Could not convert text to outlines ({exc}) - it was kept as editable text")
    finally:
        for obj in view_layer.objects:
            obj.select_set(False)
        for obj in previous_selection:
            try:
                obj.select_set(True)
            except Exception:
                pass
        try:
            view_layer.objects.active = previous_active
        except Exception:
            pass
    return objects


def create_text_from_svg(
    scene: bpy.types.Scene,
    text_items: List[Dict[str, object]],
    frame: SvgFrame,
    report: Optional[List[str]] = None,
    outlines: Optional[str] = None,
) -> List[bpy.types.Object]:
    """
    Create Blender FONT objects for SVG text items.

    `frame` is where the artwork from the same SVG was imported, so text lands
    where the SVG puts it and keeps its size relative to the shapes beside it.
    Anything worth telling the user (missing fonts, mixed styles) is appended
    to `report`. With `outlines` set to "CURVE" or "GP" the finished text is
    converted to that kind of shape instead of staying editable.
    """
    created: List[bpy.types.Object] = []
    for item in text_items:
        try:
            curve_data = bpy.data.curves.new(name=f"{item['name']}_Font", type="FONT")
            curve_data.body = str(item["body"])
            curve_data.align_x = _ALIGN.get(str(item.get("anchor", "start")), "LEFT")

            face = _apply_fonts(curve_data, item, report)
            # Blender's text size is 1 / bbox_h em rather than 1 em, and the factor
            # differs per font, so a real point size needs the font's own value.
            bbox_h = float(face.get("bbox_h") or 0.0) if face else 0.0
            if bbox_h <= 0.0:
                bbox_h = BUILTIN_FONT_BBOX_H
            curve_data.size = max(frame.length(float(item["font_size"])) * bbox_h, 1e-5)
            # Line distance is measured in `size` units, so it carries the same factor.
            curve_data.space_line = max(float(item.get("line_spacing", 1.0)) / bbox_h, 0.01)

            material = _text_material(item.get("fill"))
            if material is not None:
                curve_data.materials.append(material)

            obj = bpy.data.objects.new(str(item["name"]), curve_data)
            x, y = frame.point(float(item["x"]), float(item["y"]))
            obj.location = (x, y, frame.depth(bool(item.get("above_art", True))))
            # SVG angles run clockwise (y points down); Blender's run counter-clockwise.
            obj.rotation_euler = (0.0, 0.0, -float(item.get("rotation", 0.0)))
            scene.collection.objects.link(obj)
            created.append(obj)

            if item.get("mixed_styles") and report is not None:
                report.append(
                    f"Text '{item['name']}' mixes several fonts or sizes - the first style was used for all of it"
                )
        except Exception:
            continue
    if outlines:
        convert_to_outlines(created, outlines, report)
    return created
