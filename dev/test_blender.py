"""
ToothPaste integration tests - run them against any Blender build.

    /path/to/Blender --background --factory-startup --python dev/test_blender.py

Run this against a new Blender release before shipping for it. It copies the
add-on to a temporary folder, registers it, and drives the real importers and
operators, so a Blender change that breaks ToothPaste shows up here as a FAIL.
Exits with status 1 if anything fails.

Optional: if a `toothpaste_tests/` folder of sample SVGs sits next to this
folder, every file in it is also parsed and imported.
"""

import math
import os
import shutil
import sys
import tempfile
import traceback

import bpy

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.dirname(HERE)
WORK = tempfile.mkdtemp(prefix="toothpaste_test_")
PKG = os.path.join(WORK, "toothpaste")
shutil.copytree(SRC, PKG, ignore=shutil.ignore_patterns("__pycache__", ".git", "*.zip", "dev", "toothpaste_tests"))
sys.path.insert(0, WORK)

failures = []
skipped = []


def check(label, condition, detail=""):
    if not condition:
        failures.append(f"{label}: {detail}")
    print(f"[{'PASS' if condition else 'FAIL'}] {label}{(' - ' + str(detail)) if detail != '' else ''}")


def skip(label, reason):
    skipped.append(label)
    print(f"[SKIP] {label} - {reason}")


def near(a, b, tolerance=0.02):
    """Relative comparison."""
    return abs(a - b) <= tolerance * max(abs(a), abs(b), 1e-12)


def write(name, text):
    path = os.path.join(WORK, name)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(text)
    return path


def clear_scene():
    bpy.ops.wm.read_factory_settings(use_empty=True)


import toothpaste  # noqa: E402
from toothpaste import constants, font_resolver, obj_text  # noqa: E402
from toothpaste.importer import import_svg_root  # noqa: E402
from toothpaste.font_resolver import normalize_name  # noqa: E402
from toothpaste.properties import OPTION_KEYS, ImportSettings  # noqa: E402
from toothpaste.svg_parser import parse_svg, parse_svg_root  # noqa: E402
from toothpaste.svg_plan import _collapse_single_object_groups, _prepare_import_plan  # noqa: E402

toothpaste.register()
print(f"Blender {bpy.app.version_string} - ToothPaste {toothpaste.bl_info['version']}")

# --------------------------------------------------------------------------- #
# registration
# --------------------------------------------------------------------------- #
for name in (
    "TOOTHPASTE_OT_paste_svg",
    "TOOTHPASTE_OT_paste_svg_curve",
    "TOOTHPASTE_OT_paste_clipboard_image",
    "TOOTHPASTE_OT_import_svg",
    "TOOTHPASTE_OT_gp_select_layer_strokes",
    "TOOTHPASTE_OT_gp_set_stroke_type",
    "TOOTHPASTE_OT_gp_reverse_direction",
    "TOOTHPASTE_PT_panel",
    "TOOTHPASTE_PT_gp_edit",
):
    check(f"registered {name}", hasattr(bpy.types, name))
check("drop handler", "TOOTHPASTE_FH_import_svg" in [c.bl_idname for c in bpy.types.FileHandler.__subclasses__()])
check(
    "File > Import entry",
    any(getattr(f, "__module__", "").startswith("toothpaste") for f in bpy.types.TOPBAR_MT_file_import._dyn_ui_initialize()),
)
check(
    "layer buttons on GP Layers panel",
    any(getattr(f, "__name__", "") == "draw_gp_layer_selection" for f in bpy.types.DATA_PT_grease_pencil_layers._dyn_ui_initialize()),
)
for prop in ("toothpaste_gp_thickness",):
    check(f"scene prop {prop}", hasattr(bpy.context.scene, prop))
op_props = {p.identifier for p in bpy.ops.toothpaste.import_svg.get_rna_type().properties}
check("import dialog has every option", all(k in op_props for k in OPTION_KEYS) and "import_mode" in op_props)

# --------------------------------------------------------------------------- #
# pixel -> Blender unit ratios (guards against a Blender update changing importers)
# --------------------------------------------------------------------------- #
rect = write(
    "rect.svg",
    '<svg xmlns="http://www.w3.org/2000/svg" width="1000" height="1000"><g id="L">'
    '<rect id="R" x="0" y="0" width="200" height="100" fill="#f00"/></g></svg>',
)

clear_scene()
bpy.ops.toothpaste.import_svg(filepath=rect, import_mode="GP", split_into_gp_layers=True)
gp_obj = next(o for o in bpy.context.scene.objects if o.type == "GREASEPENCIL")
xs = [d.vector[0] for d in gp_obj.data.layers[0].frames[0].drawing.attributes["position"].data]
measured_gp = (max(xs) - min(xs)) / 200.0
check("GP units per SVG px matches constant", near(measured_gp, constants.GP_UNITS_PER_PX, 0.01), f"{measured_gp:.6f} vs {constants.GP_UNITS_PER_PX:.6f}")

# Blender's own curve importer, untouched: this is the size ToothPaste has to correct
clear_scene()
bpy.ops.import_curve.svg(filepath=rect)
raw_curve = next(o for o in bpy.context.scene.objects if o.type == "CURVE")
xs = [v[0] for v in raw_curve.bound_box]
measured_raw_curve = (max(xs) - min(xs)) / 200.0
check("Blender's curve importer size matches the constant", near(measured_raw_curve, constants.CURVE_UNITS_PER_PX, 0.01), f"{measured_raw_curve:.6f} vs {constants.CURVE_UNITS_PER_PX:.6f}")
check("the curve size factor brings it to the GP size", near(measured_raw_curve * constants.CURVE_SIZE_FACTOR, measured_gp, 0.01), f"{measured_raw_curve * constants.CURVE_SIZE_FACTOR:.6f} vs {measured_gp:.6f}")

