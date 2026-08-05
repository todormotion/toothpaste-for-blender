"""
Text objects.

Creates Blender FONT objects from SVG <text> nodes.
"""

import os
from typing import Dict, List, Optional

import bpy


def _resolve_font(font_family: str) -> Optional[object]:
    """Best-effort mapping from SVG font-family to loaded Blender font."""
    if not font_family:
        return None
    first_choice = font_family.split(",")[0].strip().strip("'\"")
    if not first_choice:
        return None

    for font in bpy.data.fonts:
        if first_choice.lower() in font.name.lower():
            return font

    if os.path.exists(first_choice):
        try:
            return bpy.data.fonts.load(first_choice)
        except Exception:
            return None
    return None


def create_text_from_svg(scene: bpy.types.Scene, text_items: List[Dict[str, object]]) -> List[bpy.types.Object]:
    """Create Blender FONT objects for SVG text nodes."""
    created: List[bpy.types.Object] = []
    for item in text_items:
        try:
            curve_data = bpy.data.curves.new(name=f"{item['name']}_Font", type="FONT")
            curve_data.body = str(item["body"])
            font_size = max(float(item["font_size"]) * 0.01, 0.01)
            curve_data.size = font_size

            font = _resolve_font(str(item["font_family"]))
            if font:
                curve_data.font = font

            obj = bpy.data.objects.new(str(item["name"]), curve_data)
            obj.location = (float(item["x"]), -float(item["y"]), 0.0)
            scene.collection.objects.link(obj)
            created.append(obj)
        except Exception:
            continue
    return created
