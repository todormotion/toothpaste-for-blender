"""
Installed-font lookup.

An SVG never contains the font used by its text: `<text>` only names it through
`font-family`, `font-weight` and `font-style`. To get the right typeface into
Blender the name has to be matched against the fonts installed on this machine,
which is what this module does.

It reads each font file's `name`, `OS/2` and `head` tables directly, so it needs
no third-party packages, and keeps the result in an optional on-disk cache so
that scanning a large font library only happens once. Pure Python: this module
must never import bpy.
"""

import json
import os
import re
import struct
import sys
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

FONT_EXTENSIONS = (".ttf", ".otf", ".ttc", ".otc")
CACHE_VERSION = 3

# name table IDs
_NAME_FAMILY = 1
_NAME_SUBFAMILY = 2
_NAME_FULL = 4
_NAME_POSTSCRIPT = 6
_NAME_TYPO_FAMILY = 16
_NAME_TYPO_SUBFAMILY = 17

_WEIGHT_WORDS = (
    ("extralight", 200),
    ("ultralight", 200),
    ("extrabold", 800),
    ("ultrabold", 800),
    ("semibold", 600),
    ("demibold", 600),
    ("thin", 100),
    ("hairline", 100),
    ("light", 300),
    ("regular", 400),
    ("normal", 400),
    ("book", 400),
    ("roman", 400),
    ("medium", 500),
    ("bold", 700),
    ("heavy", 800),
    ("black", 900),
)

# Platform defaults for the CSS generic families, in order of preference.
_GENERIC_FAMILIES = {
    "sans-serif": ("Helvetica Neue", "Helvetica", "Arial", "Segoe UI", "DejaVu Sans", "Liberation Sans"),
    "serif": ("Times New Roman", "Times", "Georgia", "DejaVu Serif", "Liberation Serif"),
    "monospace": ("Menlo", "Courier New", "Consolas", "DejaVu Sans Mono", "Liberation Mono"),
    "system-ui": ("Helvetica Neue", "Segoe UI", "DejaVu Sans"),
    "cursive": ("Apple Chancery", "Comic Sans MS", "Brush Script MT"),
    "fantasy": ("Impact", "Papyrus"),
}


def normalize_name(name: str) -> str:
    """Lowercase and strip everything but letters and digits for loose comparison."""
    return re.sub(r"[^a-z0-9]", "", str(name).lower())


def font_directories() -> List[str]:
    """Return the font folders that exist on this platform, user fonts included."""
    home = os.path.expanduser("~")
    candidates: List[str] = []
    if sys.platform == "darwin":
        candidates += [
            "/System/Library/Fonts",
            "/System/Library/Fonts/Supplemental",
            "/Library/Fonts",
            os.path.join(home, "Library/Fonts"),
            # Adobe Fonts (Creative Cloud) that are activated for the user
            os.path.join(home, "Library/Application Support/Adobe/CoreSync/plugins/livetype/.r"),
            os.path.join(home, "Library/Application Support/Adobe/CoreSync/plugins/livetype/r"),
            "/Library/Application Support/Adobe/Fonts",
            os.path.join(home, "Library/Application Support/Adobe/User Owned Fonts"),
        ]
    elif sys.platform.startswith("win"):
        windir = os.environ.get("WINDIR", r"C:\Windows")
        local = os.environ.get("LOCALAPPDATA", "")
        roaming = os.environ.get("APPDATA", "")
        candidates += [
            os.path.join(windir, "Fonts"),
            os.path.join(local, r"Microsoft\Windows\Fonts") if local else "",
            os.path.join(roaming, r"Adobe\CoreSync\plugins\livetype\r") if roaming else "",
            os.path.join(roaming, r"Adobe\CoreSync\plugins\livetype\.r") if roaming else "",
        ]
    else:
        candidates += [
            "/usr/share/fonts",
            "/usr/local/share/fonts",
            os.path.join(home, ".fonts"),
            os.path.join(home, ".local/share/fonts"),
        ]
    seen = set()
    result = []
    for path in candidates:
        if path and os.path.isdir(path) and path not in seen:
            seen.add(path)
            result.append(path)
    return result


# --------------------------------------------------------------------------- #
# sfnt parsing
# --------------------------------------------------------------------------- #

def _decode_name(platform: int, encoding: int, raw: bytes) -> str:
    try:
        if platform in (0, 3):
            return raw.decode("utf-16-be")
        if platform == 1 and encoding == 0:
            return raw.decode("mac_roman")
        return raw.decode("latin-1")
    except Exception:
        return ""