# What ToothPaste actually pastes as Curves must be the same size as Grease Pencil
clear_scene()
bpy.ops.toothpaste.import_svg(filepath=rect, import_mode="CURVE")
curve_obj = next(o for o in bpy.context.scene.objects if o.type == "CURVE")
xs = [v[0] for v in curve_obj.bound_box]
ys = [v[1] for v in curve_obj.bound_box]
check("pasted Curves are the same width as pasted Grease Pencil", near((max(xs) - min(xs)) / 200.0, measured_gp, 0.01), f"{(max(xs) - min(xs)) / 200.0:.6f} vs {measured_gp:.6f}")
check("pasted Curves are the same height as pasted Grease Pencil", near((max(ys) - min(ys)) / 100.0, measured_gp, 0.01), f"{(max(ys) - min(ys)) / 100.0:.6f} vs {measured_gp:.6f}")
check("pasted curve object scale stays 1,1,1 (size is in the curve data)", tuple(round(v, 6) for v in curve_obj.scale) == (1.0, 1.0, 1.0), tuple(curve_obj.scale))

# --------------------------------------------------------------------------- #
# font matching
# --------------------------------------------------------------------------- #
obj_text.reset_font_cache()
index = obj_text.get_font_index()
check("font index built", len(index) > 0, f"{len(index)} faces")


def family_with_bold():
    """An installed family whose bold and regular cuts are different files/faces."""
    for key in ("arial", "helvetica", "timesnewroman", "dejavusans", "liberationsans", "segoeui"):
        regular = index.best_in_family(key, 400, False)
        bold = index.best_in_family(key, 700, False)
        if regular and bold and (regular["path"], regular["index"]) != (bold["path"], bold["index"]):
            return regular["family"] or regular["legacy_family"], regular, bold
    for key in list(index.by_family)[:3000]:
        regular = index.best_in_family(key, 400, False)
        bold = index.best_in_family(key, 700, False)
        if regular and bold and regular["weight"] <= 450 and bold["weight"] >= 650 and bold["width"] == regular["width"]:
            return regular["family"], regular, bold
    return None, None, None


family, regular_face, bold_face = family_with_bold()
if family is None:
    skip("font weight selection", "no family with regular and bold cuts installed")
else:
    check("weight picks the bold cut", index.resolve([family], 700, False)["weight"] >= 600, family)
    check("default weight picks the regular cut", index.resolve([family], 400, False)["weight"] <= 500, family)
    check("PostScript name resolves exactly", index.resolve([bold_face["ps"]]) is not None and index.resolve([bold_face["ps"]])["ps"] == bold_face["ps"], bold_face["ps"])
    check("fallback list skips missing fonts", index.resolve(["NoSuchFontXYZ", family])["family"] in (regular_face["family"], family), family)
check("unknown font resolves to nothing", index.resolve(["NoSuchFontXYZ123"]) is None)

ttc_face = next((f for f in index.faces if f.get("ttc") and f["index"] > 0 and f.get("ps")), None)
if ttc_face is None:
    skip("collection face extraction", "no .ttc/.otc with several faces installed")
else:
    out = font_resolver.extract_face(ttc_face, os.path.join(WORK, "extracted"))
    reread = font_resolver.read_font_file(out) if out else []
    check("collection face extracted to a standalone font", bool(reread) and reread[0]["ps"] == ttc_face["ps"], ttc_face["ps"])

# --------------------------------------------------------------------------- #
# text import
# --------------------------------------------------------------------------- #
K = constants.units_per_px


def text_width(obj):
    deps = bpy.context.evaluated_depsgraph_get()
    evaluated = obj.evaluated_get(deps)
    mesh = evaluated.to_mesh()
    xs = [v.co.x for v in mesh.vertices]
    evaluated.to_mesh_clear()
    return (max(xs) - min(xs)) if xs else 0.0


from toothpaste.scene_utils import _object_world_bounds  # noqa: E402


def block_bounds(objects):
    """Union of the world-space bounding boxes of some objects."""
    bpy.context.view_layer.update()
    deps = bpy.context.evaluated_depsgraph_get()
    boxes = [b for b in (_object_world_bounds(o, deps) for o in objects) if b]
    low = [min(b[0][i] for b in boxes) for i in range(3)]
    high = [max(b[1][i] for b in boxes) for i in range(3)]
    return low, high


def import_text(body, mode="CURVE", text_mode="TEXT"):
    path = write("t.svg", body)
    clear_scene()
    bpy.context.scene.toothpaste_text_mode = text_mode
    settings_obj = ImportSettings.from_scene(bpy.context.scene)
    root = parse_svg_root(open(path, encoding="utf-8").read())
    return import_svg_root(bpy.context, root, mode, settings_obj)


illustrator = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 400 300">'
    "<style>.st0{font-family:'FONT';} .st1{font-size:24px;}</style>"
    '<g><text transform="matrix(1 0 0 1 100 50)" class="st0 st1">Hello World</text></g></svg>'
)

