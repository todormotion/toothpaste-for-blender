"""
Clipboard access.

Reads SVG/vector data from the system clipboard, preferring real vector
flavors over bitmap or plain-text ones (needed for Inkscape, Illustrator,
Affinity and Figma).
"""

import base64
import json
import shutil
import subprocess
import sys
from typing import List, Optional, Set

import bpy

from .constants import (
    SVG_CLIPBOARD_TYPE_PRIORITY,
    TEXT_CLIPBOARD_TYPE_PRIORITY,
    XML_CLIPBOARD_TYPE_PRIORITY,
)
from .exceptions import ClipboardError, InvalidSVGError
from .svg_parser import _extract_svg_fragment, validate_svg_string


def _decode_svg_bytes(payload: bytes) -> str:
    """Decode clipboard payload bytes and extract SVG XML when present."""
    if not payload:
        return ""
    for encoding in ("utf-8", "utf-8-sig", "utf-16", "utf-16-le", "utf-16-be", "latin-1"):
        try:
            fragment = _extract_svg_fragment(payload.decode(encoding))
        except Exception:
            continue
        if fragment:
            return fragment
    return ""


def _rank_clipboard_types(type_names: List[str]) -> List[str]:
    """Rank clipboard MIME/types so SVG/vector content wins over bitmap/text."""
    available = [str(name).strip() for name in type_names if str(name).strip()]
    available_set = set(available)
    seen: Set[str] = set()
    ranked: List[str] = []

    def add(type_name: Optional[str]) -> None:
        if not type_name:
            return
        key = str(type_name).strip()
        if not key or key in seen or key not in available_set:
            return
        seen.add(key)
        ranked.append(key)

    for type_name in SVG_CLIPBOARD_TYPE_PRIORITY:
        add(type_name)
    for type_name in XML_CLIPBOARD_TYPE_PRIORITY:
        add(type_name)
    for type_name in TEXT_CLIPBOARD_TYPE_PRIORITY:
        add(type_name)

    svg_like = [name for name in available if "svg" in name.lower()]
    xml_like = [name for name in available if "xml" in name.lower()]
    text_like = [
        name
        for name in available
        if "text" in name.lower() or name in {"UTF8_STRING", "STRING", "TEXT"}
    ]
    other = [name for name in available if name not in svg_like and name not in xml_like and name not in text_like]

    for bucket in (svg_like, xml_like, text_like, other):
        for type_name in bucket:
            add(type_name)
    return ranked


def _read_macos_clipboard_svg() -> str:
    """Read SVG/vector clipboard data from the macOS pasteboard."""
    list_script = """
ObjC.import("AppKit");
var pb = $.NSPasteboard.generalPasteboard;
var types = ObjC.deepUnwrap(pb.types) || [];
types.join("\\n");
"""
    try:
        result = subprocess.run(
            ["osascript", "-l", "JavaScript", "-e", list_script],
            capture_output=True,
            text=True,
            check=False,
        )
    except Exception:
        return ""

    if result.returncode != 0:
        return ""

    type_names = [
        line.strip()
        for line in result.stdout.splitlines()
        if line.strip() and line.strip() != "undefined"
    ]
    for type_name in _rank_clipboard_types(type_names):
        payload_script = f"""
ObjC.import("AppKit");
ObjC.import("Foundation");
var pb = $.NSPasteboard.generalPasteboard;
var typeName = {json.dumps(type_name)};
var nsType = $(typeName);
var text = pb.stringForType(nsType);
if (text) {{
    "TEXT\\n" + ObjC.unwrap(text);
}} else {{
    var data = pb.dataForType(nsType);
    if (data) {{
        "BASE64\\n" + ObjC.unwrap(data.base64EncodedStringWithOptions(0));
    }} else {{
        "";
    }}
}}
"""
        try:
            payload_result = subprocess.run(
                ["osascript", "-l", "JavaScript", "-e", payload_script],
                capture_output=True,
                text=True,
                check=False,
            )
        except Exception:
            continue
        if payload_result.returncode != 0:
            continue

        output = payload_result.stdout or ""
        if output.startswith("TEXT\n"):
            fragment = _extract_svg_fragment(output.split("\n", 1)[1])
            if fragment:
                return fragment
            continue
        if output.startswith("BASE64\n"):
            encoded = output.split("\n", 1)[1].strip()
            if not encoded:
                continue
            try:
                fragment = _decode_svg_bytes(base64.b64decode(encoded))
            except Exception:
                continue
            if fragment:
                return fragment
    return ""


