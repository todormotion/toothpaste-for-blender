"""
Import plan builder.

Converts parsed SVG metadata into the concrete documents handed to Blender's
importers, plus the mappings (masks, clips, materials, groups) applied
afterwards. Pure Python: this module must never import bpy.
"""

import xml.etree.ElementTree as ET
from typing import Dict, List, Optional, Set, Tuple

from .constants import (
    CLIP_LAYER_PREFIX,
    MASK_LAYER_PREFIX,
    RESOURCE_TAGS,
    SUPPORTED_GEOMETRY,
    SVG_NS,
)
from .svg_geometry import (
    _bounds_match_score,
    _collect_geometry_signatures,
    _definition_geometry_bounds,
    _geometry_bounds,
    _geometry_signature,
    _geometry_to_import_element,
    _transform_bounds,
)
from .svg_parser import _collect_svg_metadata, _group_name_from_node
from .svg_utils import (
    _clone_xml_element,
    _extract_mask_clip_refs,
    _extract_url_ref,
    _is_identity_matrix,
    _local_name,
    _matrix_identity,
    _matrix_multiply,
    _matrix_to_svg,
    _parse_style,
    _parse_transform_attr,
    _presentation_style,
    _resolve_fill_spec,
    _resolve_stroke_spec,
    _style_to_string,
)


def _build_svg_document(source_root: ET.Element, group_elements: List[ET.Element]) -> str:
    """Build SVG string with specific top-level groups and source root attributes."""
    root_copy = ET.Element(source_root.tag, dict(source_root.attrib))
    for child in list(source_root):
        if not isinstance(child.tag, str):
            continue
        if _local_name(child.tag) in RESOURCE_TAGS:
            root_copy.append(_clone_xml_element(child))
    for group in group_elements:
        root_copy.append(_clone_xml_element(group))
    return ET.tostring(root_copy, encoding="unicode")


def _strip_mask_clip_references(
    node: ET.Element,
    keep_mask_refs: Optional[Set[str]] = None,
    keep_clip_refs: Optional[Set[str]] = None,
) -> None:
    """Remove only SVG mask/clip refs that ToothPaste is rebuilding in GP."""
    if not isinstance(node.tag, str):
        return

    keep_mask_refs = keep_mask_refs or set()
    keep_clip_refs = keep_clip_refs or set()

    mask_attr = _extract_url_ref(node.get("mask"))
    if mask_attr and mask_attr not in keep_mask_refs and "mask" in node.attrib:
        del node.attrib["mask"]

    clip_attr = _extract_url_ref(node.get("clip-path"))
    if clip_attr and clip_attr not in keep_clip_refs and "clip-path" in node.attrib:
        del node.attrib["clip-path"]

    style = _parse_style(node.get("style"))
    if style:
        style_mask = _extract_url_ref(style.get("mask"))
        if style_mask and style_mask not in keep_mask_refs:
            style.pop("mask", None)
        style_clip = _extract_url_ref(style.get("clip-path"))
        if style_clip and style_clip not in keep_clip_refs:
            style.pop("clip-path", None)
        if style:
            node.set("style", _style_to_string(style))
        elif "style" in node.attrib:
            del node.attrib["style"]

    for child in list(node):
        _strip_mask_clip_references(child, keep_mask_refs, keep_clip_refs)


def _flatten_layers_to_group(layers: List[Dict[str, object]], name: str = "Layer_1") -> Optional[ET.Element]:
    """Flatten all layer groups into one group for non-split imports."""
    if not layers:
        return None
    flattened = ET.Element(f"{{{SVG_NS}}}g", {"id": name})
    for layer in layers:
        group = layer["element"]
        flattened.append(_clone_xml_element(group))
    return flattened if len(flattened) else None


def _build_definition_groups(definitions: Dict[str, ET.Element], prefix: str) -> List[ET.Element]:
    """Convert mask/clip definitions to importable groups."""
    groups: List[ET.Element] = []
    for definition_id, definition_node in definitions.items():
        group = ET.Element(f"{{{SVG_NS}}}g", {"id": f"{prefix}{definition_id}"})
        for child in list(definition_node):
            group.append(_clone_xml_element(child))
        if len(group):
            groups.append(group)
    return groups


def _make_unique_names(names: List[str]) -> List[str]:
    """Ensure layer/object names are unique while preserving order."""
    counts: Dict[str, int] = {}
    unique: List[str] = []
    for name in names:
        base = name.strip() or "Layer"
        idx = counts.get(base, 0)
        counts[base] = idx + 1
        unique.append(base if idx == 0 else f"{base}_{idx + 1}")
    return unique