if family is not None:
    result = import_text(illustrator.replace("FONT", bold_face["ps"]))
    obj = result["texts"][0]
    check("text object created", len(result["texts"]) == 1)
    check("text body", obj.data.body == "Hello World", obj.data.body)
    expected_size = 24 * result["frame"].units_per_user * bold_face["bbox_h"]
    check("text size is the real font size scaled by the font's own factor", near(obj.data.size, expected_size), f"{obj.data.size:.6f} vs {expected_size:.6f}")
    low, high = block_bounds([obj])
    check("a lone text block is centred on the paste target", abs((low[0] + high[0]) / 2) < 1e-5 and abs((low[1] + high[1]) / 2) < 1e-5, (low, high))
    loaded = obj.data.font.filepath if obj.data.font else ""
    expected_file = os.path.basename(bold_face["path"])
    check(
        "matched font file is loaded",
        loaded != "<builtin>" and bool(loaded) and (expected_file in loaded or bold_face["ps"].replace("-", "_") in loaded or bold_face.get("ttc")),
        loaded,
    )
    check("no warnings for an installed font", result["warnings"] == [], result["warnings"])

    # the font really changes the glyphs: bold text is wider than regular text
    bold_obj = import_text(illustrator.replace("FONT", bold_face["ps"]))["texts"][0]
    bold_width = text_width(bold_obj)
    regular_obj = import_text(illustrator.replace("FONT", regular_face["ps"]))["texts"][0]
    regular_width = text_width(regular_obj)
    check("bold and regular text differ in width", abs(bold_width - regular_width) > 1e-6, f"{bold_width:.4f} vs {regular_width:.4f}")

    # weight requested through CSS rather than the font name
    css_bold = import_text(
        '<svg xmlns="http://www.w3.org/2000/svg"><text x="0" y="20" font-family="%s" font-weight="bold" font-size="20">Hello World</text></svg>' % family
    )["texts"][0]
    check("font-weight selects the bold face", near(text_width(css_bold), bold_width * (20.0 / 24.0), 0.02), f"{text_width(css_bold):.4f}")
    check("bold/italic slots filled from the family", css_bold.data.font_bold is not None and css_bold.data.font_bold.filepath != "<builtin>")
else:
    skip("text font loading", "no suitable installed font")

missing = import_text(illustrator.replace("FONT", "NoSuchFontXYZ123"))
check("missing font still imports the text", len(missing["texts"]) == 1)
check("missing font is reported", any("NoSuchFontXYZ123" in w for w in missing["warnings"]), missing["warnings"])

layout = import_text(
    '<svg xmlns="http://www.w3.org/2000/svg" xmlns:sodipodi="http://sodipodi.sourceforge.net/DTD/sodipodi-0.dtd">'
    '<g transform="translate(10 20)"><text transform="rotate(90)" style="font-size:20px;text-anchor:middle;fill:#ff0000">'
    '<tspan x="0" y="0">Line one</tspan><tspan x="0" y="24">Line two</tspan></text></g></svg>'
)["texts"][0]
check("tspan lines become text lines", layout.data.body == "Line one\nLine two", repr(layout.data.body))
check("line pitch preserved (1.2 lines, built-in font factor)", near(layout.data.space_line, 1.2 / obj_text.BUILTIN_FONT_BBOX_H, 0.01), layout.data.space_line)
check("text-anchor middle centres the text", layout.data.align_x == "CENTER", layout.data.align_x)
check("SVG rotation applied (clockwise -> Blender z)", near(layout.rotation_euler.z, -math.pi / 2, 0.01), layout.rotation_euler.z)
check("text colour becomes a material", bool(layout.data.materials) and near(layout.data.materials[0].diffuse_color[0], 1.0, 0.01))

pair = import_text(
    '<svg xmlns="http://www.w3.org/2000/svg"><text x="0" y="0" font-size="10">A</text><text x="100" y="0" font-size="10">B</text></svg>'
)["texts"]
spread = abs(pair[1].location.x - pair[0].location.x)
check("several texts keep their spacing", near(spread, 100 * K("CURVE"), 0.01), f"{spread:.6f}")
low, high = block_bounds(pair)
check("block of texts centred on the origin", abs((low[0] + high[0]) / 2) < 1e-5 and abs((low[1] + high[1]) / 2) < 1e-5, (low, high))

# text as outlines: converted in place to the shape type of the chosen output
outline_svg = (
    '<svg xmlns="http://www.w3.org/2000/svg"><text transform="matrix(1 0 0 1 40 60) rotate(10)" '
    'font-size="48" font-family="sans-serif" fill="#ff0000">OB</text></svg>'
)
check("editable text is the default", bpy.context.scene.toothpaste_text_mode == "TEXT")
editable = import_text(outline_svg, "CURVE", "TEXT")["texts"][0]
editable_width = text_width(editable)

as_curve = import_text(outline_svg, "CURVE", "OUTLINES")
check("outlines in Curves mode become curve objects", as_curve["texts"][0].type == "CURVE", as_curve["texts"][0].type)
check("curve outlines have the same size as the text", near(max(v[0] for v in as_curve["texts"][0].bound_box) - min(v[0] for v in as_curve["texts"][0].bound_box), editable_width, 0.35), editable_width)
check("curve outlines keep the text colour", bool(as_curve["texts"][0].data.materials))
low, high = block_bounds(as_curve["texts"])
check("curve outlines are centred on the paste target", abs((low[0] + high[0]) / 2) < 1e-5, (low, high))
check("curve outlines report no warnings", as_curve["warnings"] == [], as_curve["warnings"])

as_gp = import_text(outline_svg, "GP", "OUTLINES")
gp_text = as_gp["texts"][0]
check("outlines in Grease Pencil mode become Grease Pencil", gp_text.type == "GREASEPENCIL", gp_text.type)
if gp_text.type == "GREASEPENCIL":
    strokes = list(gp_text.data.layers[0].frames[0].drawing.strokes)
    check("GP outlines keep every glyph contour (letter holes included)", len(strokes) >= 3 and all(st.cyclic for st in strokes), len(strokes))
    check("GP outline layer is named after the text", gp_text.data.layers[0].name == gp_text.name, gp_text.data.layers[0].name)

# converting text must not disturb what the user had selected
clear_scene()
bpy.ops.mesh.primitive_cube_add()
cube = bpy.context.view_layer.objects.active
bpy.context.scene.toothpaste_text_mode = "OUTLINES"
import_svg_root(bpy.context, parse_svg_root(outline_svg), "CURVE", ImportSettings.from_scene(bpy.context.scene))
check("converting text restores the previous active object", bpy.context.view_layer.objects.active == cube)
check("converting text restores the previous selection", cube.select_get() and sum(1 for o in bpy.context.view_layer.objects if o.select_get()) == 1)
bpy.context.scene.toothpaste_text_mode = "TEXT"

