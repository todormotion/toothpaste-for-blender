# ToothPaste for Blender v1.0.0

**Paste SVG from clipboard directly into Blender as Grease Pencil objects**

ToothPaste is a Blender addon that allows you to quickly import SVG graphics from your clipboard into Blender's Grease Pencil system. Perfect for motion graphics, 2D animation, and integrating vector artwork into your 3D projects.

## Features

✅ **Paste SVG directly from clipboard** - No need to save files
✅ **Grease Pencil v3 support** - Works with Blender 4.2+
✅ **Shape conversion** - Rectangles, circles, ellipses, lines, polygons, and polylines automatically converted to paths
✅ **Layer preservation** - SVG groups (`<g>`) imported as separate Grease Pencil layers
✅ **Layer splitting** - Split Grease Pencil layers into individual objects with one click
✅ **Multi-editor support** - Works with SVGs from Adobe Illustrator, Affinity Designer, Inkscape, and more

## Requirements

- **Blender 4.2.0 or later** (Grease Pencil v3 required)
- Supported operating systems: Windows, macOS, Linux

## Installation

### Method 1: Install from ZIP (Recommended)

1. Download the latest release ZIP file
2. Open Blender
3. Go to **Edit > Preferences > Add-ons**
4. Click **Install...** button (top right)
5. Select the downloaded ZIP file
6. Enable the addon by checking the box next to "Import-Export: ToothPaste for Blender"

### Method 2: Manual Installation

1. Download or clone this repository
2. Copy the entire folder to your Blender addons directory:
   - **Windows:** `%APPDATA%\Blender Foundation\Blender\[version]\scripts\addons\`
   - **macOS:** `~/Library/Application Support/Blender/[version]/scripts/addons/`
   - **Linux:** `~/.config/blender/[version]/scripts/addons/`
3. Restart Blender
4. Enable the addon in Preferences > Add-ons

## Usage

### Importing SVG from Clipboard

1. Copy SVG code to your clipboard:
   - In your SVG editor: Select artwork → Copy (or Edit → Copy)
   - From a file: Open in text editor → Select all → Copy

2. In Blender's 3D Viewport, open the **Sidebar** (press `N` if hidden)

3. Navigate to the **ToothPaste** tab

4. Click **Paste SVG (Single Object)**

5. Your SVG will be imported as a Grease Pencil object at the 3D cursor location

### Splitting Layers into Objects

If your imported Grease Pencil object has multiple layers, you can split them:

1. Select the Grease Pencil object

2. In the **ToothPaste** sidebar panel, click **Split layers into objects**

3. Each layer will become a separate object (original is hidden, not deleted)

### Keyboard Shortcuts

You can assign custom shortcuts via Edit > Preferences > Keymap:
- Search for `toothpaste.paste_svg`
- Search for `gpencil.split_layers_into_objects`

## Supported SVG Features

### ✅ Fully Supported
- Basic shapes: `<rect>`, `<circle>`, `<ellipse>`, `<line>`, `<polygon>`, `<polyline>`
- Path data: `<path>` elements with standard SVG path commands
- Layer groups: `<g>` elements (become GP layers)
- Layer naming: `id` and `inkscape:label` attributes

### ⚠️ Limitations
- **No transform support** (rotation, scale, translation must be baked)
- **No text elements** (convert to paths first)
- **No gradients or patterns** (solid colors only)
- **No masks or clipping paths**
- **No embedded images**

👉 **See [LIMITATIONS.md](LIMITATIONS.md) for full details and workarounds**

## Workflow Tips

### Preparing SVGs for Import

For best results, prepare your SVG before copying:

**In Inkscape:**
- Convert text to paths: `Path → Object to Path`
- Flatten transforms: `Path → Object to Path` (select all)
- Organize in groups: Use layers panel

**In Adobe Illustrator:**
- Convert text: `Type → Create Outlines`
- Expand appearances: `Object → Expand Appearance`
- Use groups for layers: `Object → Group`

**In Affinity Designer:**
- Convert to curves: `Layer → Convert to Curves`
- Expand strokes: `Layer → Expand Stroke`
- Use groups for organization

### Common Use Cases

**Motion Graphics:**
1. Design logo/graphics in vector editor
2. Copy SVG to clipboard
3. Paste into Blender as Grease Pencil
4. Animate with GP modifiers and keyframes

**2D Animation:**
1. Import character parts as separate layers
2. Split layers into objects
3. Rig with armatures
4. Animate

**Mixed 2D/3D:**
1. Import vector elements
2. Combine with 3D scenes
3. Use GP materials for stylization

## Troubleshooting

### "No valid SVG layers found"
- Ensure your SVG contains `<g>` group elements
- Try grouping your artwork in the SVG editor
- Check that groups contain actual geometry

### "Invalid XML format" / "Clipboard does not contain valid SVG"
- Make sure you copied the SVG code (not just selected artwork)
- In some editors, use "Copy SVG Code" instead of regular copy
- Try exporting as SVG file, then open in text editor and copy

### Imported object is empty
- Convert all objects to paths in your SVG editor
- Check that objects aren't hidden or masked
- Verify paths have fills or strokes (not just transforms)

### Layer names are generic ("Layer_1", etc.)
- Add `id` attributes to your `<g>` elements
- In Inkscape: Use Object Properties panel
- In Illustrator/Affinity: Name layers in the Layers panel

### Shapes look wrong or distorted
- Flatten transforms before exporting (`Object → Expand` / `Path → Object to Path`)
- Check that viewBox and dimensions are standard values
- Try simplifying complex path data

## Development

### Running Tests

Tests require pytest installed in Blender's Python environment.

```bash
# Install pytest in Blender's Python
/path/to/blender/python -m pip install pytest

# Run all tests
blender --background --python tests/run_tests.py -- -v

# Run specific test file
blender --background --python tests/run_tests.py -- tests/test_svg_validation.py -v
```

See [tests/README.md](tests/README.md) for more details.

### Project Structure

```
toothpaste-for-blender/
├── __init__.py           # Main addon code
├── exceptions.py         # Custom exception classes
├── blender_manifest.toml # Extension manifest
├── README.md            # This file
├── LIMITATIONS.md       # Detailed feature support documentation
├── tests/               # Test suite
│   ├── conftest.py      # Pytest fixtures
│   ├── run_tests.py     # Test runner
│   └── test_*.py        # Test modules
└── icon.jpg             # Addon icon
```

## Contributing

Contributions are welcome! Please:

1. **Report bugs** with example SVG files
2. **Request features** with use cases
3. **Submit pull requests** with tests

## License

GNU General Public License v3.0 or later (GPL-3.0-or-later)

See Blender's extension licensing requirements: https://docs.blender.org/manual/en/dev/advanced/extensions/licenses.html

## Credits

**Author:** Todor Hlebarov (Cosmonavt)
**Website:** https://gitlab.com/todor.motion/toothpaste-for-blender
**Version:** 1.0.0
**Blender Versions:** 4.2.0+

## Changelog

### v1.0.0 (2025)
- Complete refactor with TDD methodology
- Improved error handling with specific exception types
- Fixed Grease Pencil v3 compatibility issues
- Added proper resource cleanup (temp file handling)
- Removed unused code (masks, clips parsing)
- Comprehensive docstrings and documentation
- Full test suite with pytest
- Created LIMITATIONS.md with detailed feature documentation

### v0.9.2 (Previous)
- Initial Blender 4.4 compatibility
- Basic SVG import functionality
- Layer splitting feature

## Support

- **Documentation:** See LIMITATIONS.md for feature details
- **Community:** Blender Artists forum

---

Made with ❤️ for the Blender community
