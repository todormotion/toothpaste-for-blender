"""Simple test runner that works with Blender."""
import sys
import os

# Add parent directory and tests directory to path
addon_dir = os.path.dirname(os.path.abspath(__file__))
tests_dir = os.path.join(addon_dir, 'tests')
if addon_dir not in sys.path:
    sys.path.insert(0, addon_dir)
if tests_dir not in sys.path:
    sys.path.insert(0, tests_dir)

# Import bpy to ensure it's available
import bpy

# Register the addon
try:
    # Load the addon module
    import importlib.util
    spec = importlib.util.spec_from_file_location("toothpaste_addon", os.path.join(addon_dir, "__init__.py"))
    toothpaste_addon = importlib.util.module_from_spec(spec)
    sys.modules['toothpaste_addon'] = toothpaste_addon
    spec.loader.exec_module(toothpaste_addon)

    # Register it
    toothpaste_addon.register()
    print("✓ Addon registered successfully")
except Exception as e:
    print(f"✗ Failed to register addon: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)

# Ensure pytest is available
try:
    import pytest
    print(f"✓ pytest {pytest.__version__} found")
except ImportError:
    print("✗ pytest not found - trying to add user site-packages")
    import site
    user_site = site.getusersitepackages()
    if user_site not in sys.path:
        sys.path.insert(0, user_site)
    try:
        import pytest
        print(f"✓ pytest {pytest.__version__} found in user site-packages")
    except ImportError:
        print("✗ ERROR: pytest still not found")
        print("Install with: blender_python -m pip install pytest")
        sys.exit(1)

# Run pytest on the tests directory
print("\n" + "="*70)
print("Running tests...")
print("="*70 + "\n")

exit_code = pytest.main([tests_dir, '-v', '--tb=short'])
print("\n" + "="*70)
print(f"Tests completed with exit code: {exit_code}")
print("="*70)

sys.exit(exit_code)
