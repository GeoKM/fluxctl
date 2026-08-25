"""Reporting operations exposed to Fluxctl frontends.

These wrappers are intentionally small during the migration. The report
implementation will move here after callers stop depending on the monolithic
Studio service module.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from ..decoding import load_builtin_decoders
from ..detection import detect_encoding, detect_layout
from ..filesystem_detection import detect_filesystem
from ..filesystems import TrackSectorImage, load_builtin_filesystems
from ..layouts.loader import ensure_layout_loaded, load_builtin_layouts
from ..output import atomic_write_text
from ..reports.map import (
    apply_c64_cpm_2_2_logical_overlay,
    build_cbm_bam_block_map,
    build_disk_map,
    build_disk_map_from_tracksectors,
    render_svg,
)
from ..reports.qc import build_qc_report, build_qc_report_from_tracks, write_qc_report_json
from ..reports.preservation import build_flat_sector_diagnostic, build_sector_diagnostic
from ..scp import parse_scp
from .image_operations import get_decoder, prepare_image, prefix_track_count_for_size


def build_qc_for_image(path: Path, layout_id: Optional[str], encoding: str, operation=None):
    load_builtin_decoders()
    load_builtin_layouts()
    if path.suffix.lower() == ".scp":
        image = parse_scp(path)
        layout = ensure_layout_loaded(layout_id) if layout_id else None
        selected_encoding = layout.encoding if layout else encoding
        return build_qc_report(image, get_decoder(selected_encoding), layout=layout, operation=operation)
    image_obj = prepare_image(path, layout_id, encoding, operation=operation)
    if not isinstance(image_obj, TrackSectorImage):
        raise ValueError("Image could not be reconstructed into sector tracks")
    layout = ensure_layout_loaded(layout_id) if layout_id else None
    return build_qc_report_from_tracks(image_obj.tracks, layout=layout, track_step=1)


def build_disk_map_for_image(path: Path, layout_id: Optional[str], encoding: str, map_view: str, operation=None):
    load_builtin_decoders()
    load_builtin_layouts()
    load_builtin_filesystems()
    if map_view == "bam":
        image_obj = prepare_image(path, layout_id, encoding, operation=operation)
        layout = ensure_layout_loaded(layout_id) if layout_id else None
        max_tracks = (
            prefix_track_count_for_size(layout, path.stat().st_size)
            if layout is not None and path.suffix.lower() in {".d64", ".d71"}
            else None
        )
        detection = detect_filesystem(image_obj)
        if detection.plugin is None or not hasattr(detection.plugin, "bam_blocks"):
            raise ValueError("No CBM DOS BAM is available for this image")
        return build_cbm_bam_block_map(detection.plugin.bam_blocks(max_tracks=max_tracks))
    if path.suffix.lower() == ".scp":
        image = parse_scp(path)
        layout = ensure_layout_loaded(layout_id) if layout_id else None
        selected_encoding = layout.encoding if layout else encoding
        if layout is None:
            mfm_candidate = detect_layout(image, "mfm")
            if mfm_candidate is not None:
                layout = mfm_candidate.layout
                selected_encoding = layout.encoding
            elif encoding == "auto":
                encoding_candidate = detect_encoding(image)
                if encoding_candidate is not None:
                    selected_encoding = encoding_candidate.encoding
                    layout_candidate = detect_layout(image, selected_encoding)
                    layout = layout_candidate.layout if layout_candidate else None
        disk_map = build_disk_map(image, get_decoder(selected_encoding), layout=layout, operation=operation)
        if map_view == "logical" and layout and layout.layout_id == "commodore_gcr_1541_170k":
            try:
                image_obj = prepare_image(path, layout.layout_id, layout.encoding)
                detection = detect_filesystem(image_obj)
                if detection.primary == "c64_cpm_2_2":
                    allocated = detection.plugin.allocation_blocks() if detection.plugin is not None and hasattr(detection.plugin, "allocation_blocks") else None
                    return apply_c64_cpm_2_2_logical_overlay(disk_map, allocated)
            except Exception:
                pass
        return disk_map
    selected_layout = layout_id
    selected_encoding = encoding
    if selected_layout is None:
        from .image_operations import probe_flat_image

        candidates = probe_flat_image(path)
        if candidates:
            selected_layout = candidates[0].layout_id
            selected_encoding = candidates[0].encoding or encoding
    image_obj = prepare_image(path, selected_layout, selected_encoding, operation=operation)
    if not isinstance(image_obj, TrackSectorImage):
        raise ValueError("Image could not be reconstructed into sector tracks")
    layout = ensure_layout_loaded(selected_layout) if selected_layout else None
    disk_map = build_disk_map_from_tracksectors(image_obj.tracks, layout=layout)
    if map_view == "logical" and selected_layout == "commodore_gcr_1541_170k":
        try:
            detection = detect_filesystem(image_obj)
            if detection.primary == "c64_cpm_2_2":
                allocated = detection.plugin.allocation_blocks() if detection.plugin is not None and hasattr(detection.plugin, "allocation_blocks") else None
                return apply_c64_cpm_2_2_logical_overlay(disk_map, allocated)
        except Exception:
            pass
    return disk_map


def sector_diagnostic_for_image(
    path: Path,
    layout_id: Optional[str],
    encoding: str,
    track: int,
    head: int,
    sector_id: int,
):
    """Return preservation diagnostics for one physical sector."""

    load_builtin_decoders()
    load_builtin_layouts()
    layout = ensure_layout_loaded(layout_id) if layout_id else None
    if path.suffix.lower() == ".scp":
        image = parse_scp(path)
        selected_encoding = layout.encoding if layout else encoding
        return build_sector_diagnostic(
            image,
            get_decoder(selected_encoding),
            layout,
            track,
            head,
            sector_id,
        )
    selected_layout = layout_id
    selected_encoding = encoding
    if selected_layout is None:
        from .image_operations import probe_flat_image

        candidates = probe_flat_image(path)
        if candidates:
            selected_layout = candidates[0].layout_id
            selected_encoding = candidates[0].encoding or encoding
    image_obj = prepare_image(path, selected_layout, selected_encoding)
    if not isinstance(image_obj, TrackSectorImage):
        raise ValueError("Image could not be reconstructed into sector tracks")
    return build_flat_sector_diagnostic(image_obj.tracks, track, head, sector_id)


def export_qc_json(
    path: Path,
    output: Path,
    layout_id: Optional[str],
    encoding: str,
    *,
    overwrite: bool = False,
) -> Path:
    """Build and write a QC JSON report without invoking the CLI."""

    report = build_qc_for_image(path, layout_id, encoding)
    write_qc_report_json(report, output, overwrite=overwrite)
    return output


def export_disk_map_svg(
    path: Path,
    output: Path,
    layout_id: Optional[str],
    encoding: str,
    *,
    map_view: str = "physical",
    overwrite: bool = False,
) -> Path:
    """Build and write an SVG disk map without invoking the CLI."""

    disk_map = build_disk_map_for_image(path, layout_id, encoding, map_view)
    atomic_write_text(output, render_svg(disk_map), overwrite=overwrite, source_paths=[path])
    return output
