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

# SVG image/bitmap dimensions arrive in pixel units, which land far too large
# next to imported geometry. Objects are created at their pixel size and then
# scaled down by this factor, so a pasted image sits at 0.1 scale.
IMAGE_OBJECT_SCALE = 0.1

ET.register_namespace("", SVG_NS)
ET.register_namespace("xlink", XLINK_NS)
