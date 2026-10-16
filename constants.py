"""
Shared constants.

Namespaces, tag sets, layer-name prefixes, clipboard type priorities and the
scale factors applied to created objects. Pure Python: no bpy import.
"""

import xml.etree.ElementTree as ET

GP_TYPE = "GREASEPENCIL"
SVG_NS = "http://www.w3.org/2000/svg"
XLINK_NS = "http://www.w3.org/1999/xlink"
INKSCAPE_NS = "http://www.inkscape.org/namespaces/inkscape"
SODIPODI_NS = "http://sodipodi.sourceforge.net/DTD/sodipodi-0.dtd"
XML_NS = "http://www.w3.org/XML/1998/namespace"
NS = {
    "svg": SVG_NS,
    "xlink": XLINK_NS,
    "inkscape": INKSCAPE_NS,
}
SUPPORTED_SHAPES = {"rect", "circle", "ellipse", "line", "polyline", "polygon"}
SUPPORTED_GEOMETRY = {"path"} | SUPPORTED_SHAPES
MASK_LAYER_PREFIX = "__TP_MASK__"
CLIP_LAYER_PREFIX = "__TP_CLIP__"
RESOURCE_TAGS = {
    "defs",
    "style",
    "clipPath",
    "mask",
    "linearGradient",
    "radialGradient",
    "pattern",
    "symbol",
}
SVG_CLIPBOARD_TYPE_PRIORITY = (
    "image/svg+xml",
    "application/svg+xml",
    "text/svg+xml",
    "public.svg-image",
    "public.svg",
    "org.inkscape.svg",
    "org.inkscape.output.svg.inkscape",
    "application/x-inkscape-svg",
    "image/x-inkscape-svg",
)
XML_CLIPBOARD_TYPE_PRIORITY = (
    "application/xml",
    "text/xml",
    "public.xml",
)
TEXT_CLIPBOARD_TYPE_PRIORITY = (
    "public.utf8-plain-text",
    "public.utf16-plain-text",
    "UTF8_STRING",
    "STRING",
    "TEXT",
    "text/plain",
    "text/plain;charset=utf-8",
)

# Blender's two SVG importers map one SVG pixel to different amounts of space,
# so anything ToothPaste builds itself (images, text) has to use the same ratio
# as the geometry it sits next to. The values below were measured on Blender
# 5.2 and are checked against real imports by the test suite, so a Blender
# update that changes either importer fails loudly instead of drifting.
#
# Grease Pencil: the importer's `scale` option (passed explicitly, so a changed
# default cannot move things) times its 96 dpi pixel size.
GP_IMPORT_SCALE = 10.0
GP_UNITS_PER_PX = GP_IMPORT_SCALE * 0.0254 / 96.0
# Curves: Blender's curve importer works at 90 dpi, which makes the art about 9x
# smaller than the Grease Pencil importer's. ToothPaste scales imported curves up
# by CURVE_SIZE_FACTOR so a Curves paste is the same size as a Grease Pencil one.
CURVE_UNITS_PER_PX = 0.0254 / 90.0
CURVE_SIZE_FACTOR = GP_UNITS_PER_PX / CURVE_UNITS_PER_PX
# Bitmaps pasted from the clipboard have no artwork to line up with, so they
# use Blender's own "Import Images as Planes" default of 100 pixels per unit.
CLIPBOARD_IMAGE_UNITS_PER_PX = 0.01


def units_per_px(import_mode: str) -> float:
    """Blender units per SVG pixel for imported geometry; curves are scaled to match Grease Pencil."""
    return GP_UNITS_PER_PX

ET.register_namespace("", SVG_NS)
ET.register_namespace("xlink", XLINK_NS)