# --------------------------------------------------------------------------- #
# images
# --------------------------------------------------------------------------- #
png = os.path.join(WORK, "wide.png")
img = bpy.data.images.new("src", width=200, height=100)
img.filepath_raw = png
img.file_format = "PNG"
img.save()

image_svg = (
    '<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" width="400" height="400">'
    '<g id="L"><rect id="Box" x="0" y="0" width="200" height="100" fill="#00f"/>'
    '<image id="Pic" x="0" y="0" width="200" height="100" xlink:href="%s"/></g></svg>' % png
)

for mode in ("GP", "CURVE"):
    for image_mode in ("REFERENCE", "MESH_PLANE"):
        clear_scene()
        bpy.context.scene.toothpaste_image_paste_mode = image_mode
        settings = ImportSettings.from_scene(bpy.context.scene)
        result = import_svg_root(bpy.context, parse_svg_root(image_svg), mode, settings)
        pic = result["images"][0]
        k = K(mode)
        check(f"{mode}/{image_mode} image object scale is 1,1,1", tuple(round(v, 6) for v in pic.scale) == (1.0, 1.0, 1.0), tuple(pic.scale))
        if image_mode == "REFERENCE":
            check(f"{mode}/{image_mode} image matches artwork scale", near(pic.empty_display_size, 200 * k), f"{pic.empty_display_size:.5f} vs {200 * k:.5f}")
        else:
            xs = [v.co.x for v in pic.data.vertices]
            ys = [v.co.y for v in pic.data.vertices]
            check(f"{mode}/{image_mode} width matches artwork scale", near(max(xs) - min(xs), 200 * k), f"{max(xs) - min(xs):.5f}")
            check(f"{mode}/{image_mode} height keeps aspect", near(max(ys) - min(ys), 100 * k), f"{max(ys) - min(ys):.5f}")

# the image and the rectangle it sits on are the same size in GP mode
clear_scene()
bpy.context.scene.toothpaste_image_paste_mode = "REFERENCE"
result = import_svg_root(bpy.context, parse_svg_root(image_svg), "GP", ImportSettings.from_scene(bpy.context.scene))
gp = result["grease_pencil"][0]
art_width = max(d.vector[0] for l in gp.data.layers for d in l.frames[0].drawing.attributes["position"].data) - min(
    d.vector[0] for l in gp.data.layers for d in l.frames[0].drawing.attributes["position"].data
)
check("image is the same width as the 200px rectangle under it", near(result["images"][0].empty_display_size, art_width, 0.02), f"{result['images'][0].empty_display_size:.5f} vs {art_width:.5f}")

# clipboard bitmaps: 1/10 of the old 0.1 per pixel
from toothpaste.obj_images import _create_image_object  # noqa: E402

clear_scene()
loaded_image = bpy.data.images.load(png)
clip = _create_image_object(bpy.context.scene, loaded_image, {"name": "Clip", "x": 0, "y": 0, "width": 1920.0, "height": 1080.0}, "MESH_PLANE")
xs = [v.co.x for v in clip.data.vertices]
check("clipboard image is 0.01 units per pixel", near(max(xs) - min(xs), 19.2), f"{max(xs) - min(xs):.3f}")
check("clipboard image scale is 1,1,1", tuple(round(v, 6) for v in clip.scale) == (1.0, 1.0, 1.0))

# --------------------------------------------------------------------------- #
# Grease Pencil edit controls
# --------------------------------------------------------------------------- #
gp_svg = write(
    "gp.svg",
    '<svg xmlns="http://www.w3.org/2000/svg" width="300" height="300"><g id="L">'
    '<rect id="A" x="0" y="0" width="100" height="50" fill="#f00" stroke="#00f" stroke-width="4"/>'
    '<rect id="B" x="150" y="0" width="100" height="50" fill="#0f0" stroke="#000" stroke-width="4"/></g></svg>',
)
clear_scene()
bpy.ops.toothpaste.import_svg(filepath=gp_svg, import_mode="GP", split_into_gp_layers=True)
gp = next(o for o in bpy.context.scene.objects if o.type == "GREASEPENCIL")
bpy.context.view_layer.objects.active = gp
gp.select_set(True)
scene = bpy.context.scene
layer_a, layer_b = gp.data.layers[0], gp.data.layers[1]


def first_stroke(layer):
    return layer.frames[0].drawing.strokes[0]


def snapshot(layer):
    return tuple(round(p.radius, 5) for p in first_stroke(layer).points)


before_a, before_b = snapshot(layer_a), snapshot(layer_b)

# outside edit mode nothing may change
scene.toothpaste_gp_thickness = 0.5
check("thickness change outside edit mode does nothing", snapshot(layer_a) == before_a and snapshot(layer_b) == before_b)

bpy.ops.object.mode_set(mode="EDIT")
bpy.ops.grease_pencil.select_all(action="DESELECT")
gp.data.layers.active = layer_a
bpy.ops.toothpaste.gp_select_layer_strokes(action="SELECT")
check("layer select button selects strokes", first_stroke(layer_a).select and not first_stroke(layer_b).select)

scene.toothpaste_gp_thickness = 0.02
check("thickness sets radius to half", all(abs(r - 0.01) < 1e-5 for r in snapshot(layer_a)), snapshot(layer_a)[:2])
check("thickness leaves unselected points alone", snapshot(layer_b) == before_b)

# stroke type / direction go through Blender's own operators
bpy.ops.toothpaste.gp_set_stroke_type(stroke_type="FILL")
check("Fill hides the stroke on the selection", first_stroke(layer_a).hide_stroke is True)
bpy.ops.toothpaste.gp_set_stroke_type(stroke_type="BOTH")
check("Both shows the stroke again", first_stroke(layer_a).hide_stroke is False)
b_hidden_before = first_stroke(layer_b).hide_stroke
check("stroke type leaves unselected strokes alone", first_stroke(layer_b).hide_stroke == b_hidden_before)

