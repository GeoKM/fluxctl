"""Application services shared by Fluxctl Studio and future frontends."""
from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from . import __version__
from .cli import (
    _doctor_report,
    _get_decoder,
    _prepare_image,
    _probe_flat_image,
    _prefix_track_count_for_size,
)
from .decoding import load_builtin_decoders
from .detection import detect_encoding, detect_layout
from .filesystem_detection import detect_filesystem
from .filesystems import TrackSectorImage, load_builtin_filesystems
from .layouts.loader import ensure_layout_loaded, load_builtin_layouts
from .plugins import registry
from .reports.map import (
    DiskMap,
    apply_c64_cpm_2_2_logical_overlay,
    build_cbm_bam_block_map,
    build_disk_map,
    build_disk_map_from_tracksectors,
)
from .reports.qc import DiskQCReport, build_qc_report, build_qc_report_from_tracks
from .scp import parse_scp


@dataclass(frozen=True)
class CommandResult:
    """Completed CLI command result."""

    args: list[str]
    returncode: int
    stdout: str
    stderr: str


@dataclass(frozen=True)
class ImageSummary:
    """High-level image metadata for the Studio dashboard."""

    path: str
    size: int
    kind: str
    layout_id: str
    encoding: str
    filesystem: str
    confidence: float
    evidence: list[str]


@dataclass(frozen=True)
class FileEntryView:
    """Filesystem entry suitable for display in the GUI."""

    name: str
    kind: str
    size: int


def run_fluxctl_command(args: list[str], cwd: Optional[Path] = None) -> CommandResult:
    """Run a fluxctl CLI command using the current interpreter."""

    cmd = [sys.executable, "-m", "fluxctl.cli", *args]
    completed = subprocess.run(
        cmd,
        cwd=str(cwd) if cwd else None,
        text=True,
        capture_output=True,
        check=False,
    )
    return CommandResult(args=args, returncode=completed.returncode, stdout=completed.stdout, stderr=completed.stderr)


def doctor_report(hxcfe: Optional[Path] = None) -> dict:
    """Return the same doctor report used by the CLI."""

    return _doctor_report(hxcfe)


def load_layout_options() -> list[dict[str, object]]:
    """Return layout descriptors in a compact GUI-friendly shape."""

    layouts = load_builtin_layouts()
    return [
        {
            "layout_id": layout.layout_id,
            "name": layout.name,
            "encoding": layout.encoding,
            "tracks": layout.tracks,
            "sides": layout.sides,
            "sectors_per_track": layout.sectors_per_track,
            "sector_size": layout.sector_size,
        }
        for layout in sorted(layouts, key=lambda item: item.layout_id)
    ]


def summarize_image(path: Path, hxcfe: Optional[Path] = None) -> ImageSummary:
    """Probe an image and return the best current interpretation."""

    load_builtin_decoders()
    load_builtin_layouts()
    load_builtin_filesystems()

    if path.suffix.lower() != ".scp":
        candidates = _probe_flat_image(path)
    else:
        image = parse_scp(path)
        hint = None
        if hxcfe:
            from .cli import _maybe_hxc_hint

            hint = _maybe_hxc_hint(path, hxcfe)
        encoding = detect_encoding(image, path=path, hint=hint)
        layout = detect_layout(image, encoding.encoding, path, hint=hint) if encoding else None
        candidates = []
        if layout:
            fs_name = ""
            try:
                image_obj = _prepare_image(path, layout.layout.layout_id, layout.layout.encoding)
                fs_detection = detect_filesystem(image_obj, path_name=path.name)
                fs_name = fs_detection.primary or ""
                fs_evidence = [
                    f"filesystem_confidence={fs_detection.confidence:.2f}",
                    *fs_detection.evidence,
                    *[
                        f"filesystem_region={region.region}:{region.filesystem}"
                        for region in fs_detection.regions
                    ],
                ]
            except Exception:
                fs_name = ""
                fs_evidence = ["filesystem_probe_failed=1"]
            candidates.append(
                {
                    "layout_id": layout.layout.layout_id,
                    "encoding": layout.layout.encoding,
                    "filesystem": fs_name,
                    "score": layout.score,
                    "evidence": (encoding.evidence if encoding else []) + layout.evidence + fs_evidence,
                }
            )
        elif encoding:
            candidates.append(
                {
                    "layout_id": "",
                    "encoding": encoding.encoding,
                    "filesystem": "",
                    "score": encoding.confidence,
                    "evidence": encoding.evidence,
                }
            )

    if not candidates:
        return ImageSummary(str(path), path.stat().st_size, path.suffix.lower().lstrip(".") or "image", "", "", "", 0.0, [])

    best = candidates[0]
    if not isinstance(best, dict):
        best = best.__dict__
    return ImageSummary(
        path=str(path),
        size=path.stat().st_size,
        kind=path.suffix.lower().lstrip(".") or "image",
        layout_id=str(best.get("layout_id") or ""),
        encoding=str(best.get("encoding") or ""),
        filesystem=str(best.get("filesystem") or ""),
        confidence=float(best.get("score") or 0.0),
        evidence=list(best.get("evidence") or []),
    )


