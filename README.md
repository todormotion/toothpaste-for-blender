# ToothPaste for Blender v1.1.7

Paste or import SVG content into Blender as Grease Pencil, Curves, Text, and Images.

## Current Scope

ToothPaste is focused on Blender 5.0+ including Blender 5.2 LTS (Grease Pencil v3) and supports practical SVG copy/paste and file import workflows from:
- Affinity Designer
- Adobe Illustrator
- Figma
- Inkscape

## Features

- Editable text in the font the SVG asks for (`Text > Editable Text`), or text turned into Grease Pencil / Curves shapes (`Text > Grease Pencil / Curves`)
- Grease Pencil Edit panel: `Stroke` / `Fill` / `Both`, thickness, and reverse direction for the selected strokes in Edit Mode
- One `Paste as:` tab (`Grease Pencil` / `Curves` / `Image`) with a single paste button, showing only the options that apply
- Text and images land exactly where the SVG puts them, at the same scale as the artwork, and the whole paste is centred on the paste target
- Grease Pencil, Curves, text and images all lie flat, face up, and come in at the same size, so a paste is one consistent layout
- Blender 5.2 LTS support
- Import SVG files from `File > Import > SVG (ToothPaste)`
- Drag and drop an SVG file into the viewport and pick `SVG (ToothPaste)` from Blender's drop menu
- Materials transfer from SVG to Grease Pencil correctly and stay editable after import
- Multilevel masks are supported
- Inkscape clipboard paste prioritizes SVG/vector data over image clipboard flavors
- Select or deselect every stroke on the active Grease Pencil layer, from Object Data Properties > Layers
- Paste SVG from clipboard as Grease Pencil (`Paste as Grease Pencil`)
- Paste SVG from clipboard as Curves (`Paste as Curves`)
- Split imported SVG into GP layers (`Split into GP Layers`)
- Split existing multi-layer GP object into separate GP objects (`Split layers into objects`)
- Rebuild SVG mask/clip relationships using GP layer masks + holdout where possible
- Import SVG text as Blender `FONT` objects
- Import SVG `<image>` elements (embedded base64 or valid local paths)
- Paste bitmap image from clipboard using Blender built-in image paste
- Paste image as `Mesh Plane` or `Reference`
- Paste location controls for all modes (`World Origin` or `Cursor`)
- GP material mapping for fill + stroke color
- Shared SVG styles reuse the same GP material
- Imported GP objects stay material-driven so later material edits remain visible in the viewport
- Partial gradient mapping from SVG gradients to GP Fill Gradient

## Gradient Support (Partial)

Gradients are supported on a best-effort basis for Grease Pencil fill materials:
- Linear and radial SVG gradients are detected
- Base/secondary colors are mapped from gradient stops
- Gradient transform/location/rotation/scale are approximated to GP material controls

Important:
- Gradient mapping is still not 1:1 with source editors in all cases
- Some gradients will need manual tweaking after import
- This is currently partial support, not full SVG gradient parity

## Requirements

- Blender 5.0.0+ including Blender 5.2 LTS
- Windows, macOS, or Linux

## Installation

1. Open Blender
2. Go to `Edit > Preferences > Add-ons`
3. Click `Install...`
4. Select the addon ZIP (or repository folder ZIP)
5. Enable `Import-Export: ToothPaste for Blender`

## Usage

Paste from the clipboard:

1. Copy SVG content from your design app
2. Open `View3D > Sidebar (N) > ToothPaste`
3. Use the grouped tools:
   - `Grease Pencil`
   - `Curves & Text`
   - `Images`
   - `Paste at:`

Import a file:

- `File > Import > SVG (ToothPaste)` opens the file browser, with every ToothPaste option in its sidebar
- Dragging an `.svg` file into the 3D viewport opens Blender's drop menu; choose `SVG (ToothPaste)` to get the same options as a dialog
- Both routes start from whatever the sidebar panel is currently set to

### Panel Layout

- `Paste as:` `Grease Pencil` | `Curves` | `Image` - picks what to paste; one paste button follows it
  - Grease Pencil: `Split into GP Layers`, `Create GP Groups`, `Split layers into objects`
  - Curves: `Stack Curve Objects` (axis, depth, keep same SVG group level)
  - Both SVG tabs also show `Text` (`Editable Text` | `Grease Pencil` or `Curves`), `SVG images as:` (`Mesh Plane` / `Reference`) and `Paste at:`
  - Image: `Paste Image (Clipboard)`, `Image as:` (`Mesh Plane` / `Reference`) and `Paste at:`
