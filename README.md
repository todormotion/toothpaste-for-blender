# ToothPaste for Blender v1.1.5

Paste SVG content from clipboard into Blender as Grease Pencil, Curves, Text, and Images.

## Current Scope

ToothPaste is focused on Blender 4.3+ (Grease Pencil v3) and supports practical SVG copy/paste workflows from:
- Affinity Designer
- Adobe Illustrator
- Figma
- Inkscape

## Features

- Blender 5.1 support
- Materials now transfer from SVG to Grease Pencil correctly and stay editable after import
- Multilevel masks are now supported
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

- Blender 4.3.0+ including Blender 5.1
- Windows, macOS, or Linux

## Installation

1. Open Blender
2. Go to `Edit > Preferences > Add-ons`
3. Click `Install...`
4. Select the addon ZIP (or repository folder ZIP)
5. Enable `Import-Export: ToothPaste for Blender`

## Usage

1. Copy SVG content from your design app
2. Open `View3D > Sidebar (N) > ToothPaste`
3. Use the grouped tools:
   - `Grease Pencil`
   - `Curves & Text`
   - `Images`
   - `Paste at:`

### Panel Groups

- `Grease Pencil`
  - `Paste as Grease Pencil`
  - `Split into GP Layers`
  - `Split layers into objects`
- `Curves & Text`
  - `Paste as Curves`
- `Images`
  - `Paste Image (Clipboard)`
  - `Mesh Plane` / `Reference`
- `Paste at:`
  - `World Origin` / `Cursor`

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

### v1.1.5 (current line)

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