def build_qc_for_image(path: Path, layout_id: Optional[str], encoding: str = "mfm") -> DiskQCReport:
    """Build a QC report for SCP or flat images."""

    load_builtin_decoders()
    load_builtin_layouts()
    if path.suffix.lower() == ".scp":
        image = parse_scp(path)
        layout = ensure_layout_loaded(layout_id) if layout_id else None
        selected_encoding = layout.encoding if layout else encoding
        decoder = _get_decoder(selected_encoding)
        return build_qc_report(image, decoder, layout=layout)

    image_obj = _prepare_image(path, layout_id, encoding)
    if not isinstance(image_obj, TrackSectorImage):
        raise ValueError("Image could not be reconstructed into sector tracks")
    layout = ensure_layout_loaded(layout_id) if layout_id else None
    return build_qc_report_from_tracks(image_obj.tracks, layout=layout, track_step=1)


def build_disk_map_for_image(
    path: Path,
    layout_id: Optional[str],
    encoding: str = "mfm",
    map_view: str = "logical",
) -> DiskMap:
    """Build an in-memory disk map for the Studio visualizer."""

    load_builtin_decoders()
    load_builtin_layouts()
    load_builtin_filesystems()
    if map_view == "bam":
        image_obj = _prepare_image(path, layout_id, encoding)
        layout = ensure_layout_loaded(layout_id) if layout_id else None
        max_tracks = (
            _prefix_track_count_for_size(layout, path.stat().st_size)
            if layout is not None and path.suffix.lower() in {".d64", ".d71"}
            else None
        )
        detection = detect_filesystem(image_obj, path_name=path.name)
        if detection.plugin is None or not hasattr(detection.plugin, "bam_blocks"):
            raise ValueError("No CBM DOS BAM is available for this image")
        return build_cbm_bam_block_map(detection.plugin.bam_blocks(max_tracks=max_tracks))

    if path.suffix.lower() == ".scp":
        image = parse_scp(path)
        layout = ensure_layout_loaded(layout_id) if layout_id else None
        selected_encoding = layout.encoding if layout else encoding
        decoder = _get_decoder(selected_encoding)
        disk_map = build_disk_map(image, decoder, layout=layout)
        if map_view == "logical" and layout and layout.layout_id == "commodore_gcr_1541_170k":
            try:
                image_obj = _prepare_image(path, layout.layout_id, layout.encoding)
                detection = detect_filesystem(image_obj, path_name=path.name)
                if detection.primary == "c64_cpm_2_2":
                    allocated = (
                        detection.plugin.allocation_blocks()
                        if detection.plugin is not None and hasattr(detection.plugin, "allocation_blocks")
                        else None
                    )
                    return apply_c64_cpm_2_2_logical_overlay(disk_map, allocated)
            except Exception:
                pass
        return disk_map

    image_obj = _prepare_image(path, layout_id, encoding)
    if not isinstance(image_obj, TrackSectorImage):
        raise ValueError("Image could not be reconstructed into sector tracks")
    disk_map = build_disk_map_from_tracksectors(image_obj.tracks)
    if map_view == "logical" and layout_id == "commodore_gcr_1541_170k":
        try:
            detection = detect_filesystem(image_obj, path_name=path.name)
            if detection.primary == "c64_cpm_2_2":
                allocated = (
                    detection.plugin.allocation_blocks()
                    if detection.plugin is not None and hasattr(detection.plugin, "allocation_blocks")
                    else None
                )
                return apply_c64_cpm_2_2_logical_overlay(disk_map, allocated)
        except Exception:
            pass
    return disk_map


def list_files(path: Path, layout_id: Optional[str], encoding: str = "mfm") -> list[FileEntryView]:
    """Return root directory entries when a supported filesystem is detected."""

    load_builtin_filesystems()
    image = _prepare_image(path, layout_id, encoding)
    filesystem = detect_filesystem(image, path_name=path.name).plugin
    if filesystem is None:
        return []
    try:
        entries = filesystem.list_directory("/")
    except Exception:
        return []
    return [FileEntryView(entry.name, "<DIR>" if entry.is_dir else "file", entry.size) for entry in entries]


def provenance_json(path: Path) -> dict:
    """Load a provenance sidecar for display."""

    return json.loads(path.read_text(encoding="utf-8"))


def runtime_version() -> str:
    """Return the fluxctl version used by Studio."""

    return __version__