- `Paste at:` `World Origin` / `Cursor`
- `Grease Pencil Edit` (only while a Grease Pencil object is active; controls work in Edit Mode)
  - `Show`: `Stroke` / `Fill` / `Both` (same as `Stroke > Set Stroke Type`)
  - `Thickness` slider for the selected points
  - `Reverse Direction` (same as `Stroke > Switch Direction`)

The file import dialog uses the same layout (without the clipboard `Image` tab, since it imports SVG files).

### Text and fonts

An SVG never contains its fonts. A `<text>` element only carries a font *name* (`font-family`), plus weight and style, so ToothPaste looks that name up among the fonts installed on your computer (system fonts, user fonts and activated Adobe Fonts) and loads the match into Blender.

- Weight and italic are matched too (`font-weight`, `font-style`, and PostScript names such as `Arial-BoldMT`)
- Several lines, alignment, rotation, size and colour are kept, and the text is sized to its real point size whatever the font (Blender sizes text by each font's bounding box, which ToothPaste compensates for)
- If a font is not installed, the text is still imported with Blender's default font and a warning names the missing font
- The first scan of your fonts can take a second; the result is cached for later imports
- Some apps turn text into shapes before the SVG leaves them (a setting in some, always in others). That text arrives as shapes, not as text
- `Text > Grease Pencil` / `Curves` converts the text to shapes instead, so it looks identical on any machine
- Editable text is a regular mesh-type text object. Over Grease Pencil artwork it shows correctly in the viewport, but Blender's final renders draw Grease Pencil over mesh objects whatever their depth, so use `Text > Grease Pencil` when the text has to appear in a render

### Placement and sizes

Blender's two SVG importers place the same file differently (the Curve importer flips the page around its height and treats `viewBox` its own way, the Grease Pencil importer anchors the top-left corner). To keep text and images lined up with the artwork, ToothPaste imports a tiny probe square through the same importer first, measures where its corner landed and how large it came out, deletes it, and positions text and images from that measurement. This works for any `viewBox`, `width`/`height` or units, so text sits where the SVG puts it, at the artwork's scale.

- The whole paste (artwork, text and images) is then centred on the paste target as one block
- Text and images are given a depth offset of one SVG unit following the SVG's own order: text or an image drawn after a shape sits in front of the artwork, one before every shape (a background photo) sits behind it
- Curves are scaled up when imported (Blender's curve importer makes an SVG pixel about 9x smaller than its Grease Pencil importer does), so a Curves paste is the same size as a Grease Pencil paste. The size is in the curve data; the object scale stays 1, 1, 1
- Everything lies flat on the ground plane facing up (+Z). Blender's Grease Pencil importer stands its art upright, so ToothPaste lays it down to match the other types
- Bitmaps pasted from the clipboard come in at 100 pixels per Blender unit

### Grease Pencil layer selection

With a Grease Pencil object selected, `Properties > Object Data Properties > Layers`
gains `Select Strokes` and `Deselect` buttons at the end of the panel. Both act on
the active layer and are greyed out until one is selected in the list.

### Import options in the file browser and drop dialog

`Import as:` chooses Grease Pencil or Curves. The section that does not apply to
that choice is dimmed, but every option stays in the same place as in the panel.

## SVG Support Summary

### Supported

- Geometry: `path`, `rect`, `circle`, `ellipse`, `line`, `polyline`, `polygon`
- Group hierarchy via top-level `<g>`
- CSS class-based styles in `<style>` blocks (common in Illustrator exports)
- Fill/stroke color mapping to GP materials
- Material edits after import remain live on the GP object
- Stroke width mapping (best effort)
- Mask/clip reconstruction to GP layer masks/holdout in common workflows
- Nested mask/clip relationships use the nearest parent mask layer for each imported GP layer
- Text extraction to Blender font objects
- SVG image extraction to Blender objects
- Images embedded only inside `defs`/`mask`/`clipPath` containers are ignored and not pasted as scene objects

### Limited / Partial

- Gradients: partial mapping only (see section above)
- Text layout fidelity (`tspan`, advanced typographic layout)
- Complex nested masking/clipping edge cases
- Very complex paths and arcs may differ slightly

### Not Fully Supported

- Full SVG rendering parity with browser engines
- All filter/pattern/blend-mode effects
- Full transform semantics across every editor/export variant

## Troubleshooting

- `No valid SVG layers, text, or image content found`
  - Ensure SVG has visible geometry/text/image data
  - Try grouping artwork into top-level groups
- `Clipboard does not contain valid SVG`
  - Copy raw SVG text or copy as SVG from your editor
- `Failed to create image object`
  - Switch image mode (`Mesh Plane`/`Reference`) and retry
  - Check clipboard/image data validity
- Masking mismatch
  - Share the source SVG export and expected Blender mask setup

## Notes for Packaging

`blender_manifest.toml` excludes non-essential packaging paths (including `images/`) for cleaner builds.

## Changelog

### v1.1.7 (current line)

**Added**

- Text now imports as editable Blender text in the font the SVG names, found among your installed fonts (system, user and Adobe Fonts), including weight, italic and `.ttc` collections, with line breaks, alignment, rotation, size and colour. Missing fonts and mixed-style text are reported when importing
- `Text:` `Editable Text` | `Grease Pencil` / `Curves`, to convert text to shapes instead
- `Grease Pencil Edit` panel with `Stroke` / `Fill` / `Both`, a thickness slider and reverse direction for the selected strokes in Edit Mode
- `Paste as:` is now a `Grease Pencil | Curves | Image` toggle with one paste button, showing only the options that apply. The file import dialog shares the same layout (without `Image`)
- `dev/test_blender.py`, an integration test suite (180 checks) to run against each new Blender version

**Changed**

- Grease Pencil, Curves, text and images now all lie flat, face up and come in at the same size. Grease Pencil used to stand upright while curves lay flat, and curves came in about 9x smaller than Grease Pencil
- Text and images land exactly where the SVG puts them. Their position and size are measured from Blender's own importer, so any `viewBox`, `width`/`height` or units works, and the whole paste is centred on the paste target as one block. Draw order follows the SVG: text or an image drawn after a shape sits in front of it, a background image sits behind
- Clipboard bitmaps come in at 100 pixels per Blender unit, 1/10 of their previous size

**Fixed**

- Text was the wrong size: Blender scales text differently per font, so text is now sized by each font's own metrics and matches the SVG's point size
- Text positions ignored nested `transform`s, which is how Affinity and Illustrator write them
- Reference images were centred on their corner instead of anchored at the top-left
- A collection-cleanup helper never worked on Blender 5.2

### v1.1.6

- Added `File > Import > SVG (ToothPaste)` and drag-and-drop import for `.svg` files, sharing one options layout and one import pipeline with clipboard paste
- Added `Select Strokes` / `Deselect` buttons for the active Grease Pencil layer to `Object Data Properties > Layers`
- Added Inkscape-friendly clipboard MIME prioritization so SVG/vector clipboard content is preferred over image data
- Changed minimum Blender version to 5.0 and added Blender 5.2 LTS support
- Fixed pasted images arriving at pixel dimensions; they now come in at 0.1 scale
- Fixed SVG text landing far from the workspace; text is now centered on the paste target
- Fixed Grease Pencil layer groups being created for groups that hold a single object
- Split the add-on into one module per area of functionality (parser, plan, importer, GP layers, GP materials, operators, UI)

### v1.1.5

- Fixed complex Illustrator Grease Pencil imports where top-level clipping could hide the whole object
- Fixed non-split Figma Grease Pencil imports so group-level masks/styles are preserved
- Added repeated `Pasted SVG` collection naming using Blender's native `.001` suffix behavior
- Added SVG group-to-Grease Pencil group mapping improvements across layer/group-style exports
- Added grouped curve stacking so objects from the same SVG group can share one stack level

### v1.1.4

- Blender 5.1 support
- Materials now transfer from SVG to Grease Pencil correctly and stay editable after import
- Multilevel masks are now supported

### v1.1.0

- Added `Paste as Curves`
- Added `Split into GP Layers`
- Added `Split layers into objects` with low-level API copy flow
- Added clipboard image paste and SVG image import modes (`Mesh Plane` / `Reference`)
- Added SVG text import to Blender font objects
- Added GP mask/clip reconstruction improvements
- Added cross-editor style handling improvements (Affinity/Figma/Illustrator/Inkscape)
- Added partial SVG gradient-to-GP material mapping
- Added stroke/fill material naming cleanup (`Fill`/`Stroke`)

## License

GPL-3.0-or-later
