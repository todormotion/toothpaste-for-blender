# SVG Support Limitations

ToothPaste for Blender implements a practical subset of SVG import behavior for Blender 5.0+ including Blender 5.2 LTS (Grease Pencil v3).

## Supported

- Geometry: `<path>`, `<rect>`, `<circle>`, `<ellipse>`, `<line>`, `<polyline>`, `<polygon>`
- Top-level group/layer workflows via `<g>`
- CSS class style inlining from `<style>` blocks (common Illustrator export style)
- Fill/stroke color mapping to GP materials
- Imported GP strokes remain material-driven after import, so later material edits continue to affect the viewport
- Stroke width mapping (best effort)
- Mask/clip reconstruction using GP layer masks and holdout (common cases)
- Nested mask/clip chains resolve to the nearest parent mask layer for each imported GP layer
- Text extraction from `<text>` into Blender FONT objects
- SVG `<image>` support for base64 data and valid local paths
- Images embedded only inside `defs`, `mask`, `clipPath`, `symbol`, or `pattern` containers are ignored
- Clipboard image paste via Blender built-in functionality

## Partial Support

### Gradients

Gradient support is partial and best-effort:
- Linear/radial gradients are detected
- First/last stop colors are mapped to GP fill gradient colors
- Location/rotation/scale are approximated to GP material gradient controls

Current limitation:
- Gradient transforms are not always visually identical to source editors
- Manual tweaking in Blender may still be required

### Masks and Clipping

- Multi-layer mask/clip reconstruction works in common exports
- Editor-specific edge cases can still fail when hierarchy is deeply nested or transformed

### Text

- Basic text content and position are supported
- Advanced text layout (`tspan` nesting behavior, textPath, tracking, baseline shifts) is limited

## Not Fully Supported

- SVG filters (blur, drop shadow, etc.)
- Pattern fills and complex paint servers beyond current gradient mapping
- Blend modes with full SVG compositing parity
- External CSS stylesheet loading
- Symbol/use workflows that depend on unresolved external defs

## Coordinate / Transform Caveats

- Transform-heavy SVGs may still import with differences
- Complex viewBox/unit setups can produce scale/position mismatches

## File Import and Drag & Drop

- Only `.svg` files are handled; PDF and AI files are not supported
- Imported artwork is copied into the scene, not linked to the source file, so
  later edits to the `.svg` are not picked up
- Dropping an SVG shows Blender's drop menu whenever more than one importer
  claims the extension; `SVG (ToothPaste)` is the ToothPaste entry
- Grease Pencil layer groups are not created for groups holding a single object

## Performance Caveats

- Very large SVGs (many thousands of paths) can import slowly
- Clipboard size limits may affect large documents

## Recommended Prep

1. Flatten transforms where possible
2. Expand symbols/instances before export
3. Simplify deeply nested clips/masks
4. Keep groups organized and named
5. Test editor exports with small representative files first