def _read_tables(handle, face_offset: int) -> Tuple[bytes, Dict[str, Tuple[int, int, int]]]:
    """Return the sfnt version tag and {tag: (checksum, offset, length)} for one face."""
    handle.seek(face_offset)
    header = handle.read(12)
    if len(header) < 12:
        raise ValueError("truncated sfnt header")
    version = header[:4]
    if version not in (b"\x00\x01\x00\x00", b"OTTO", b"true", b"typ1"):
        raise ValueError("not an sfnt font")
    num_tables = struct.unpack(">H", header[4:6])[0]
    records = handle.read(16 * num_tables)
    tables: Dict[str, Tuple[int, int, int]] = {}
    for i in range(num_tables):
        tag, checksum, offset, length = struct.unpack(">4sIII", records[i * 16:(i + 1) * 16])
        tables[tag.decode("latin-1")] = (checksum, offset, length)
    return version, tables


def _read_table(handle, tables, tag: str, limit: int = 1 << 20) -> bytes:
    entry = tables.get(tag)
    if not entry:
        return b""
    _, offset, length = entry
    handle.seek(offset)
    return handle.read(min(length, limit))


def _parse_name_table(data: bytes) -> Dict[int, str]:
    """Return {nameID: best English string} from a `name` table."""
    if len(data) < 6:
        return {}
    _fmt, count, string_offset = struct.unpack(">HHH", data[:6])
    best: Dict[int, Tuple[int, str]] = {}
    for i in range(count):
        base = 6 + i * 12
        if base + 12 > len(data):
            break
        platform, encoding, language, name_id, length, offset = struct.unpack(">HHHHHH", data[base:base + 12])
        if name_id not in (
            _NAME_FAMILY, _NAME_SUBFAMILY, _NAME_FULL, _NAME_POSTSCRIPT, _NAME_TYPO_FAMILY, _NAME_TYPO_SUBFAMILY
        ):
            continue
        raw = data[string_offset + offset:string_offset + offset + length]
        text = _decode_name(platform, encoding, raw).strip()
        if not text:
            continue
        # Prefer English Windows names, then Mac Roman, then anything else.
        rank = 0 if (platform == 3 and language == 0x409) else 1 if (platform == 1 and language == 0) else 2
        if name_id not in best or rank < best[name_id][0]:
            best[name_id] = (rank, text)
    return {name_id: value[1] for name_id, value in best.items()}


def _parse_face(handle, face_offset: int, face_index: int, path: str) -> Optional[dict]:
    try:
        version, tables = _read_tables(handle, face_offset)
        names = _parse_name_table(_read_table(handle, tables, "name"))
        os2 = _read_table(handle, tables, "OS/2")
        head = _read_table(handle, tables, "head")
    except Exception:
        return None

    # Blender sizes text by the font's overall bounding box rather than by its em
    # square: `size` 1 is 1 / bbox_h em, where bbox_h is the head-table box height
    # in em units. Knowing it lets text be sized to a real point size.
    bbox_h = 0.0
    if len(head) >= 44:
        units_per_em = struct.unpack(">H", head[18:20])[0]
        y_min = struct.unpack(">h", head[38:40])[0]
        y_max = struct.unpack(">h", head[42:44])[0]
        if units_per_em and y_max > y_min:
            bbox_h = (y_max - y_min) / units_per_em

    weight = 400
    width = 5
    italic = False
    if len(os2) >= 64:
        weight = struct.unpack(">H", os2[4:6])[0] or 400
        width = struct.unpack(">H", os2[6:8])[0] or 5
        selection = struct.unpack(">H", os2[62:64])[0]
        italic = bool(selection & 0x01) or bool(selection & 0x200)
    elif len(head) >= 46:
        mac_style = struct.unpack(">H", head[44:46])[0]
        weight = 700 if mac_style & 1 else 400
        italic = bool(mac_style & 2)
    if len(head) >= 46 and not italic:
        italic = bool(struct.unpack(">H", head[44:46])[0] & 2)

    postscript = names.get(_NAME_POSTSCRIPT, "")
    full = names.get(_NAME_FULL, "")
    family = names.get(_NAME_TYPO_FAMILY) or names.get(_NAME_FAMILY, "")
    legacy_family = names.get(_NAME_FAMILY, "")
    style = names.get(_NAME_TYPO_SUBFAMILY) or names.get(_NAME_SUBFAMILY, "")
    if not (postscript or full or family):
        return None

    return {
        "path": path,
        "index": face_index,
        "ttc": version == b"ttcf",
        "otf": version == b"OTTO",
        "family": family,
        "legacy_family": legacy_family,
        "style": style,
        "full": full,
        "ps": postscript,
        "weight": int(weight),
        "width": int(width),
        "italic": italic,
        "bbox_h": round(bbox_h, 5),
    }