start = tuple(round(c, 4) for c in first_stroke(layer_a).points[0].position)
b_start = tuple(round(c, 4) for c in first_stroke(layer_b).points[0].position)
bpy.ops.toothpaste.gp_reverse_direction()
reversed_start = tuple(round(c, 4) for c in first_stroke(layer_a).points[0].position)
check("reverse direction flips the selected stroke", reversed_start != start)
check("reverse direction leaves unselected strokes alone", tuple(round(c, 4) for c in first_stroke(layer_b).points[0].position) == b_start)
bpy.ops.toothpaste.gp_reverse_direction()
check("reversing twice restores the stroke", tuple(round(c, 4) for c in first_stroke(layer_a).points[0].position) == start)

bpy.ops.toothpaste.gp_select_layer_strokes(action="DESELECT")
check("layer deselect button deselects strokes", not first_stroke(layer_a).select)
scene.toothpaste_gp_thickness = 0.03
check("nothing selected means nothing changes", all(abs(r - 0.01) < 1e-5 for r in snapshot(layer_a)))

bpy.ops.object.mode_set(mode="OBJECT")
check("panel polls true for a GP object", toothpaste.TOOTHPASTE_PT_gp_edit.poll(bpy.context))
bpy.ops.mesh.primitive_cube_add()
check("panel polls false for a mesh", not toothpaste.TOOTHPASTE_PT_gp_edit.poll(bpy.context))


# --------------------------------------------------------------------------- #
# placement: text and images land where the SVG puts them, art faces one way
# --------------------------------------------------------------------------- #
def art_bounds(obj):
    """World bounds of imported artwork (Grease Pencil by its points, curves by their box)."""
    bpy.context.view_layer.update()
    if obj.type == "GREASEPENCIL":
        from mathutils import Vector

        pts = [
            obj.matrix_world @ Vector(d.vector)
            for l in obj.data.layers
            for f in l.frames
            for d in f.drawing.attributes["position"].data
        ]
        return [min(p[i] for p in pts) for i in range(3)], [max(p[i] for p in pts) for i in range(3)]
    return block_bounds([obj])


def import_doc(svg, mode, text_mode="TEXT", image_mode="REFERENCE"):
    path = write("doc.svg", svg)
    clear_scene()
    scene = bpy.context.scene
    scene.toothpaste_text_mode = text_mode
    scene.toothpaste_image_paste_mode = image_mode
    return import_svg_root(bpy.context, parse_svg_root(open(path, encoding="utf-8").read()), mode, ImportSettings.from_scene(scene))


def art_of(result, mode):
    return (result["grease_pencil"] if mode == "GP" else result["curves"])[0]


affinity = (
    '<svg width="100%" height="100%" viewBox="0 0 960 539" xmlns="http://www.w3.org/2000/svg" xml:space="preserve">'
    '<g transform="matrix(1,0,0,1,-0,-1461.146333)"><g transform="matrix(1,0,0,1,1905,1461.146333)">'
    '<g transform="matrix(5.783133,0,0,3.322883,-4623.072289,0)"><rect x="470" y="0" width="166" height="162" style="fill:rgb(0,159,234);"/></g></g>'
    '<g transform="matrix(1,0,0,1,1905,1461.146333)"><g transform="matrix(1,0,0,1,-1905,-0)">'
    "<text x=\"36.2px\" y=\"71.835px\" style=\"font-family:'ArialMT', 'Arial', sans-serif;font-size:36px;fill:white;\">Primary</text></g></g></g></svg>"
)

for mode in ("GP", "CURVE"):
    label = mode
    result = import_doc(affinity, mode)
    art = art_of(result, mode)
    text = result["texts"][0]
    k = result["frame"].units_per_user
    a_lo, a_hi = art_bounds(art)
    t_lo, t_hi = block_bounds([text])
    up, depth = 1, 2  # flat: the artwork's vertical axis is Y, and it faces +Z
    check(f"{label}: art is centred on the origin", abs((a_lo[0] + a_hi[0]) / 2) < 0.02 * (a_hi[0] - a_lo[0]) and abs((a_lo[up] + a_hi[up]) / 2) < 0.02 * (a_hi[up] - a_lo[up]), (a_lo, a_hi))
    check(f"{label}: art lies flat, facing up", (a_hi[depth] - a_lo[depth]) < 0.05 * (a_hi[0] - a_lo[0]), (a_hi[depth] - a_lo[depth]))
    check(f"{label}: text lies flat, facing up", (t_hi[depth] - t_lo[depth]) < 1e-4, (t_lo, t_hi))
    check(f"{label}: art is 960 px wide", near(a_hi[0] - a_lo[0], 960 * k, 0.03), f"{a_hi[0] - a_lo[0]:.4f} vs {960 * k:.4f}")
    # text origin is 36.2 px from the left edge and its baseline 71.8 px below the top
    left_gap = t_lo[0] - a_lo[0]
    check(f"{label}: text starts 36 px from the left edge", abs(left_gap - 36.2 * k) < 0.12 * 36 * k, f"{left_gap / k:.1f} px")
    baseline = a_hi[up] - 71.835 * k
    check(f"{label}: text sits on its baseline 72 px below the top", t_lo[up] <= baseline + 1e-4 and t_hi[up] > baseline, f"{(a_hi[up] - t_hi[up]) / k:.1f}..{(a_hi[up] - t_lo[up]) / k:.1f} px")
    check(f"{label}: text is inside the rectangle", t_lo[0] >= a_lo[0] and t_hi[0] <= a_hi[0] and t_lo[up] >= a_lo[up] and t_hi[up] <= a_hi[up])

