"""
Image objects.

Loads SVG <image> payloads (data URIs or local paths) and the system clipboard
bitmap, and creates reference empties or textured mesh planes for them.
"""

import base64
import os
import tempfile
from typing import Dict, List, Optional

import bpy

from .svg_frame import CLIPBOARD_FRAME, SvgFrame


def _decode_data_uri_to_temp_image(data_uri: str) -> Optional[bpy.types.Image]:
    """Decode base64 data URI image and load into Blender image datablock."""
    if not data_uri.startswith("data:image/"):
        return None
    if "," not in data_uri:
        return None

    header, payload = data_uri.split(",", 1)
    is_base64 = ";base64" in header
    if not is_base64:
        return None

    mime = header.split(";")[0][5:].strip().lower()
    extension_map = {
        "image/png": ".png",
        "image/jpeg": ".jpg",
        "image/jpg": ".jpg",
        "image/webp": ".webp",
        "image/bmp": ".bmp",
        "image/tiff": ".tif",
    }
    suffix = extension_map.get(mime, ".img")

    temp_path = None
    try:
        binary = base64.b64decode(payload)
        temp_file = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
        temp_path = temp_file.name
        temp_file.write(binary)
        temp_file.close()
        image = bpy.data.images.load(temp_path, check_existing=False)
        try:
            image.pack()
        except Exception:
            pass
        return image
    except Exception:
        return None
    finally:
        if temp_path and os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except OSError:
                pass


def _load_svg_image(href: str) -> Optional[bpy.types.Image]:
    """Load image from SVG href (base64 or local path)."""
    href = href.strip()
    if href.startswith("data:image/"):
        return _decode_data_uri_to_temp_image(href)
    if os.path.exists(href):
        try:
            return bpy.data.images.load(href, check_existing=True)
        except Exception:
            return None
    return None


def _create_image_empty(
    scene: bpy.types.Scene,
    image: bpy.types.Image,
    item: Dict[str, object],
    frame: SvgFrame = CLIPBOARD_FRAME,
) -> Optional[bpy.types.Object]:
    """Create IMAGE empty object from parsed SVG image item."""
    try:
        obj = bpy.data.objects.new(item["name"], None)
        obj.empty_display_type = "IMAGE"
        obj.data = image
        x, y = frame.point(float(item["x"]), float(item["y"]))
        obj.location = (x, y, frame.depth(bool(item.get("above_art", True))))
        # Anchor the image by its top-left corner, like an SVG <image>; Blender's
        # default would centre it on the object.
        obj.empty_image_offset = (0.0, -1.0)

        # Blender's own Image Empty draws the image at its native aspect from
        # `empty_display_size` alone, so the size is baked in here instead of
        # on the object - a non-uniform object.scale would compound with that
        # built-in aspect handling and squash the shorter axis twice.
        width = max(float(item["width"]), 0.001)
        height = max(float(item["height"]), 0.001)
        obj.empty_display_size = frame.length(max(width, height))
        obj.scale = (1.0, 1.0, 1.0)
        scene.collection.objects.link(obj)
        return obj
    except Exception:
        return None


def _ensure_image_plane_material(image: bpy.types.Image) -> bpy.types.Material:
    """Create or reuse a simple node material for image planes."""
    material_name = f"TP_Image_{image.name}"
    material = bpy.data.materials.get(material_name)
    if material is None:
        material = bpy.data.materials.new(material_name)
    if hasattr(material, "use_nodes"):
        material.use_nodes = True
    if material.node_tree is None:
        return material

    nodes = material.node_tree.nodes
    links = material.node_tree.links
    nodes.clear()

    output_node = nodes.new("ShaderNodeOutputMaterial")
    output_node.location = (300, 0)
    shader_node = nodes.new("ShaderNodeBsdfPrincipled")
    shader_node.location = (0, 0)
    texture_node = nodes.new("ShaderNodeTexImage")
    texture_node.location = (-320, 0)
    texture_node.image = image

    links.new(texture_node.outputs["Color"], shader_node.inputs["Base Color"])
    if "Alpha" in texture_node.outputs and "Alpha" in shader_node.inputs:
        links.new(texture_node.outputs["Alpha"], shader_node.inputs["Alpha"])
    links.new(shader_node.outputs["BSDF"], output_node.inputs["Surface"])

    if hasattr(material, "blend_method"):
        material.blend_method = "BLEND"
    if hasattr(material, "shadow_method"):
        material.shadow_method = "NONE"
    if hasattr(material, "use_backface_culling"):
        material.use_backface_culling = False
    return material