def _read_wayland_clipboard_svg() -> str:
    """Read SVG/vector clipboard data from a Wayland clipboard."""
    if not shutil.which("wl-paste"):
        return ""
    try:
        listed = subprocess.run(
            ["wl-paste", "-l"],
            capture_output=True,
            text=True,
            check=False,
        )
    except Exception:
        return ""

    if listed.returncode != 0:
        return ""

    type_names = [token.strip() for token in listed.stdout.split() if token.strip()]
    for type_name in _rank_clipboard_types(type_names):
        try:
            result = subprocess.run(
                ["wl-paste", "-n", "-t", type_name],
                capture_output=True,
                check=False,
            )
        except Exception:
            continue
        if result.returncode != 0:
            continue
        fragment = _decode_svg_bytes(result.stdout)
        if fragment:
            return fragment
    return ""


def _read_xclip_clipboard_svg() -> str:
    """Read SVG/vector clipboard data from an X11 clipboard."""
    if not shutil.which("xclip"):
        return ""
    try:
        listed = subprocess.run(
            ["xclip", "-selection", "clipboard", "-t", "TARGETS", "-o"],
            capture_output=True,
            text=True,
            check=False,
        )
    except Exception:
        return ""

    if listed.returncode != 0:
        return ""

    type_names = [line.strip() for line in listed.stdout.splitlines() if line.strip()]
    for type_name in _rank_clipboard_types(type_names):
        try:
            result = subprocess.run(
                ["xclip", "-selection", "clipboard", "-t", type_name, "-o"],
                capture_output=True,
                check=False,
            )
        except Exception:
            continue
        if result.returncode != 0:
            continue
        fragment = _decode_svg_bytes(result.stdout)
        if fragment:
            return fragment
    return ""


def _read_platform_clipboard_svg() -> str:
    """Best-effort clipboard reader that prioritizes SVG/vector content."""
    if sys.platform == "darwin":
        fragment = _read_macos_clipboard_svg()
        if fragment:
            return fragment
    elif sys.platform.startswith("linux"):
        fragment = _read_wayland_clipboard_svg()
        if fragment:
            return fragment
        fragment = _read_xclip_clipboard_svg()
        if fragment:
            return fragment
    return _read_platform_clipboard_text()


def _read_platform_clipboard_text() -> str:
    """Best-effort fallback clipboard text reader when Blender clipboard is empty."""
    commands: List[List[str]] = []
    if sys.platform == "darwin":
        commands.append(["pbpaste"])
    elif sys.platform.startswith("win"):
        commands.append(["powershell", "-NoProfile", "-Command", "Get-Clipboard -Raw"])
    else:
        if shutil.which("wl-paste"):
            commands.append(["wl-paste", "-n"])
        if shutil.which("xclip"):
            commands.append(["xclip", "-selection", "clipboard", "-o"])
        if shutil.which("xsel"):
            commands.append(["xsel", "--clipboard", "--output"])

    for command in commands:
        try:
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                check=False,
            )
        except Exception:
            continue
        if result.returncode != 0:
            continue
        fragment = _extract_svg_fragment(result.stdout)
        if fragment:
            return fragment
    return ""


def get_clipboard_svg() -> str:
    """Get SVG from clipboard and validate."""
    try:
        clipboard_text = bpy.context.window_manager.clipboard
    except Exception as exc:
        raise ClipboardError(f"Failed to access clipboard: {exc}") from exc

    svg_fragment = _extract_svg_fragment(clipboard_text)
    if not svg_fragment:
        svg_fragment = _read_platform_clipboard_svg()

    try:
        validate_svg_string(svg_fragment)
    except InvalidSVGError as exc:
        raise ClipboardError(f"Clipboard does not contain valid SVG: {exc}") from exc
    return svg_fragment