# the probe import used to measure placement must not leave anything behind
plain_doc = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 400 300"><rect x="10" y="10" width="100" height="50" fill="#00f"/></svg>'
text_doc = plain_doc.replace("</svg>", '<text x="20" y="100" font-size="20" font-family="Arial">Hi</text></svg>')


def orphan_names():
    return sorted(d.name for coll in (bpy.data.curves, bpy.data.materials, bpy.data.grease_pencils) for d in coll if d.users == 0)


for mode in ("GP", "CURVE"):
    import_doc(plain_doc, mode)
    baseline_orphans = orphan_names()  # Blender's own curve importer leaves one itself
    import_doc(text_doc, mode)
    check(f"{mode}: probe leaves no extra orphaned data", orphan_names() == baseline_orphans, (orphan_names(), baseline_orphans))
    extra = [c.name for c in bpy.data.collections if c.name not in {"Pasted SVG", "Pasted Text", "Pasted Image"}]
    check(f"{mode}: probe leaves no stray collections", not extra, extra)
    check(f"{mode}: probe leaves no extra objects (artwork + text only)", len(bpy.context.scene.objects) == 2, [o.name for o in bpy.context.scene.objects])

# Grease Pencil and Curves lie in the same plane
gp_result = import_doc(affinity, "GP")
gp_lo, gp_hi = art_bounds(art_of(gp_result, "GP"))
curve_result = import_doc(affinity, "CURVE")
cu_lo, cu_hi = art_bounds(art_of(curve_result, "CURVE"))
check("Grease Pencil and Curves lie in the same flat plane", (gp_hi[2] - gp_lo[2]) < 0.05 * (gp_hi[0] - gp_lo[0]) and (cu_hi[2] - cu_lo[2]) < 0.05 * (cu_hi[0] - cu_lo[0]))

# a viewBox that does not start at 0,0 and a width/height that rescales it
for name, svg, expect_px, scale in (
    ("offset viewBox", '<svg xmlns="http://www.w3.org/2000/svg" viewBox="100 50 200 150"><rect x="100" y="50" width="200" height="150" fill="#00f"/><text x="130" y="110" font-size="20" font-family="Arial">Hi</text></svg>', (30, 60), 1.0),
    ("width/height 2x", '<svg xmlns="http://www.w3.org/2000/svg" width="800" height="600" viewBox="0 0 400 300"><rect x="0" y="0" width="400" height="300" fill="#00f"/><text x="30" y="60" font-size="20" font-family="Arial">Hi</text></svg>', (30, 60), 2.0),
):
    for mode in ("GP", "CURVE"):
        result = import_doc(svg, mode)
        a_lo, a_hi = art_bounds(art_of(result, mode))
        t_lo, t_hi = block_bounds(result["texts"])
        k = result["frame"].units_per_user
        # k already includes the 2x of width/height; 'expect_px' is in SVG user units
        left = (t_lo[0] - a_lo[0]) / k
        check(f"{name}/{mode}: text keeps its horizontal offset", abs(left - expect_px[0]) < 5, f"{left:.1f} vs {expect_px[0]} user units")
        baseline_from_top = (a_hi[1] - t_lo[1]) / k
        check(f"{name}/{mode}: text sits at its vertical offset", abs(baseline_from_top - expect_px[1]) < 10, f"{baseline_from_top:.1f} vs ~{expect_px[1]} user units")
        check(f"{name}/{mode}: scale follows width/height", near(a_hi[0] - a_lo[0], (200 if "offset" in name else 400) * k, 0.03))

# images are drawn on the artwork they belong to
image_doc = (
    '<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" viewBox="0 0 400 300">'
    '<rect x="0" y="0" width="400" height="300" fill="#eee"/>'
    '<rect id="Box" x="50" y="40" width="200" height="100" fill="#00f"/>'
    '<image x="50" y="40" width="200" height="100" xlink:href="%s"/></svg>' % png
)
for mode in ("GP", "CURVE"):
    for image_mode in ("REFERENCE", "MESH_PLANE"):
        result = import_doc(image_doc, mode, image_mode=image_mode)
        i_lo, i_hi = block_bounds(result["images"])
        art = art_of(result, mode) if mode == "CURVE" else None
        # compare against the full-canvas background: the image must sit 50/40 px in from its corner
        a_lo, a_hi = art_bounds(art_of(result, mode)) if mode == "GP" else block_bounds(result["curves"])
        k = result["frame"].units_per_user
        check(f"{mode}/{image_mode}: image is 200x100 px", near(i_hi[0] - i_lo[0], 200 * k, 0.03) and near(i_hi[1] - i_lo[1], 100 * k, 0.03), f"{(i_hi[0] - i_lo[0]) / k:.1f} x {(i_hi[1] - i_lo[1]) / k:.1f}")
        check(f"{mode}/{image_mode}: image is anchored at its top-left corner", abs((i_lo[0] - a_lo[0]) / k - 50) < 3 and abs((a_hi[1] - i_hi[1]) / k - 40) < 3, f"{(i_lo[0] - a_lo[0]) / k:.1f}, {(a_hi[1] - i_hi[1]) / k:.1f}")

# draw order follows the SVG: text after a shape is in front of it, an image before every shape is behind it
order_doc = (
    '<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" viewBox="0 0 400 300">'
    '<image x="0" y="0" width="400" height="300" xlink:href="%s"/>'
    '<rect x="50" y="50" width="100" height="100" fill="#00f"/>'
    '<text x="60" y="100" font-size="20" font-family="Arial">Hi</text>'
    '<image x="200" y="50" width="100" height="100" xlink:href="%s"/></svg>' % (png, png)
)
for mode in ("GP", "CURVE"):
    result = import_doc(order_doc, mode, image_mode="MESH_PLANE")
    k = result["frame"].units_per_user
    below, above = sorted(result["images"], key=lambda o: o.location.z)
    check(f"{mode}: a background image is behind the artwork", below.location.z < 0, below.location.z)
    check(f"{mode}: an image drawn after a shape is in front of it", above.location.z > 0, above.location.z)
    check(f"{mode}: text after a shape is in front of the artwork", result["texts"][0].location.z > 0, result["texts"][0].location.z)
    check(f"{mode}: the depth offset is one SVG unit", near(abs(result["texts"][0].location.z), k, 0.02), f"{result['texts'][0].location.z:.6f} vs {k:.6f}")