def _collect_split_geometry_groups(layer: Dict[str, object]) -> List[Dict[str, object]]:
    """
    Build one temporary SVG group per geometry object.

    This enables one GP layer per SVG object when split mode is enabled, which
    is required for per-object mask/clip assignment.
    """
    layer_group = layer["element"]
    layer_name = str(layer["name"])
    split_items: List[Dict[str, object]] = []
    item_counter = 0
    anonymous_group_counter = 0

    def walk(
        node: ET.Element,
        inherited_masks: Set[str],
        inherited_clips: Set[str],
        inherited_style: Dict[str, str],
        inherited_transform: Tuple[float, float, float, float, float, float],
        inherited_group_path: Tuple[str, ...],
        is_root: bool,
        definition_context: bool = False,
    ) -> None:
        nonlocal item_counter, anonymous_group_counter

        node_masks, node_clips = _extract_mask_clip_refs(node)
        # GP layer masks should form a chain, so each geometry item only keeps the
        # nearest active mask/clip container instead of accumulating all ancestors.
        active_masks = set(node_masks) if node_masks else set(inherited_masks)
        active_clips = set(node_clips) if node_clips else set(inherited_clips)
        active_style = dict(inherited_style)
        active_style.update(_presentation_style(node))
        active_transform = _matrix_multiply(inherited_transform, _parse_transform_attr(node.get("transform")))

        tag = _local_name(node.tag)
        is_definition_container = definition_context or tag in {"clipPath", "mask", "defs", "symbol"}
        active_group_path = inherited_group_path
        if tag == "g" and not is_definition_container and not is_root:
            group_name = _group_name_from_node(node)
            if not group_name:
                anonymous_group_counter += 1
                group_name = f"Group_{anonymous_group_counter}"
            if group_name:
                active_group_path = inherited_group_path + (group_name,)

        if tag in SUPPORTED_GEOMETRY and not is_definition_container:
            geometry = _geometry_to_import_element(node)
            if geometry is not None:
                for style_key, style_value in active_style.items():
                    if style_value:
                        geometry.set(style_key, style_value)
                if "class" in geometry.attrib:
                    del geometry.attrib["class"]
                item_counter += 1
                raw_name = node.get("id") or f"{layer_name}_{item_counter}"
                item_group = ET.Element(f"{{{SVG_NS}}}g", {"id": raw_name})
                if not _is_identity_matrix(active_transform):
                    item_group.set("transform", _matrix_to_svg(active_transform))
                item_group.append(geometry)
                bounds = _geometry_bounds(node)
                world_bounds = _transform_bounds(bounds, active_transform) if bounds is not None else None
                split_items.append(
                    {
                        "name": raw_name,
                        "element": item_group,
                        "signature": _geometry_signature(node, active_transform),
                        "signature_raw": _geometry_signature(node),
                        "fill_spec": _resolve_fill_spec(active_style),
                        "stroke_spec": _resolve_stroke_spec(active_style),
                        "bounds": world_bounds,
                        "transform": active_transform,
                        "mask_refs": sorted(active_masks),
                        "clip_refs": sorted(active_clips),
                        "group_path": list(active_group_path) if active_group_path else [layer_name],
                    }
                )

        for child in list(node):
            if isinstance(child.tag, str):
                walk(
                    child,
                    active_masks,
                    active_clips,
                    active_style,
                    active_transform,
                    active_group_path,
                    False,
                    is_definition_container,
                )

    walk(layer_group, set(), set(), {}, _matrix_identity(), (layer_name,), True, False)
    return split_items


def _collapse_single_object_groups(
    layer_group_paths: Dict[str, List[str]],
) -> Dict[str, List[str]]:
    """
    Drop group levels that would end up holding a single object.

    SVG editors wrap lone shapes in groups constantly. Recreating those as GP
    layer groups buries every single-object layer one level deeper for no gain,
    so any trailing level that only ever contains one layer is removed. A group
    shared by two or more layers is kept.
    """
    occupancy: Dict[Tuple[str, ...], int] = {}
    for path in layer_group_paths.values():
        for depth in range(1, len(path) + 1):
            prefix = tuple(path[:depth])
            occupancy[prefix] = occupancy.get(prefix, 0) + 1

    collapsed: Dict[str, List[str]] = {}
    for layer_name, path in layer_group_paths.items():
        trimmed = list(path)
        while trimmed and occupancy.get(tuple(trimmed), 0) <= 1:
            trimmed.pop()
        collapsed[layer_name] = trimmed
    return collapsed