def _create_image_plane(
    scene: bpy.types.Scene,
    image: bpy.types.Image,
    item: Dict[str, object],
    frame: SvgFrame = CLIPBOARD_FRAME,
) -> Optional[bpy.types.Object]:
    """Create a mesh plane with image material."""
    try:
        # The size is baked into the mesh vertices and location here,
        # equivalent to Object > Apply > Scale, so the object itself ends up
        # at (1, 1, 1) instead of carrying an object.scale.
        width = frame.length(max(float(item["width"]), 0.001))
        height = frame.length(max(float(item["height"]), 0.001))
        half_w = width * 0.5
        half_h = height * 0.5

        mesh = bpy.data.meshes.new(f"{item['name']}_Mesh")
        mesh.from_pydata(
            [
                (-half_w, -half_h, 0.0),
                (half_w, -half_h, 0.0),
                (half_w, half_h, 0.0),
                (-half_w, half_h, 0.0),
            ],
            [],
            [(0, 1, 2, 3)],
        )
        mesh.update()

        uv_layer = mesh.uv_layers.new(name="UVMap")
        if uv_layer and len(uv_layer.data) >= 4:
            uv_data = uv_layer.data
            uv_data[0].uv = (0.0, 0.0)
            uv_data[1].uv = (1.0, 0.0)
            uv_data[2].uv = (1.0, 1.0)
            uv_data[3].uv = (0.0, 1.0)

        material = _ensure_image_plane_material(image)
        mesh.materials.append(material)

        obj = bpy.data.objects.new(str(item["name"]), mesh)
        left, top = frame.point(float(item["x"]), float(item["y"]))
        obj.location = (left + half_w, top - half_h, frame.depth(bool(item.get("above_art", True))))
        scene.collection.objects.link(obj)
        return obj
    except Exception:
        return None


def _create_image_object(
    scene: bpy.types.Scene,
    image: bpy.types.Image,
    item: Dict[str, object],
    image_mode: str,
    frame: SvgFrame = CLIPBOARD_FRAME,
) -> Optional[bpy.types.Object]:
    """Create either reference image or mesh plane."""
    if image_mode == "MESH_PLANE":
        return _create_image_plane(scene, image, item, frame)
    return _create_image_empty(scene, image, item, frame)


def create_images_from_svg(
    scene: bpy.types.Scene,
    image_items: List[Dict[str, object]],
    image_mode: str,
    frame: SvgFrame = CLIPBOARD_FRAME,
) -> List[bpy.types.Object]:
    """
    Create Blender objects for SVG <image> items.

    `frame` is where the artwork from the same SVG was imported, which keeps
    images lined up with and in proportion to it.
    """
    created: List[bpy.types.Object] = []
    for item in image_items:
        image = _load_svg_image(str(item["href"]))
        if not image:
            continue
        obj = _create_image_object(scene, image, item, image_mode, frame)
        if obj:
            created.append(obj)
    return created


def _find_image_editor_override(context: bpy.types.Context) -> Optional[Dict[str, object]]:
    """Find IMAGE_EDITOR area for clipboard image paste operator."""
    window = context.window
    if not window or not window.screen:
        return None
    screen = window.screen
    for area in screen.areas:
        if area.type != "IMAGE_EDITOR":
            continue
        region = next((region for region in area.regions if region.type == "WINDOW"), None)
        if not region:
            continue
        return {
            "window": window,
            "screen": screen,
            "area": area,
            "region": region,
            "space_data": area.spaces.active,
        }
    return None


def _build_area_override(context: bpy.types.Context, area: object) -> Optional[Dict[str, object]]:
    """Build context override dictionary for a specific area."""
    window = context.window
    if not window or not window.screen or not area:
        return None
    region = next((region for region in area.regions if region.type == "WINDOW"), None)
    if not region:
        return None
    return {
        "window": window,
        "screen": window.screen,
        "area": area,
        "region": region,
        "space_data": area.spaces.active,
    }


def paste_image_from_clipboard_builtin(context: bpy.types.Context) -> Optional[bpy.types.Image]:
    """Use Blender built-in image clipboard paste in an Image Editor context."""
    if not hasattr(bpy.ops.image, "clipboard_paste"):
        raise RuntimeError("Blender image clipboard paste operator is unavailable.")

    override = _find_image_editor_override(context)
    if override:
        with context.temp_override(**override):
            result = bpy.ops.image.clipboard_paste()
            if "CANCELLED" in result:
                raise RuntimeError("Image clipboard paste failed or clipboard has no image data.")
            return override["space_data"].image

    # Fallback: temporarily turn an existing area into IMAGE_EDITOR.
    screen = context.window.screen if context.window else None
    fallback_area = None
    if screen:
        fallback_area = next(
            (area for area in screen.areas if area.type not in {"TOPBAR", "STATUSBAR"}),
            None,
        )
    if fallback_area is None:
        raise RuntimeError("No editable area available to paste image from clipboard.")

    original_type = fallback_area.type
    try:
        fallback_area.type = "IMAGE_EDITOR"
        temp_override = _build_area_override(context, fallback_area)
        if not temp_override:
            raise RuntimeError("Failed to build temporary Image Editor context.")
        with context.temp_override(**temp_override):
            result = bpy.ops.image.clipboard_paste()
            if "CANCELLED" in result:
                raise RuntimeError("Clipboard has no image data to paste.")
            return temp_override["space_data"].image
    finally:
        try:
            fallback_area.type = original_type
        except Exception:
            pass
