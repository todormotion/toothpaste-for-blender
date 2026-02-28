# SVG Support Limitations

ToothPaste for Blender provides basic SVG import functionality. However, due to the complexity of the SVG specification and Blender's Grease Pencil architecture, some features are not supported.

## ✅ Supported Features

### Shape Elements
- `<path>` - SVG path data
- `<rect>` - Rectangles (converted to paths)
- `<circle>` - Circles (converted to paths using arcs)
- `<ellipse>` - Ellipses (converted to paths using arcs)
- `<line>` - Lines (converted to paths)
- `<polyline>` - Polylines (converted to paths, open)
- `<polygon>` - Polygons (converted to paths, closed)

### Layer Structure
- `<g>` elements are treated as layers
- Layer names extracted from `id` or `inkscape:label` attributes
- Multiple layers supported

### SVG Editors
- Adobe Illustrator
- Affinity Designer
- Inkscape
- Any editor producing standard SVG

## ⚠️ Partially Supported / Not Supported

### Transforms
The addon **does not apply SVG transforms**. This includes:
- `transform="translate(x, y)"`
- `transform="rotate(angle)"`
- `transform="scale(x, y)"`
- `transform="skewX(angle)"` / `transform="skewY(angle)"`
- `transform="matrix(...)"`

**Workaround:** Flatten transforms in your SVG editor before copying:
- **Inkscape:** Path → Object to Path
- **Illustrator:** Object → Expand Appearance
- **Affinity Designer:** Layer → Expand Stroke / Convert to Curves

### Styling & Appearance
- **No gradients** (linear or radial)
- **No patterns** (fills or strokes)
- **No opacity/transparency** control
- **No stroke width** preservation
- **No fill/stroke colors** (handled by Blender's material system instead)
- **No filters** (blur, drop shadow, etc.)
- **No blend modes**

**Note:** Grease Pencil materials in Blender can be adjusted after import.

### Advanced Path Features
- **Quadratic Bézier curves** - Converted but may lose precision
- **Arc path commands** - Partially supported
- **Nested paths** - Only top-level paths in groups are imported

### Text
- `<text>` elements are imported as Blender FONT objects
- Nested `<tspan>` style/layout fidelity is limited
- Advanced text layout (textPath, letter spacing, baseline shifts) is not fully supported

### Masks & Clipping
- `<mask>` and `<clipPath>` definitions are converted to GP helper layers when possible
- GP layer masks are assigned to target layers where API support exists
- Holdout behavior for clip paths depends on Blender's current GP layer API
- Complex nested masks/clips may still require flattening in the SVG editor

### Images & External Resources
- `<image>` elements are supported for embedded base64 data and valid local file paths
- Relative asset paths and remote URLs are not resolved
- External CSS stylesheets are not supported

### Symbols & Use
- `<symbol>` definitions are **not imported**
- `<use>` instances are **not supported**

**Workaround:** Expand symbol instances before exporting

### Viewbox & Coordinate Systems
- Custom `viewBox` attributes may cause incorrect scaling
- Coordinate system transformations are not fully supported

## 🔧 Known Issues

### Precision
- Floating-point rounding may cause minor differences in curves
- Very small or very large coordinates may not import correctly

### Blender Grease Pencil v3 (4.3+)
- GP v3 API is relatively new and may have differences across Blender versions
- Layer naming API may vary - fallback behavior is implemented

### Performance
- Very complex SVGs with thousands of paths may be slow to import
- Large file sizes may cause clipboard limitations on some OS

## 💡 Best Practices

### For Best Results:

1. **Simplify before exporting**
   - Merge overlapping paths
   - Remove hidden layers
   - Delete unused elements

2. **Convert advanced features to basic paths**
   - Text → Paths
   - Strokes → Fills
   - Symbols → Expanded instances

3. **Use groups for layers**
   - Group related paths in `<g>` elements
   - Name groups with meaningful layer names

4. **Test with simple SVGs first**
   - Start with basic shapes
   - Verify import works correctly
   - Then try more complex artwork

5. **Keep SVG file size reasonable**
   - Clipboard may have size limits (typically 1-10 MB)
   - Simplify paths to reduce complexity

## 📚 Further Information

### SVG Specification
This addon implements a **subset** of the SVG 1.1 specification focused on path geometry extraction.

### Blender Grease Pencil
The import relies on Blender's built-in `wm.grease_pencil_import_svg` operator, which has its own limitations.

### Contributing
If you need support for additional SVG features, please open an issue on the project repository with example SVG files.

## 🆘 Troubleshooting

### "No valid SVG layers found"
- Ensure your SVG has `<g>` group elements
- Check that groups contain path data or supported shapes
- Try ungrouping and regrouping in your SVG editor

### "Invalid XML format"
- SVG must be valid XML
- Check for unclosed tags or malformed attributes
- Try re-exporting from your SVG editor

### Import creates empty object
- Check that paths have actual geometry (not just transforms)
- Verify paths are visible (not hidden or masked)
- Try converting all objects to paths first

### Layer names are wrong
- Use `id` or `inkscape:label` attributes on `<g>` elements
- Avoid special characters in layer names
- Name groups before exporting SVG