def _prepare_import_plan(root: ET.Element, split_layers: bool) -> Dict[str, object]:
    """Prepare import variants and mappings for operators."""
    metadata = _collect_svg_metadata(root)
    layers = [layer for layer in metadata["layers"] if layer["has_geometry"]]
    mask_defs = metadata["mask_defs"]
    clip_defs = metadata["clip_defs"]
    gradient_defs = metadata.get("gradient_defs", {})

    visible_groups: List[ET.Element] = []
    visible_names: List[str] = []
    mask_bindings: Dict[str, List[str]] = {}
    clip_bindings: Dict[str, List[str]] = {}
    mask_layer_bindings: Dict[str, List[str]] = {}
    clip_layer_bindings: Dict[str, List[str]] = {}
    layer_fill_specs: Dict[str, Dict[str, object]] = {}
    layer_stroke_specs: Dict[str, Dict[str, object]] = {}
    layer_bounds: Dict[str, Optional[Tuple[float, float, float, float]]] = {}
    layer_transforms: Dict[str, Tuple[float, float, float, float, float, float]] = {}
    layer_group_paths: Dict[str, List[str]] = {}
    split_curve_groups: List[Dict[str, object]] = []
    signature_to_layers: Dict[str, List[str]] = {}
    signature_to_layers_raw: Dict[str, List[str]] = {}

    if split_layers:
        object_items: List[Dict[str, object]] = []
        for layer in layers:
            object_items.extend(_collect_split_geometry_groups(layer))

        if object_items:
            unique_object_names = _make_unique_names([str(item["name"]) for item in object_items])
            for item, unique_name in zip(object_items, unique_object_names):
                group = _clone_xml_element(item["element"])
                group.set("id", unique_name)
                visible_groups.append(group)
                visible_names.append(unique_name)
                mask_bindings[unique_name] = list(item["mask_refs"])
                clip_bindings[unique_name] = list(item["clip_refs"])
                layer_fill_specs[unique_name] = dict(item.get("fill_spec") or {})
                layer_stroke_specs[unique_name] = dict(item.get("stroke_spec") or {})
                layer_bounds[unique_name] = item.get("bounds")
                layer_transforms[unique_name] = item.get("transform") or _matrix_identity()
                layer_group_paths[unique_name] = list(item.get("group_path") or [unique_name])
                split_curve_groups.append(
                    {
                        "name": unique_name,
                        "element": group,
                        "group_path": list(item.get("group_path") or [unique_name]),
                    }
                )
                signature = item.get("signature")
                if signature:
                    signature_to_layers.setdefault(str(signature), []).append(unique_name)
                signature_raw = item.get("signature_raw")
                if signature_raw:
                    signature_to_layers_raw.setdefault(str(signature_raw), []).append(unique_name)
        else:
            unique_layer_names = _make_unique_names([str(layer["name"]) for layer in layers])
            for layer, unique_name in zip(layers, unique_layer_names):
                group = _clone_xml_element(layer["element"])
                group.set("id", unique_name)
                visible_groups.append(group)
                visible_names.append(unique_name)
                mask_bindings[unique_name] = list(layer["mask_refs"])
                clip_bindings[unique_name] = list(layer["clip_refs"])
                layer_fill_specs[unique_name] = {}
                layer_stroke_specs[unique_name] = {}
                layer_bounds[unique_name] = None
                layer_transforms[unique_name] = _matrix_identity()
                layer_group_paths[unique_name] = [unique_name]
                split_curve_groups.append({"name": unique_name, "element": group, "group_path": [unique_name]})
    else:
        flat_group = _flatten_layers_to_group(layers, name="Layer_1")
        if flat_group is not None:
            visible_groups.append(flat_group)
            visible_names.append("Layer_1")
            all_mask_refs = sorted({ref for layer in layers for ref in layer["mask_refs"]})
            all_clip_refs = sorted({ref for layer in layers for ref in layer["clip_refs"]})
            mask_bindings["Layer_1"] = all_mask_refs
            clip_bindings["Layer_1"] = all_clip_refs
            layer_fill_specs["Layer_1"] = {}
            layer_stroke_specs["Layer_1"] = {}
            layer_bounds["Layer_1"] = None
            layer_transforms["Layer_1"] = _matrix_identity()
            layer_group_paths["Layer_1"] = ["Layer_1"]

    def resolve_ref_bindings(
        refs_by_layer: Dict[str, List[str]],
        defs_by_id: Dict[str, ET.Element],
        allow_bounds_fallback: bool = True,
    ) -> Tuple[Dict[str, List[str]], Dict[str, List[str]]]:
        """Resolve refs to visible layers when definitions match imported geometry."""
        resolved_layer_bindings: Dict[str, List[str]] = {}
        for target_layer_name, refs in list(refs_by_layer.items()):
            unresolved_refs: List[str] = []
            target_transform = layer_transforms.get(target_layer_name, _matrix_identity())
            for definition_ref in refs:
                definition_node = defs_by_id.get(definition_ref)
                if definition_node is None:
                    unresolved_refs.append(definition_ref)
                    continue

                transformed_matches: List[str] = []
                signatures = _collect_geometry_signatures(definition_node, target_transform)
                for signature in signatures:
                    transformed_matches.extend(signature_to_layers.get(signature, []))

                raw_matches: List[str] = []
                if not transformed_matches or target_layer_name in transformed_matches:
                    raw_signatures = _collect_geometry_signatures(definition_node, include_transform=False)
                    for signature in raw_signatures:
                        raw_matches.extend(signature_to_layers_raw.get(signature, []))

                matched_all = list(dict.fromkeys(transformed_matches + raw_matches))
                matched_non_self = [name for name in matched_all if name != target_layer_name]
                if allow_bounds_fallback and not matched_non_self:
                    # Fallback for editor exports with tiny geometric deltas
                    # (e.g. viewport clip rect dimensions rounded differently).
                    definition_bounds = _definition_geometry_bounds(definition_node, target_transform)
                    scored_matches: List[Tuple[float, str]] = []
                    for candidate_name, candidate_bounds in layer_bounds.items():
                        score = _bounds_match_score(definition_bounds, candidate_bounds)
                        if score > 0.98:
                            scored_matches.append((score, candidate_name))
                    scored_matches.sort(reverse=True)
                    bounds_matches = [name for _, name in scored_matches]
                    matched_all = list(dict.fromkeys(matched_all + bounds_matches))
                    matched_non_self = [name for name in matched_all if name != target_layer_name]

                if matched_non_self:
                    existing = resolved_layer_bindings.setdefault(target_layer_name, [])
                    for matched_layer in matched_non_self:
                        if matched_layer not in existing:
                            existing.append(matched_layer)
                elif not matched_all:
                    unresolved_refs.append(definition_ref)
                # self-only matches count as resolved and don't require helper layers.

            refs_by_layer[target_layer_name] = unresolved_refs
        return refs_by_layer, resolved_layer_bindings

    if split_layers and signature_to_layers:
        clip_bindings, clip_layer_bindings = resolve_ref_bindings(clip_bindings, clip_defs, allow_bounds_fallback=False)
        mask_bindings, mask_layer_bindings = resolve_ref_bindings(mask_bindings, mask_defs, allow_bounds_fallback=True)

    # A group holding a single object adds nesting without adding meaning, so
    # those levels are dropped before GP layer groups are created.
    layer_group_paths = _collapse_single_object_groups(layer_group_paths)

    if not split_layers:
        # Single-layer GP import should not emit extra hidden helper layers.
        mask_bindings = {name: [] for name in visible_names}
        clip_bindings = {name: [] for name in visible_names}
        mask_layer_bindings = {}
        clip_layer_bindings = {}

    referenced_mask_ids = {ref for refs in mask_bindings.values() for ref in refs}
    referenced_clip_ids = {ref for refs in clip_bindings.values() for ref in refs}
    mask_groups = _build_definition_groups(
        {definition_id: mask_defs[definition_id] for definition_id in referenced_mask_ids if definition_id in mask_defs},
        MASK_LAYER_PREFIX,
    )
    clip_groups = _build_definition_groups(
        {definition_id: clip_defs[definition_id] for definition_id in referenced_clip_ids if definition_id in clip_defs},
        CLIP_LAYER_PREFIX,
    )
    mask_names = [group.get("id", "") for group in mask_groups]
    clip_names = [group.get("id", "") for group in clip_groups]

    gp_visible_groups = []
    for group, visible_name in zip(visible_groups, visible_names):
        group_copy = _clone_xml_element(group)
        if split_layers:
            keep_mask_refs = set(mask_bindings.get(visible_name, []))
            keep_clip_refs = set(clip_bindings.get(visible_name, []))
            _strip_mask_clip_references(group_copy, keep_mask_refs, keep_clip_refs)
        gp_visible_groups.append(group_copy)

    gp_groups = list(gp_visible_groups)
    gp_groups.extend(mask_groups)
    gp_groups.extend(clip_groups)
    gp_svg = _build_svg_document(root, gp_groups) if gp_groups else None

    curve_svg = _build_svg_document(root, visible_groups) if visible_groups else None

    return {
        "metadata": metadata,
        "visible_names": visible_names,
        "mask_names": mask_names,
        "clip_names": clip_names,
        "mask_bindings": mask_bindings,
        "clip_bindings": clip_bindings,
        "mask_layer_bindings": mask_layer_bindings,
        "clip_layer_bindings": clip_layer_bindings,
        "layer_fill_specs": layer_fill_specs,
        "layer_stroke_specs": layer_stroke_specs,
        "layer_bounds": layer_bounds,
        "layer_transforms": layer_transforms,
        "layer_group_paths": layer_group_paths,
        "gradient_defs": gradient_defs,
        "split_curve_groups": split_curve_groups,
        "gp_svg": gp_svg,
        "curve_svg": curve_svg,
        "split_layers": split_layers,
    }