def read_font_file(path: str) -> List[dict]:
    """Return one info dict per face in a font file (several for .ttc/.otc)."""
    faces: List[dict] = []
    try:
        with open(path, "rb") as handle:
            tag = handle.read(4)
            if tag == b"ttcf":
                handle.seek(8)
                count = struct.unpack(">I", handle.read(4))[0]
                offsets = [struct.unpack(">I", handle.read(4))[0] for _ in range(min(count, 64))]
                for index, offset in enumerate(offsets):
                    face = _parse_face(handle, offset, index, path)
                    if face:
                        face["ttc"] = True
                        faces.append(face)
            else:
                face = _parse_face(handle, 0, 0, path)
                if face:
                    faces.append(face)
    except OSError:
        return []
    return faces


def extract_face(face: dict, destination_dir: str) -> Optional[str]:
    """
    Write one face of a font collection out as a standalone font file.

    Blender can only load the first face of a .ttc, so any other face has to be
    split out before it can be used. Returns the path of the standalone file.
    """
    source = face["path"]
    index = int(face.get("index", 0))
    if not face.get("ttc"):
        return source
    if index == 0:
        return source

    os.makedirs(destination_dir, exist_ok=True)
    extension = ".otf" if face.get("otf") else ".ttf"
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", face.get("ps") or face.get("full") or f"face{index}")
    destination = os.path.join(destination_dir, f"{safe}{extension}")
    if os.path.exists(destination) and os.path.getsize(destination) > 0:
        return destination

    try:
        with open(source, "rb") as handle:
            handle.seek(12 + 4 * index)
            face_offset = struct.unpack(">I", handle.read(4))[0]
            version, tables = _read_tables(handle, face_offset)
            ordered = sorted(tables.items())
            payload = []
            for tag, (checksum, offset, length) in ordered:
                handle.seek(offset)
                payload.append((tag, checksum, handle.read(length)))
    except Exception:
        return None

    count = len(payload)
    entry_selector = max(count.bit_length() - 1, 0)
    search_range = (1 << entry_selector) * 16
    range_shift = count * 16 - search_range
    header = version + struct.pack(">HHHH", count, search_range, entry_selector, range_shift)

    directory = b""
    body = b""
    cursor = 12 + 16 * count
    for tag, checksum, data in payload:
        padded = data + b"\x00" * (-len(data) % 4)
        directory += struct.pack(">4sIII", tag.encode("latin-1"), checksum, cursor + len(body), len(data))
        body += padded

    try:
        with open(destination, "wb") as handle:
            handle.write(header + directory + body)
    except OSError:
        return None
    return destination


# --------------------------------------------------------------------------- #
# index + matching
# --------------------------------------------------------------------------- #

def weight_from_words(text: str) -> Optional[int]:
    """Read a weight out of words such as 'Semibold' or 'Bold Italic'."""
    lowered = re.sub(r"[^a-z]", "", str(text).lower())
    for word, weight in _WEIGHT_WORDS:
        if word in lowered:
            return weight
    return None


def italic_from_words(text: str) -> bool:
    lowered = str(text).lower()
    return "italic" in lowered or "oblique" in lowered or "slanted" in lowered