# font size: a real point size, whatever font is used
if family is not None:
    def cap_height_px(font_name):
        result = import_doc(
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 400 300"><text x="0" y="100" font-size="100" font-family="%s">H</text></svg>' % font_name,
            "GP",
        )
        low, high = block_bounds(result["texts"])
        return (high[1] - low[1]) / result["frame"].units_per_user

    def cap_ratio(face):
        """Cap height in em, read from the font's OS/2 table (None when the font has none)."""
        import struct

        with open(face["path"], "rb") as handle:
            offset = 0
            if handle.read(4) == b"ttcf":
                handle.seek(12 + 4 * face["index"])
                offset = struct.unpack(">I", handle.read(4))[0]
            _version, tables = font_resolver._read_tables(handle, offset)
            os2 = font_resolver._read_table(handle, tables, "OS/2")
            head = font_resolver._read_table(handle, tables, "head")
        if len(os2) < 90 or struct.unpack(">H", os2[0:2])[0] < 2:
            return None
        cap = struct.unpack(">h", os2[88:90])[0]
        return cap / struct.unpack(">H", head[18:20])[0] if cap > 0 else None

    checked = 0
    for candidate in ("Arial", "Courier New", "Times New Roman", "Georgia", "Verdana", "Menlo", "Helvetica Neue"):
        face = index.resolve([candidate])
        if face is None or normalize_name(face["family"]) != normalize_name(candidate):
            continue
        expected = cap_ratio(face)
        if not expected:
            continue
        measured = cap_height_px(candidate) / 100.0
        check(f"{candidate}: 100 px text has the font's real cap height", near(measured, expected, 0.04), f"{measured:.3f} vs {expected:.3f} em")
        checked += 1
    if not checked:
        skip("font size by real metrics", "none of the reference fonts are installed")

    # two lines 24 px apart: block height = one cap height + the line pitch
    def block_height(svg_body):
        result = import_doc(
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 400 300"><text font-size="20" font-family="%s">%s</text></svg>' % (family, svg_body),
            "GP",
        )
        low, high = block_bounds(result["texts"])
        return (high[1] - low[1]) / result["frame"].units_per_user

    one_line = block_height('<tspan x="0" y="50">HH</tspan>')
    two_lines = block_height('<tspan x="0" y="50">HH</tspan><tspan x="0" y="74">HH</tspan>')
    check("line spacing reproduces the SVG's 24 px", abs((two_lines - one_line) - 24.0) < 0.5, f"{two_lines - one_line:.2f} px")

# curve stacking uses the artwork's own depth axis (Z = toward the viewer)
stack_doc = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 200 100"><g id="One"><rect x="0" y="0" width="80" height="80" fill="#f00"/></g>'
    '<g id="Two"><rect x="20" y="10" width="80" height="80" fill="#0f0"/></g></svg>'
)
clear_scene()
scene = bpy.context.scene
scene.toothpaste_curve_stack_enabled = True
scene.toothpaste_curve_stack_axis = "Z"
scene.toothpaste_curve_stack_depth = 0.1
result = import_svg_root(bpy.context, parse_svg_root(stack_doc), "CURVE", ImportSettings.from_scene(scene))
one, two = result["curves"][0], result["curves"][1]
offset = two.location[2] - one.location[2]
check("stacking Z moves the next layer up", near(offset, 0.1, 0.02), f"{offset:.4f}")
scene.toothpaste_curve_stack_enabled = False

# --------------------------------------------------------------------------- #
# shared options layout: tabs for the output type, only that type's options
# --------------------------------------------------------------------------- #
from toothpaste.ui_panels import draw_toothpaste_options  # noqa: E402


class FakeLayout:
    """Minimal UILayout that records what a draw function touches."""

    def __init__(self, sink):
        self.sink = sink

    def prop(self, data, name, **kwargs):
        self.sink.append(("prop", name))

    def prop_enum(self, data, name, value, **kwargs):
        self.sink.append(("prop", name))
        self.sink.append(("enum", f"{name}:{value}:{kwargs.get('text', '')}"))

    def operator(self, idname, **kwargs):
        self.sink.append(("op", idname))
        return type("Result", (), {})()

    def label(self, **kwargs):
        self.sink.append(("label", kwargs.get("text", "")))

    def separator(self, **kwargs):
        pass

    def row(self, **kwargs):
        return FakeLayout(self.sink)

    def column(self, **kwargs):
        return FakeLayout(self.sink)


def drawn(data, prefix, paste_buttons):
    sink = []
    draw_toothpaste_options(FakeLayout(sink), data, prefix=prefix, paste_buttons=paste_buttons)
    return sink


def has(sink, kind, name):
    """Operators and properties match exactly; enum entries match by prefix."""
    if kind == "enum":
        return any(k == kind and n.startswith(name) for k, n in sink)
    return any(k == kind and n == name for k, n in sink)


