"""
The coordinate frame an SVG is imported into.

Blender's two SVG importers place the same file differently (the curve importer
flips the vertical axis around the canvas height, the Grease Pencil importer
anchors the top-left corner), and both react to `viewBox`, `width` and `height`
in their own ways. Anything ToothPaste builds itself, text and images, has to
land exactly where the importer put the artwork next to it.

An `SvgFrame` describes that placement: where the top-left corner of the SVG
canvas ended up, and how many Blender units one SVG user unit became. The
importer measures it from a probe import, so it holds for any file and keeps
working if a later Blender version changes how its importers behave.

Everything here is in the flat "frame" plane: X to the right, Y up, objects
facing +Z. Pure Python: this module must never import bpy.
"""

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Tuple

from .constants import CLIPBOARD_IMAGE_UNITS_PER_PX, GP_UNITS_PER_PX


@dataclass(frozen=True)
class SvgFrame:
    """Maps SVG user units to positions and sizes in Blender units."""

    units_per_user: float
    origin_x: float = 0.0
    origin_y: float = 0.0
    view_x: float = 0.0
    view_y: float = 0.0

    def point(self, x: float, y: float) -> Tuple[float, float]:
        """Frame position of an SVG point; the vertical axis runs upwards."""
        return (
            self.origin_x + (x - self.view_x) * self.units_per_user,
            self.origin_y - (y - self.view_y) * self.units_per_user,
        )

    def length(self, value: float) -> float:
        """Size in Blender units of a length given in SVG user units."""
        return value * self.units_per_user

    def depth(self, above_art: bool) -> float:
        """
        Offset along the facing axis that settles what draws on top.

        Text and images share the artwork's plane, and coplanar objects fight over
        the draw order (a Grease Pencil fill simply hides text lying in it). One
        SVG unit is far too small to see but enough to follow the SVG's own order.
        """
        return self.length(1.0) if above_art else -self.length(1.0)


# Bitmaps pasted from the clipboard have no artwork to line up with.
CLIPBOARD_FRAME = SvgFrame(units_per_user=CLIPBOARD_IMAGE_UNITS_PER_PX)


def view_origin(root: ET.Element) -> Tuple[float, float]:
    """Top-left corner of the SVG canvas in user units (the viewBox origin)."""
    numbers = re.findall(r"[-+]?(?:\d*\.\d+|\d+)(?:[eE][-+]?\d+)?", root.get("viewBox") or "")
    if len(numbers) >= 4:
        return float(numbers[0]), float(numbers[1])
    return 0.0, 0.0


def fallback_frame(root: ET.Element, import_mode: str) -> SvgFrame:
    """
    Best guess when the probe import cannot be measured.

    Uses the pixel size measured on Blender 5.2 (curves are scaled up to the Grease
    Pencil size) and puts the canvas corner at the origin, which is exactly right
    for Grease Pencil.
    """
    view_x, view_y = view_origin(root)
    return SvgFrame(units_per_user=GP_UNITS_PER_PX, view_x=view_x, view_y=view_y)