class FontIndex:
    """Searchable collection of installed font faces."""

    def __init__(self, faces: Iterable[dict]):
        self.faces: List[dict] = list(faces)
        self.by_postscript: Dict[str, dict] = {}
        self.by_full: Dict[str, dict] = {}
        self.by_family: Dict[str, List[dict]] = {}
        for face in self.faces:
            key = normalize_name(face.get("ps", ""))
            if key:
                self.by_postscript.setdefault(key, face)
            key = normalize_name(face.get("full", ""))
            if key:
                self.by_full.setdefault(key, face)
            for family in {face.get("family", ""), face.get("legacy_family", "")}:
                key = normalize_name(family)
                if key:
                    self.by_family.setdefault(key, []).append(face)

    def __len__(self) -> int:
        return len(self.faces)

    @staticmethod
    def _score(face: dict, weight: int, italic: bool) -> float:
        score = abs(int(face.get("weight", 400)) - weight)
        if bool(face.get("italic")) != bool(italic):
            score += 1000
        # Prefer normal-width cuts unless the family only has condensed/expanded ones.
        score += 40 * abs(int(face.get("width", 5)) - 5)
        return score

    def best_in_family(self, family_key: str, weight: int, italic: bool) -> Optional[dict]:
        candidates = self.by_family.get(family_key)
        if not candidates:
            return None
        return min(candidates, key=lambda face: self._score(face, weight, italic))

    def _match_one(self, name: str, weight: int, italic: bool) -> Optional[dict]:
        key = normalize_name(name)
        if not key:
            return None

        # 1. a family name, styled by the requested weight and slant. This has to
        #    come first: "Arial" and "Helvetica" are also the full/PostScript name
        #    of their regular cut, so an exact-name match would ignore bold.
        face = self.best_in_family(key, weight, italic)
        if face:
            return face

        # 2. exact PostScript or full name: the file names one face explicitly,
        #    for example 'Arial-BoldMT' or 'Helvetica Neue Bold'
        for table in (self.by_postscript, self.by_full):
            if key in table:
                return table[key]

        # 3. "Family-Style" or "Family Style" forms that are not an exact name,
        #    for example 'MyriadPro-BoldIt' or 'Open Sans Bold'
        parts = re.split(r"[-_,]", str(name).strip(), maxsplit=1)
        if len(parts) == 2:
            family_key = normalize_name(parts[0])
            if family_key in self.by_family:
                style_weight = weight_from_words(parts[1])
                style_italic = italic or italic_from_words(parts[1])
                return self.best_in_family(
                    family_key,
                    style_weight if style_weight is not None else weight,
                    style_italic,
                )
        words = str(name).strip().split()
        for cut in range(len(words) - 1, 0, -1):
            family_key = normalize_name(" ".join(words[:cut]))
            if family_key in self.by_family:
                tail = " ".join(words[cut:])
                style_weight = weight_from_words(tail)
                style_italic = italic or italic_from_words(tail)
                return self.best_in_family(
                    family_key,
                    style_weight if style_weight is not None else weight,
                    style_italic,
                )
        return None

    def resolve(self, families: Sequence[str], weight: int = 400, italic: bool = False) -> Optional[dict]:
        """Return the first installed face matching one of `families`, in order."""
        for name in families:
            cleaned = str(name).strip().strip("'\"")
            if not cleaned:
                continue
            generic = _GENERIC_FAMILIES.get(cleaned.lower())
            if generic:
                for option in generic:
                    face = self._match_one(option, weight, italic)
                    if face:
                        return face
                continue
            face = self._match_one(cleaned, weight, italic)
            if face:
                return face
        return None

    def variants(self, face: dict) -> Dict[str, Optional[dict]]:
        """Regular/bold/italic/bold-italic faces of the same family as `face`."""
        key = normalize_name(face.get("family", "")) or normalize_name(face.get("legacy_family", ""))
        result: Dict[str, Optional[dict]] = {}
        for slot, weight, italic in (
            ("regular", 400, False),
            ("bold", 700, False),
            ("italic", 400, True),
            ("bold_italic", 700, True),
        ):
            result[slot] = self.best_in_family(key, weight, italic) if key else None
        return result


# --------------------------------------------------------------------------- #
# scanning + cache
# --------------------------------------------------------------------------- #

def _iter_font_files(directories: Sequence[str]) -> Iterable[str]:
    for directory in directories:
        for root, _dirs, files in os.walk(directory):
            for name in files:
                if name.lower().endswith(FONT_EXTENSIONS):
                    yield os.path.join(root, name)


def _load_cache(cache_path: Optional[str]) -> Dict[str, dict]:
    if not cache_path or not os.path.exists(cache_path):
        return {}
    try:
        with open(cache_path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return {}
    if data.get("version") != CACHE_VERSION:
        return {}
    return data.get("files", {})


def _save_cache(cache_path: Optional[str], files: Dict[str, dict]) -> None:
    if not cache_path:
        return
    try:
        os.makedirs(os.path.dirname(cache_path), exist_ok=True)
        with open(cache_path, "w", encoding="utf-8") as handle:
            json.dump({"version": CACHE_VERSION, "files": files}, handle)
    except OSError:
        pass


def build_index(
    directories: Optional[Sequence[str]] = None,
    cache_path: Optional[str] = None,
) -> FontIndex:
    """Scan font folders (re-reading only files that changed) and return an index."""
    directories = list(directories) if directories is not None else font_directories()
    cached = _load_cache(cache_path)
    current: Dict[str, dict] = {}
    faces: List[dict] = []
    changed = False

    for path in _iter_font_files(directories):
        try:
            stat = os.stat(path)
        except OSError:
            continue
        signature = [int(stat.st_mtime), int(stat.st_size)]
        entry = cached.get(path)
        if entry and entry.get("sig") == signature:
            file_faces = entry.get("faces", [])
        else:
            file_faces = read_font_file(path)
            changed = True
        current[path] = {"sig": signature, "faces": file_faces}
        faces.extend(file_faces)

    if changed or set(current) != set(cached):
        _save_cache(cache_path, current)
    return FontIndex(faces)