scene = bpy.context.scene
scene.toothpaste_import_mode = "GP"
gp_panel = drawn(scene, "toothpaste_", True)
check("panel (GP tab): one paste button, for Grease Pencil", has(gp_panel, "op", "toothpaste.paste_svg") and not has(gp_panel, "op", "toothpaste.paste_svg_curve"))
check("panel (GP tab): GP options shown, curve options hidden", has(gp_panel, "prop", "toothpaste_split_into_gp_layers") and not has(gp_panel, "prop", "toothpaste_curve_stack_enabled"))
check("panel (GP tab): text choice is labelled Grease Pencil", has(gp_panel, "enum", "toothpaste_text_mode:OUTLINES:Grease Pencil"), [x for x in gp_panel if x[0] == "enum"])
scene.toothpaste_import_mode = "CURVE"
curve_panel = drawn(scene, "toothpaste_", True)
check("panel (Curves tab): one paste button, for Curves", has(curve_panel, "op", "toothpaste.paste_svg_curve") and not has(curve_panel, "op", "toothpaste.paste_svg"))
check("panel (Curves tab): curve options shown, GP options hidden", has(curve_panel, "prop", "toothpaste_curve_stack_enabled") and not has(curve_panel, "prop", "toothpaste_split_into_gp_layers"))
check("panel (Curves tab): text choice is labelled Curves", has(curve_panel, "enum", "toothpaste_text_mode:OUTLINES:Curves"))
check("panel has no facing choice", not any("orientation" in n for k, n in gp_panel + curve_panel))
check("panel tabs: Grease Pencil, Curves and Image", all(has(gp_panel, "enum", f"toothpaste_import_mode:{v}") for v in ("GP", "CURVE", "IMAGE")))
scene.toothpaste_import_mode = "IMAGE"
image_panel = drawn(scene, "toothpaste_", True)
check("panel (Image tab): the paste button is the clipboard image one", has(image_panel, "op", "toothpaste.paste_clipboard_image") and not has(image_panel, "op", "toothpaste.paste_svg") and not has(image_panel, "op", "toothpaste.paste_svg_curve"))
check("panel (Image tab): image mode and location shown", has(image_panel, "prop", "toothpaste_image_paste_mode") and has(image_panel, "prop", "toothpaste_paste_location"))
check("panel (Image tab): SVG-only options hidden", not any(n in ("toothpaste_split_into_gp_layers", "toothpaste_curve_stack_enabled", "toothpaste_text_mode") for k, n in image_panel if k == "prop") and not has(image_panel, "enum", "toothpaste_text_mode"))
check("panel (GP and Curves tabs): no clipboard image button", not has(gp_panel, "op", "toothpaste.paste_clipboard_image") and not has(curve_panel, "op", "toothpaste.paste_clipboard_image"))
scene.toothpaste_import_mode = "GP"
check("panel offers editable text in both tabs", has(gp_panel, "enum", "toothpaste_text_mode:TEXT:Editable Text") and has(curve_panel, "enum", "toothpaste_text_mode:TEXT:Editable Text"))

scene_props = {p.identifier for p in scene.bl_rna.properties}
check("panel draws only real scene properties", all(n in scene_props for k, n in gp_panel + curve_panel if k == "prop"), [n for k, n in gp_panel + curve_panel if k == "prop" and n not in scene_props])

class DialogStub:
    import_mode = "GP"
    split_into_gp_layers = True

dialog_gp = drawn(DialogStub, "", False)
check("dialog has no paste buttons", not any(k == "op" for k, _ in dialog_gp))
check("dialog draws only real operator properties", all(n in op_props for k, n in dialog_gp if k == "prop"), [n for k, n in dialog_gp if k == "prop" and n not in op_props])
check("dialog shows the same tabs (without Image) and text choice as the panel", has(dialog_gp, "enum", "import_mode:GP") and has(dialog_gp, "enum", "import_mode:CURVE") and not has(dialog_gp, "enum", "import_mode:IMAGE") and has(dialog_gp, "enum", "text_mode:OUTLINES:Grease Pencil"))
scene.toothpaste_import_mode = "GP"

# --------------------------------------------------------------------------- #
# groups, paste operators, sample files
# --------------------------------------------------------------------------- #
check("single-object group collapsed", _collapse_single_object_groups({"a": ["Root", "Solo"]})["a"] == [])
check("shared group kept", _collapse_single_object_groups({"a": ["Root", "P"], "b": ["Root", "P"]})["a"] == ["Root", "P"])

import toothpaste.ops_paste as ops_paste  # noqa: E402

clip_svg = open(rect, encoding="utf-8").read()
ops_paste.get_clipboard_svg = lambda: clip_svg
clear_scene()
check("paste as Grease Pencil", "FINISHED" in bpy.ops.toothpaste.paste_svg())
clear_scene()
check("paste as Curves", "FINISHED" in bpy.ops.toothpaste.paste_svg_curve())

samples = os.path.join(SRC, "toothpaste_tests")
if os.path.isdir(samples):
    for name in sorted(f for f in os.listdir(samples) if f.endswith(".svg")):
        path = os.path.join(samples, name)
        try:
            data = open(path, encoding="utf-8").read()
            parse_svg(data)
            _prepare_import_plan(parse_svg_root(data), split_layers=True)
            clear_scene()
            ok = all("FINISHED" in bpy.ops.toothpaste.import_svg(filepath=path, import_mode=m) for m in ("GP", "CURVE"))
            check(f"sample {name}", ok)
        except Exception:
            check(f"sample {name}", False, traceback.format_exc())
else:
    skip("sample SVG files", "no toothpaste_tests folder")

# --------------------------------------------------------------------------- #
toothpaste.unregister()
check("unregister removes scene props", not hasattr(bpy.types.Scene, "toothpaste_gp_thickness") and not hasattr(bpy.types.Scene, "toothpaste_paste_location"))
check("unregister removes the layer buttons", not any(getattr(f, "__name__", "") == "draw_gp_layer_selection" for f in bpy.types.DATA_PT_grease_pencil_layers._dyn_ui_initialize()))

shutil.rmtree(WORK, ignore_errors=True)
print("\n================ SUMMARY ================")
print(f"Blender {bpy.app.version_string}: {len(failures)} failure(s), {len(skipped)} skipped")
for item in failures:
    print(" -", item)
sys.exit(1 if failures else 0)
