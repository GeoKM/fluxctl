"""Fluxctl command line interface."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import platform
import shutil
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import wraps
from pathlib import Path
from typing import Optional, Sequence

import typer

from . import __version__
from .apple2 import (
    APPLE2_DO_ORDER,
    APPLE2_PO_ORDER,
    Apple2SectorImage,
    load_apple2_tracks,
    tracks_from_apple2_sector_image,
)
from .decoding import load_builtin_decoders
from .decoding.mfm import mfm_decoder
from .detection import detect_encoding, detect_layout, infer_track_step, logical_track_count
from .exceptions import ExportError, FluxDecodeError, FluxctlError
from .filesystem_detection import FilesystemDetection, detect_filesystem
from .exporters import load_builtin_exporters
from .filesystems import Filesystem, RawSectorImage, TrackSectorImage, load_builtin_filesystems
from .filesystems.cpm import cpm_directory_score_for_layout
from .imd import load_imd_image
from .layouts.loader import ensure_layout_loaded, load_builtin_layouts
from .models import Bitstream, CandidateFormat, LayoutDescriptor, ProvenanceRecord
from .plugins import registry
from .provenance import write_provenance
from .reports.map import build_disk_map, build_disk_map_from_tracksectors, render_ascii, render_svg
from .reports.qc import build_qc_report, build_qc_report_from_tracks, write_qc_report_json, write_qc_report_text
from .scp import parse_scp, sha256_file
from .sector.models import Sector, TrackNibbles, TrackSectors
from .sector.reconstruct import build_track_sectors_from_revolutions
from .sector.reconstruct_gcr import (
    extract_best_gcr_nibble_stream,
    score_gcr_alignment,
)
from .external.hxc import probe_hxcfe
from .geohints import LayoutHint
from .trs80 import load_trs80_image
from .native import (
    is_native_available,
    native_candidate_paths,
    native_load_errors,
    windows_process_architecture,
    windows_rust_target,
)

APP_HELP = """Inspect, verify, recover, and convert floppy flux captures.

Typical workflows:
  fluxctl doctor
  fluxctl probe disk.scp
  fluxctl qc disk.scp --layout ibm_mfm_720k --json-out qc.json
  fluxctl convert disk.scp --layout ibm_mfm_720k --to raw --out disk.img
  fluxctl roundtrip disk.scp --layout amiga_mfm_880k --to adf
  fluxctl extract disk.img --list

Use `fluxctl COMMAND --help` for command-specific examples.
"""

app = typer.Typer(add_completion=False, help=APP_HELP)
provenance_app = typer.Typer(help="Inspect provenance records")
app.add_typer(provenance_app, name="provenance")


def _handle_cli_errors(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except FluxctlError as exc:
            typer.secho(f"Error: {exc}", fg=typer.colors.RED, err=True)
            raise typer.Exit(code=1)

    return wrapper


@app.callback(invoke_without_command=True)
def main(ctx: typer.Context) -> None:
    """Inspect, verify, recover, and convert floppy flux captures."""

    if ctx.invoked_subcommand is None:
        typer.echo(ctx.get_help())
        raise typer.Exit()


def _get_decoder(encoding: str):
    load_builtin_decoders()
    if encoding == "mfm":
        return mfm_decoder
    plugin = registry.encoding.get(encoding)
    if plugin:
        return plugin.entry
    raise FluxDecodeError(f"Unknown encoding '{encoding}'")


def _status_check(name: str, status: str, detail: str, suggestion: str = "") -> dict[str, str]:
    return {"name": name, "status": status, "detail": detail, "suggestion": suggestion}


def _format_doctor_status(status: str) -> tuple[str, str]:
    if status == "ok":
        return "OK", typer.colors.GREEN
    if status == "warn":
        return "WARN", typer.colors.YELLOW
    return "FAIL", typer.colors.RED


def _hxcfe_candidate_paths(explicit_path: Optional[Path] = None) -> list[Path]:
    candidates: list[Path] = []
    if explicit_path is not None:
        candidates.append(explicit_path)
    if found := shutil.which("hxcfe"):
        candidates.append(Path(found))

    roots = [
        Path(__file__).resolve().parents[2],
        Path.cwd(),
    ]
    seen_roots: set[Path] = set()
    for root in roots:
        root = root.resolve()
        if root in seen_roots:
            continue
        seen_roots.add(root)
        sibling = root.parent / "HxCFloppyEmulator"
        candidates.extend(
            [
                sibling / "build" / "hxcfe",
                sibling / "build" / "hxcfe.exe",
                sibling / "HxCFloppyEmulator_cmdline" / "build" / "hxcfe",
                sibling / "HxCFloppyEmulator_cmdline" / "build" / "hxcfe.exe",
            ]
        )

    unique_candidates: list[Path] = []
    seen_candidates: set[Path] = set()
    for candidate in candidates:
        candidate = candidate.expanduser()
        if candidate in seen_candidates:
            continue
        seen_candidates.add(candidate)
        unique_candidates.append(candidate)
    return unique_candidates


def _is_executable(path: Path) -> bool:
    """Accept Windows executables without relying on Unix execute bits."""

    if not path.is_file():
        return False
    if os.name == "nt" and path.suffix.lower() in {".exe", ".com", ".bat", ".cmd"}:
        return True
    return os.access(path, os.X_OK)


def _first_executable_hxcfe(explicit_path: Optional[Path] = None) -> Path | None:
    for candidate in _hxcfe_candidate_paths(explicit_path):
        if _is_executable(candidate):
            return candidate
    return None


def _native_build_suggestion() -> str:
    build_command = "cargo build --manifest-path native/fluxctl_native/Cargo.toml --release"
    if os.name == "nt":
        process_arch = windows_process_architecture() or "unknown"
        rust_target = windows_rust_target()
        target_command = (
            f"rustup target add {rust_target} && {build_command} --target {rust_target}"
            if rust_target
            else build_command
        )
        return (
            "Install Rust from https://rustup.rs and Microsoft C++ Build Tools 14.0+ "
            "with the Desktop development with C++ workload so link.exe is available. "
            f"This Python process is {process_arch}; build the matching DLL with "
            f"`{target_command}`. Use an x64 Native Tools prompt for x86_64-pc-windows-msvc "
            "or an ARM64 Native Tools prompt for aarch64-pc-windows-msvc. "
            "`platform.machine()` reports the host on emulated Windows Python and must not "
            "be used to select the DLL architecture."
        )
    return (
        "Install Rust with `curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh` "
        f"if cargo is missing, then run `{build_command}`."
    )


def _doctor_report(hxcfe: Optional[Path] = None) -> dict:
    load_builtin_decoders()
    load_builtin_exporters()
    load_builtin_filesystems()
    layouts = load_builtin_layouts()

    checks = [
        _status_check(
            "python",
            "ok" if sys.version_info >= (3, 11) else "fail",
            f"{platform.python_version()} at {sys.executable}",
            "Use Python 3.11 or newer." if sys.version_info < (3, 11) else "",
        ),
        _status_check("fluxctl", "ok", f"version {__version__}"),
        _status_check(
            "layouts",
            "ok" if layouts else "fail",
            f"{len(registry.layout)} loaded",
            "Reinstall fluxctl so packaged layout JSON files are available." if not layouts else "",
        ),
        _status_check(
            "decoders",
            "ok" if registry.encoding else "fail",
            ", ".join(sorted(registry.encoding)) or "none",
            "Reinstall fluxctl; built-in decoder registration failed." if not registry.encoding else "",
        ),
        _status_check(
            "exporters",
            "ok" if registry.exporter else "fail",
            ", ".join(sorted(registry.exporter)) or "none",
            "Reinstall fluxctl; built-in exporter registration failed." if not registry.exporter else "",
        ),
        _status_check(
            "filesystems",
            "ok" if registry.filesystem else "fail",
            ", ".join(sorted(registry.filesystem)) or "none",
            "Reinstall fluxctl; built-in filesystem registration failed." if not registry.filesystem else "",
        ),
    ]

    native_disabled = os.environ.get("FLUXCTL_DISABLE_NATIVE") == "1"
    native_candidates = [str(path) for path in native_candidate_paths()]
    if native_disabled:
        native_status = "warn"
        native_detail = "disabled by FLUXCTL_DISABLE_NATIVE=1"
        native_suggestion = "Unset FLUXCTL_DISABLE_NATIVE to allow native decoder acceleration."
    elif is_native_available():
        native_status = "ok"
        native_detail = "available"
        native_suggestion = ""
    else:
        native_status = "warn"
        load_errors = native_load_errors()
        native_detail = "not built or not loadable"
        if load_errors:
            actionable_errors = [error for error in load_errors if not error.endswith(": not found")]
            native_detail = f"{native_detail}: {'; '.join(actionable_errors or load_errors)}"
        native_suggestion = _native_build_suggestion()
    checks.append(
        _status_check(
            "native acceleration",
            native_status,
            native_detail,
            native_suggestion,
        )
    )

    if importlib.util.find_spec("greaseweazle") is None:
        checks.append(
            _status_check(
                "greaseweazle",
                "warn",
                "optional package not importable",
                "Install support deps with `.venv/bin/pip install -e \".[greaseweazle]\"`, then run `git clone https://github.com/keirf/Greaseweazle.git ../greaseweazle` and `.venv/bin/pip install -e ../greaseweazle`.",
            )
        )
    else:
        checks.append(_status_check("greaseweazle", "ok", "optional package importable"))

    hxcfe_path = hxcfe if hxcfe is not None else _first_executable_hxcfe()
    if hxcfe_path is None:
        checks.append(
            _status_check(
                "hxcfe",
                "warn",
                "optional binary not found on PATH or sibling checkout",
                "Clone/build with `git clone https://github.com/jfdelnero/HxCFloppyEmulator.git ../HxCFloppyEmulator` and `make -C ../HxCFloppyEmulator/build HxCFloppyEmulator_cmdline`, then pass --hxcfe or add hxcfe to PATH.",
            )
        )
    elif not hxcfe_path.exists():
        checks.append(_status_check("hxcfe", "fail", f"{hxcfe_path} does not exist", "Check the --hxcfe path."))
    elif not _is_executable(hxcfe_path):
        checks.append(
            _status_check("hxcfe", "fail", f"{hxcfe_path} is not executable", "Run chmod +x or rebuild hxcfe.")
        )
    else:
        checks.append(_status_check("hxcfe", "ok", str(hxcfe_path)))

    overall = "fail" if any(check["status"] == "fail" for check in checks) else "ok"
    return {
        "tool": "fluxctl",
        "version": __version__,
        "overall": overall,
        "checks": checks,
        "native_candidates": native_candidates,
    }


def _first_diff_offset(a: bytes, b: bytes) -> Optional[int]:
    """Return the first offset where ``a`` and ``b`` differ."""

    limit = min(len(a), len(b))
    for idx in range(limit):
        if a[idx] != b[idx]:
            return idx
    if len(a) != len(b):
        return limit
    return None


def _resolve_encoding_for_compare(path: Path, encoding: str) -> str:
    """Resolve ``encoding`` for comparison, auto-detecting SCP inputs when requested."""

    if encoding != "auto":
        return encoding
    if path.suffix.lower() != ".scp":
        return "mfm"
    load_builtin_decoders()
    candidate = detect_encoding(parse_scp(path), path=path)
    if candidate is None:
        raise FluxDecodeError("Unable to infer encoding for SCP input; specify --encoding-a/--encoding-b")
    return candidate.encoding


def _maybe_hxc_hint(path: Path, hxcfe: Optional[Path]) -> LayoutHint | None:
    """Run HxC CLI when requested and convert the result into a layout hint."""

    if not hxcfe:
        return None
    metadata = probe_hxcfe(path, hxcfe)
    return metadata.to_layout_hint()


@app.command()
@_handle_cli_errors
def doctor(
    hxcfe: Optional[Path] = typer.Option(None, "--hxcfe", help="Optional hxcfe binary path to validate"),
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON"),
) -> None:
    """Check the local fluxctl installation and optional acceleration helpers.

    Examples:
    fluxctl doctor
    fluxctl doctor --json
    fluxctl doctor --hxcfe ~/src/HxCFloppyEmulator/HxCFloppyEmulator_cmdline/build/hxcfe
    """

    report = _doctor_report(hxcfe)
    if json_output:
        typer.echo(json.dumps(report, indent=2))
        return

    typer.echo(f"fluxctl {report['version']} doctor")
    for check in report["checks"]:
        label, color = _format_doctor_status(check["status"])
        typer.secho(f"{label:>4}", fg=color, nl=False)
        typer.echo(f"  {check['name']}: {check['detail']}")
        if check["suggestion"]:
            typer.echo(f"      {check['suggestion']}")
    if report["native_candidates"]:
        typer.echo("Native library search paths:")
        for path in report["native_candidates"]:
            typer.echo(f"  {path}")


def _image_bytes_for_compare(path: Path, layout_id: Optional[str], encoding: str) -> tuple[bytes, dict]:
    """Decode an image to a flat byte stream suitable for comparison."""

    resolved_encoding = _resolve_encoding_for_compare(path, encoding)
    image = _prepare_image(path, layout_id, encoding=resolved_encoding)
    if isinstance(image, RawSectorImage):
        payload = image.data
        kind = "raw"
    else:
        payload = b"".join(image.iter_sectors())
        kind = "sectors"
    return payload, {
        "path": str(path),
        "kind": kind,
        "encoding": resolved_encoding,
        "layout": layout_id or "",
    }


def _detect_cpm_variant(image) -> Optional[str]:
    """Lightweight CP/M flavor heuristic based on known system filenames."""

    try:
        sectors = []
        for idx, data in enumerate(image.iter_sectors()):
            if idx >= 512:
                break
            sectors.append(data)
    except Exception:
        return None

    joined = b"".join(sectors)
    if b"BOOTV3" in joined or b"BIOS3" in joined:
        return "c128_cpm_3_0"
    if b"BOOT " in joined or b"CPM+SYS" in joined:
        return "c64_cpm_2_2"
    return None


def _detect_amiga_fs(image) -> Optional[str]:
    """Detect Amiga OFS vs FFS from boot block DOS type."""

    try:
        data = b""
        for idx, sector in enumerate(image.iter_sectors()):
            data += sector
            if len(data) >= 4 or idx >= 1:
                break
        if len(data) < 4:
            return None
        if data[:3] != b"DOS":
            return None
        dostype = data[3]
        if dostype == 0:
            return "amiga_ofs"
        if dostype == 1:
            return "amiga_ffs"
        return None
    except Exception:
        return None


@app.command()
@_handle_cli_errors
def info(
    path: Path = typer.Argument(..., exists=True, readable=True),
    hxcfe: Optional[Path] = typer.Option(None, "--hxcfe", help="Path to an hxcfe binary for hints."),
) -> None:
    """Print basic image information (SCP, WOZ, PO/DO/NIB, IMG, ADF, Commodore, IMD, DSK/DMK)."""

    load_builtin_decoders()
    load_builtin_layouts()
    load_builtin_filesystems()
    ext = path.suffix.lower()

    # Flat images (.img/.adf/.d64/...) use the probe pipeline.
    if ext != ".scp":
        candidates = _probe_flat_image(path)
        if not candidates:
            raise FluxDecodeError("Unable to recognise image format")
        primary = sorted(candidates, key=lambda c: c.score, reverse=True)[0]
        layout_desc = registry.layout.get(primary.layout_id) if primary.layout_id else None
        image = _prepare_image(path, primary.layout_id, primary.encoding or "mfm")
        fs_name = _filesystem_name_for_image(image)
        amiga_fs = _detect_amiga_fs(image)
        typer.echo(f"Size: {path.stat().st_size} bytes")
        typer.echo(f"Layout: {primary.layout_id or 'unknown'} (encoding={primary.encoding or 'mfm'})")
        if layout_desc:
            typer.echo(
                f"Geometry: cylinders={layout_desc.tracks} heads={layout_desc.sides} "
                f"sectors/track={layout_desc.sectors_per_track} sector_size={layout_desc.sector_size}"
            )
        typer.echo(f"Filesystem: {amiga_fs or fs_name or primary.filesystem or 'unknown'}")
        typer.echo(f"Confidence: {primary.score:.2f}")
        if len(candidates) > 1:
            typer.echo("Other candidates:")
            for cand in sorted(candidates[1:], key=lambda c: c.score, reverse=True)[:3]:
                typer.echo(
                    f"- {cand.layout_id or 'unknown'} (encoding={cand.encoding or 'mfm'}) "
                    f"fs={cand.filesystem or 'unknown'} score={cand.score:.2f}"
                )
        return

    # SCP path: keep existing behaviour with extra filesystem hint when possible.
    scp = parse_scp(path)
    heads_with_flux = {
        track.side for track in scp.tracks if any(rev.interval_ns for rev in track.revolutions)
    }
    hxc_hint = _maybe_hxc_hint(path, hxcfe)
    encoding_candidate = detect_encoding(scp, path=path, hint=hxc_hint)
    layout_candidate = (
        detect_layout(scp, encoding_candidate.encoding, path, hint=hxc_hint)
        if encoding_candidate
        else None
    )
    track_ids = sorted({track.track for track in scp.tracks})
    track_count = len(track_ids)
    inferred_heads = 1
    inferred_cylinders = track_count
    if track_count >= 80 and track_count % 2 == 0 and track_ids and track_ids[0] == 0 and track_ids[-1] == track_count - 1:
        inferred_heads = 2
        inferred_cylinders = track_count // 2
    typer.echo(f"SCP version: {scp.version}")
    typer.echo(f"Tracks parsed: {len(scp.tracks)}")
    if layout_candidate:
        layout_desc = layout_candidate.layout
        layout_heads = layout_desc.sides
        if layout_desc.sides > 1 and len(heads_with_flux) <= 1:
            layout_heads = 1
            typer.echo("Assuming single-sided capture based on flux presence")
        elif layout_desc.sides > 1 and len(scp.tracks) <= layout_desc.tracks + 2:
            layout_heads = 1
            typer.echo("Assuming single-sided capture based on track count")
        typer.echo(f"Cylinders (layout): {layout_desc.tracks}")
        typer.echo(f"Heads (layout): {layout_heads}")
        expected_tracks = layout_desc.tracks * layout_heads
        extra_tracks = len(scp.tracks) - expected_tracks
        if extra_tracks > 0:
            cylinders_beyond = (extra_tracks + layout_heads - 1) // layout_heads
            typer.echo(f"Cylinders beyond layout: {cylinders_beyond}")
        elif extra_tracks < 0:
            cylinders_missing = (-extra_tracks + layout_heads - 1) // layout_heads
            typer.echo(f"Cylinders missing vs layout: {cylinders_missing}")
    else:
        typer.echo(f"Cylinders (inferred): {inferred_cylinders}")
        typer.echo(f"Heads (inferred): {inferred_heads}")
    typer.echo(f"Revolutions per track: {scp.revolutions_per_track}")


@app.command()
@_handle_cli_errors
def probe(
    path: Path = typer.Argument(..., exists=True, readable=True),
    hxcfe: Optional[Path] = typer.Option(None, "--hxcfe", help="Path to an hxcfe binary for hints."),
) -> None:
    """Run lightweight detection and print candidate layouts and decoders."""
    load_builtin_decoders()
    load_builtin_layouts()
    ext = path.suffix.lower()
    if ext != ".scp":
        candidates = _probe_flat_image(path)
        typer.echo(json.dumps([c.__dict__ for c in candidates], indent=2))
        return
    image = parse_scp(path)
    hxc_hint = _maybe_hxc_hint(path, hxcfe)
    candidates: list[CandidateFormat] = []

    encoding_candidate = detect_encoding(image, path=path, hint=hxc_hint)
    if encoding_candidate is None:
        candidates.append(
            CandidateFormat(
                candidate_id="unknown",
                encoding=None,
                layout_id=None,
                filesystem=None,
                score=0.0,
                evidence=["no decoder matched"],
            )
        )
        typer.echo(json.dumps([c.__dict__ for c in candidates], indent=2))
        raise typer.Exit(code=2)

    layout_candidate = detect_layout(
        image, encoding_candidate.encoding, path, hint=hxc_hint
    )
    if layout_candidate:
        filesystem: Optional[str]
        filesystem_evidence: list[str] = []
        try:
            fast_result = _fast_scp_filesystem_probe(path, layout_candidate.layout, encoding_candidate.encoding)
            if fast_result is not None:
                filesystem, filesystem_evidence = fast_result
            else:
                image_obj = _prepare_image(path, layout_candidate.layout.layout_id, encoding_candidate.encoding)
                detection = _filesystem_detection_for_image(image_obj, path)
                filesystem, filesystem_evidence = _filesystem_probe_payload(detection)
        except Exception:
            filesystem = None
            filesystem_evidence = ["filesystem_probe_failed=1"]
        candidates.append(
            CandidateFormat(
                candidate_id=layout_candidate.layout.layout_id,
                encoding=layout_candidate.layout.encoding,
                layout_id=layout_candidate.layout.layout_id,
                filesystem=filesystem,
                score=layout_candidate.score,
                evidence=encoding_candidate.evidence + layout_candidate.evidence + filesystem_evidence,
            )
        )
    else:
        candidates.append(
            CandidateFormat(
                candidate_id=f"{encoding_candidate.encoding}_decoder",
                encoding=encoding_candidate.encoding,
                layout_id=None,
                filesystem=None,
                score=encoding_candidate.confidence,
                evidence=encoding_candidate.evidence,
            )
        )

    typer.echo(json.dumps([c.__dict__ for c in candidates], indent=2))


def _fast_scp_filesystem_probe(
    path: Path, layout: LayoutDescriptor, encoding: str
) -> Optional[tuple[Optional[str], list[str]]]:
    """Probe SCP filesystems from the smallest useful prefix where safe.

    IBM FAT12 boot metadata, FATs, and root directory fit within the first
    cylinder for the supported floppy layouts. Decoding only that cylinder
    keeps `fluxctl probe` responsive on large synthetic SCP captures while
    still using the real FAT12 plugin rather than a filename/layout guess.
    """

    if layout.layout_id not in FAST_SCP_FAT12_LAYOUTS:
        return None
    if encoding != layout.encoding:
        return None

    track_limit = max(1, layout.sides)
    tracks = _decode_tracks(path, layout.layout_id, limit_tracks=track_limit, encoding=encoding)
    if not tracks:
        return None
    image_obj = TrackSectorImage(tracks, bytes_per_sector=layout.sector_size)
    image_obj.layout = layout
    _apply_layout_geometry(image_obj, layout)
    detection = _filesystem_detection_for_image(image_obj, path)
    filesystem, evidence = _filesystem_probe_payload(detection)
    if filesystem:
        evidence = [*evidence, "filesystem_probe_scope=first_cylinder"]
    return filesystem, evidence


def _select_best_gcr_nibbles(bitstreams: list[Bitstream], track: int, head: int) -> Optional[TrackNibbles]:
    """Pick the highest-confidence nibble stream from decoded revolutions."""

    best: Optional[TrackNibbles] = None
    best_score: tuple[int, float, int] = (-1, -1.0, 0)
    for bitstream in bitstreams:
        nibble_bytes = extract_best_gcr_nibble_stream(bitstream)
        valid_symbols, _ = score_gcr_alignment(bitstream.bits)
        confidence = bitstream.metrics.confidence or 0.0
        candidate_score = (valid_symbols, confidence, len(nibble_bytes))
        if candidate_score > best_score:
            source_label = ",".join(str(idx) for idx in bitstream.source_revs) or "rev0"
            best = TrackNibbles(
                track=track,
                head=head,
                gcr_bytes=nibble_bytes,
                source=source_label,
                confidence=confidence,
            )
            best_score = candidate_score
    return best


def _decode_tracks(
    path: Path,
    layout_id: Optional[str],
    limit_tracks: Optional[int] = None,
    encoding: Optional[str] = None,
    capture_nibbles: bool = False,
):
    scp = parse_scp(path)
    layout = ensure_layout_loaded(layout_id) if layout_id else None
    track_data: list[TrackSectors] = []
    nibble_data: list[TrackNibbles] = []
    selected_encoding = layout.encoding if layout else (encoding or "mfm")
    decoder = _get_decoder(selected_encoding)
    for ts in scp.tracks[: limit_tracks or None]:
        if layout and (ts.track >= layout.tracks or ts.side >= layout.sides):
            continue
        # Skip tracks with no captured revolutions.
        if not ts.revolutions:
            continue
        # Skip revolutions that have no flux intervals.
        revs = [rev for rev in ts.revolutions if getattr(rev, "interval_ns", None)]
        if not revs:
            continue
        # Respect per-track sector counts when available.
        expected_sectors = None
        if layout:
            # Determine the logical track index (no track_step here, so it is just ts.track).
            logical_track = ts.track
            try:
                expected_sectors = layout.expected_sectors_for_track(logical_track, ts.side)
            except Exception:
                # Fallback to a constant if the layout does not define per-track counts.
                expected_sectors = layout.sectors_per_track
        if selected_encoding == "gcr":
            if hasattr(decoder, "set_track"):
                decoder.set_track(ts.track)
            primary_bitstream = decoder.decode_revolution(revs[0])
            track_data.append(
                build_track_sectors_from_revolutions(
                    revs,
                    decoder,
                    cylinder=ts.track,
                    head=ts.side,
                    expected_sectors=expected_sectors,
                    encoding=selected_encoding,
                    timebase_ns=scp.timebase_ns,
                )
            )
            if capture_nibbles:
                bitstreams = [primary_bitstream]
                for rev in revs[1:]:
                    if hasattr(decoder, "set_track"):
                        decoder.set_track(ts.track)
                    bitstreams.append(decoder.decode_revolution(rev))
                nibble_candidate = _select_best_gcr_nibbles(bitstreams, ts.track, ts.side)
                if nibble_candidate:
                    nibble_data.append(nibble_candidate)
        else:
            track_data.append(
                build_track_sectors_from_revolutions(
                    revs,
                    decoder,
                    cylinder=ts.track,
                    head=ts.side,
                    expected_sectors=expected_sectors,
                    encoding=selected_encoding,
                )
            )
    if capture_nibbles and selected_encoding == "gcr":
        return track_data, nibble_data
    return track_data


def _detect_filesystem(image) -> Optional[Filesystem]:
    detection = detect_filesystem(image)
    return detection.plugin


def _filesystem_detection_for_image(image, path: Optional[Path] = None) -> FilesystemDetection:
    return detect_filesystem(image, path_name=path.name if path else "")


def _filesystem_probe_payload(detection: FilesystemDetection) -> tuple[Optional[str], list[str]]:
    evidence = [f"filesystem_confidence={detection.confidence:.2f}", *detection.evidence]
    for region in detection.regions:
        evidence.append(f"filesystem_region={region.region}:{region.filesystem}")
    return detection.primary, evidence


def _prepare_image(path: Path, layout_id: Optional[str], encoding: str):
    layout_desc = ensure_layout_loaded(layout_id) if layout_id else None
    ext = path.suffix.lower()

    if ext in {".woz", ".po", ".do", ".nib"} or (
        ext in {".img", ".dsk"} and layout_desc is not None and layout_desc.layout_id.startswith("apple2_")
    ):
        tracks, _metadata = load_apple2_tracks(path)
        return Apple2SectorImage(tracks, layout_desc)

    if layout_desc and ext == ".d81" and layout_desc.layout_id == "commodore_mfm_1581_800k":
        from .exporters.d81 import d81_bytes_to_physical_tracks

        image = TrackSectorImage(d81_bytes_to_physical_tracks(path.read_bytes()), bytes_per_sector=layout_desc.sector_size)
        image.layout = layout_desc
        image.set_geometry(
            layout_desc.sectors_per_track,
            layout_desc.sides,
            int(layout_desc.id_rules.get("sector_number_base", 1)),
        )
        return image

    # For flat images with a known layout, build TrackSectorImage directly from the blob
    # instead of trying to decode as SCP.
    if layout_desc and ext not in {".scp", ".imd", ".dsk", ".dmk"}:
        data_bytes = path.read_bytes()
        track_data = _sectors_from_blob(
            layout_desc,
            data_bytes,
            allow_pad=True,
            allow_prefix=ext in {".d64"},
        )
        if track_data:
            image = TrackSectorImage(track_data, bytes_per_sector=layout_desc.sector_size)
            image.layout = layout_desc
            _apply_layout_geometry(image, layout_desc)
            return image
        # Fall through to raw sector handling if reconstruction failed.

    if ext == ".img":
        return RawSectorImage(path.read_bytes())
    if ext == ".scp":
        if layout_desc and layout_desc.layout_id.startswith("amiga_"):
            track_data = _decode_amiga_tracks(path, layout_desc)
            image = TrackSectorImage(track_data, bytes_per_sector=layout_desc.sector_size)
            image.layout = layout_desc
            _apply_layout_geometry(image, layout_desc)
            return image
        if layout_desc and layout_desc.layout_id == "wang_ois_hs32_fm_315k":
            from .sector.reconstruct_wang import reconstruct_wang_track

            scp = parse_scp(path)
            track_data = [
                reconstruct_wang_track(ts.revolutions, ts.track, ts.side, layout_desc.sectors_per_track)
                for ts in scp.tracks
                if ts.track < layout_desc.tracks and ts.side < layout_desc.sides and ts.revolutions
            ]
            image = TrackSectorImage(track_data, bytes_per_sector=layout_desc.sector_size)
            image.layout = layout_desc
            _apply_layout_geometry(image, layout_desc)
            return image
        track_data = _decode_tracks(path, layout_id, encoding=encoding)
        if layout_desc and layout_desc.layout_id.startswith("apple2_"):
            return Apple2SectorImage(track_data, layout_desc)
        image = TrackSectorImage(track_data, bytes_per_sector=layout_desc.sector_size if layout_desc else None)
        if layout_desc:
            image.layout = layout_desc
            _apply_layout_geometry(image, layout_desc)
        return image
    if ext == ".imd":
        tracks, geom, _meta = load_imd_image(path)
        return _image_from_tracks(tracks, geom, layout_desc)
    if ext in {".dsk", ".dmk"}:
        tracks, geom, _meta = load_trs80_image(path)
        return _image_from_tracks(tracks, geom, layout_desc)
    if layout_desc:
        track_data = _decode_tracks(path, layout_id, encoding=encoding)
        image = TrackSectorImage(track_data, bytes_per_sector=layout_desc.sector_size)
        image.layout = layout_desc
        return image
    return RawSectorImage(path.read_bytes())


def _apply_layout_geometry(image: TrackSectorImage, layout: LayoutDescriptor) -> None:
    image.set_geometry(
        layout.sectors_per_track,
        layout.sides,
        int(layout.id_rules.get("sector_number_base", 1)),
    )


def _decode_amiga_tracks(path: Path, layout: LayoutDescriptor) -> list[TrackSectors]:
    scp = parse_scp(path)
    from .sector.reconstruct_amiga import reconstruct_amiga_greaseweazle, reconstruct_amiga_with_pll

    track_data: list[TrackSectors] = []
    for ts in scp.tracks:
        if ts.track >= layout.tracks or ts.side >= layout.sides or not ts.revolutions:
            continue
        candidate = reconstruct_amiga_greaseweazle(ts.revolutions, ts.track, ts.side, timebase_ns=scp.timebase_ns)
        if candidate is None:
            candidate = reconstruct_amiga_with_pll(ts.revolutions, ts.track, ts.side, timebase_ns=scp.timebase_ns)
        track_data.append(candidate)
    return track_data


FLAT_LAYOUT_PREFERENCES: dict[str, tuple[str, ...]] = {
    ".woz": ("apple2_gcr_nofs_140_140k",),
    ".po": ("apple2_gcr_nofs_140_140k",),
    ".do": ("apple2_gcr_nofs_140_140k",),
    ".nib": ("apple2_gcr_nofs_140_140k",),
    ".d64": ("commodore_gcr_1541_170k", "commodore_gcr_1541_cpm_170k"),
    ".d71": ("commodore_gcr_1571_341k",),
    ".d81": ("commodore_mfm_1581_800k",),
    ".adf": ("amiga_mfm_880k",),
    ".imd": (
        "dec_fm_rx01_250k",
        "tandy_mfm_cpmplus_156k",
        "tandy_mfm_cpmplus_hxc_360k",
        "tandy_mfm_ssdd_180k",
        "tandy_mfm_ssdd_180k_s0",
        "kaypro_mfm_ssdd_40_200k",
        "osborne_mfm_ssdd_200k",
        "generic_mfm_8inch_500k",
        "ibm_displaywriter_fm_284k",
        "ibm_mfm_8inch_1200k",
        "ibm_fm_8inch_284k",
        "ibm_mfm_1200k",
        "ibm_mfm_720k",
        "ibm_mfm_360k",
        "ibm_mfm_180k",
        "ibm_mfm_1440k",
    ),
    ".dsk": (
        "apple2_gcr_nofs_140_140k",
        "tandy_mfm_cpmplus_156k",
        "tandy_mfm_ssdd_180k",
        "tandy_mfm_ssdd_180k_s0",
    ),
    ".dmk": (
        "tandy_mfm_cpmplus_156k",
        "tandy_mfm_ssdd_180k",
        "tandy_mfm_ssdd_180k_s0",
    ),
    ".img": (
        "apple2_gcr_nofs_140_140k",
        "dec_fm_rx01_250k",
        "tandy_mfm_ssdd_180k",
        "kaypro_mfm_ssdd_40_200k",
        "osborne_mfm_ssdd_200k",
        "generic_mfm_8inch_500k",
        "generic_fm_8inch_cpm_256k",
        "dec_dec_rx02_rx02_250k",
        "ibm_displaywriter_fm_284k",
        "ibm_mfm_8inch_1200k",
        "commodore_mfm_1581_800k",
        "amiga_mfm_880k",
        "ibm_mfm_1440k",
        "ibm_mfm_720k",
        "ibm_mfm_180k",
        "ibm_mfm_360k",
        "ibm_mfm_1200k",
    ),
}

SECTOR_SIZE_TO_CODE = {128: 0, 256: 1, 512: 2, 1024: 3, 2048: 4, 4096: 5}

# Last-resort hints when filesystem probes fail but layout strongly suggests one.
LAYOUT_FILESYSTEM_HINTS: dict[str, str] = {
    "apple2_gcr_nofs_140_140k": "prodos",
    "generic_mfm_8inch_500k": "rt11",
    "dec_dec_rx02_rx02_250k": "rt11",
    "ibm_displaywriter_fm_284k": "displaywriter",
    "commodore_gcr_1541_cpm_170k": "cpm",
    "commodore_gcr_1571_341k": "cbm_dos",
    "commodore_mfm_1581_800k": "cbm_dos",
    "amiga_mfm_880k": "amiga",
}

FAST_SCP_FAT12_LAYOUTS = {
    "ibm_mfm_180k",
    "ibm_mfm_360k",
    "ibm_mfm_720k",
    "ibm_mfm_1200k",
    "ibm_mfm_1440k",
    "ibm_mfm_2880k",
    "ibm_mfm_8inch_1200k",
}


def _flat_layout_candidates(extension: str) -> list[LayoutDescriptor]:
    order: list[LayoutDescriptor] = []
    seen: set[str] = set()
    preferred_ids = FLAT_LAYOUT_PREFERENCES.get(extension, ())
    for lid in preferred_ids:
        layout = registry.layout.get(lid)
        if layout:
            order.append(layout)
            seen.add(layout.layout_id)
    if extension in {".woz", ".po", ".do", ".nib"}:
        extras = [layout for layout in registry.layout.values() if layout.layout_id.startswith("apple2_")]
    elif extension in {".d64", ".d71"}:
        extras = [layout for layout in registry.layout.values() if layout.encoding == "gcr" and layout.sector_size == 256]
    elif extension in {".d81", ".adf"}:
        extras = [layout for layout in registry.layout.values() if layout.encoding == "mfm" and layout.sector_size == 512]
    elif extension == ".img":
        extras = [
            layout
            for layout in registry.layout.values()
            if layout.encoding in {"mfm", "fm", "gcr", "dec_rx02"} and layout.sector_size in {128, 256, 512, 1024}
        ]
    elif extension in {".imd", ".dsk", ".dmk"}:
        extras = [
            layout
            for layout in registry.layout.values()
            if layout.encoding in {"mfm", "fm"} and layout.sector_size in {128, 256, 512, 1024}
        ]
    else:
        extras = []
    for layout in extras:
        if layout.layout_id not in seen:
            order.append(layout)
            seen.add(layout.layout_id)
    return order


def _default_bytes_per_sector(extension: str) -> int:
    if extension in {".d64", ".d71"}:
        return 256
    if extension in {".imd", ".dsk", ".dmk"}:
        return 512
    return 512


def _track_in_range(range_expr: str, track: int) -> bool:
    if "-" in range_expr:
        start, end = range_expr.split("-", 1)
        try:
            return int(start) <= track <= int(end)
        except ValueError:
            return False
    try:
        return track == int(range_expr)
    except ValueError:
        return False


def _parse_write_sector_spec(write_sector: str) -> tuple[int, Optional[int], int, bytes]:
    """Parse ``T:S:HEX`` or ``T:H:S:HEX`` patch input."""

    parts = write_sector.split(":")
    if len(parts) == 3:
        track_str, sector_str, payload_hex = parts
        head_str: Optional[str] = None
    elif len(parts) == 4:
        track_str, head_str, sector_str, payload_hex = parts
    else:
        raise ValueError("Expected T:S:HEX or T:H:S:HEX")
    if not track_str or not sector_str or not payload_hex or head_str == "":
        raise ValueError("Expected T:S:HEX or T:H:S:HEX")
    head = int(head_str) if head_str is not None else None
    return int(track_str), head, int(sector_str), bytes.fromhex(payload_hex)


def _apply_sector_patch(
    track_data: list[TrackSectors], track_idx: int, head_idx: Optional[int], sector_idx: int, payload: bytes
) -> None:
    """Apply a full-sector payload to one matching decoded sector."""

    matches: list[Sector] = []
    for ts in track_data:
        if ts.track != track_idx:
            continue
        if head_idx is not None and ts.head != head_idx:
            continue
        for sec in ts.sectors:
            if sec.sector_id != sector_idx:
                continue
            matches.append(sec)
    if not matches:
        target = f"{track_idx}:{head_idx}:{sector_idx}" if head_idx is not None else f"{track_idx}:{sector_idx}"
        raise FluxctlError(f"Sector {target} not found in decoded image")
    if len(matches) > 1:
        raise FluxctlError(
            f"Sector {track_idx}:{sector_idx} matches multiple heads; use T:H:S:HEX to choose one"
        )

    sector = matches[0]
    expected_size = sector.size
    if len(payload) != expected_size:
        target = f"{track_idx}:{head_idx}:{sector_idx}" if head_idx is not None else f"{track_idx}:{sector_idx}"
        raise FluxctlError(
            f"Patch payload is {len(payload)} bytes; sector {target} requires {expected_size} bytes"
        )
    sector.data = payload
    sector.crc_ok = True
    sector.confidence = 1.0


def _expected_bytes_for_layout(layout: LayoutDescriptor) -> int:
    if layout.tracks <= 0 or layout.sides <= 0 or layout.sector_size <= 0:
        return 0
    sectors_per_cylinder = list(layout.track_sectors) if layout.track_sectors else [layout.sectors_per_track] * layout.tracks
    if len(sectors_per_cylinder) < layout.tracks:
        sectors_per_cylinder.extend([layout.sectors_per_track] * (layout.tracks - len(sectors_per_cylinder)))

    def track_head_bytes(cylinder: int, head: int, sectors: int) -> int:
        override = None
        if layout.track_overrides:
            for candidate in layout.track_overrides:
                if _track_in_range(candidate.get("track_range", ""), cylinder) and (
                    candidate.get("head") is None or candidate.get("head") == head
                ):
                    override = candidate
                    break
        if override and "sector_sizes" in override:
            return sum(int(size) for size in override["sector_sizes"])
        if override:
            sector_size = int(override.get("sector_size", layout.sector_size))
            sector_count = int(override.get("sectors_per_track", sectors))
            return sector_count * sector_size
        if layout.sector_sizes:
            return sum(int(size) for size in layout.sector_sizes)
        return int(sectors) * int(layout.sector_size)

    return sum(
        track_head_bytes(cylinder, head, sectors)
        for cylinder, sectors in enumerate(sectors_per_cylinder)
        for head in range(layout.sides)
    )


def _prefix_track_count_for_size(layout: LayoutDescriptor, data_len: int) -> Optional[int]:
    if layout.sides != 1 or not layout.track_sectors or layout.sector_size <= 0:
        return None
    offset = 0
    for idx, sectors in enumerate(layout.track_sectors):
        offset += sectors * layout.sector_size
        if offset == data_len:
            return idx + 1
        if offset > data_len:
            return None
    return None


def _layout_data_distance(layout: LayoutDescriptor, data_len: int) -> int:
    if _prefix_track_count_for_size(layout, data_len) is not None:
        return 0
    expected_bytes = _expected_bytes_for_layout(layout)
    return abs(expected_bytes - data_len)


def _flat_layout_filesystem_penalty(layout: LayoutDescriptor, filesystem_name: Optional[str]) -> int:
    modelled_cpm_layouts = {
        "generic_fm_8inch_cpm_256k",
        "kaypro_mfm_ssdd_40_200k",
        "osborne_mfm_ssdd_200k",
        "tandy_mfm_ssdd_180k",
        "tandy_mfm_cpmplus_156k",
    }
    if layout.layout_id in modelled_cpm_layouts and filesystem_name != "cpm":
        return 3
    if filesystem_name == "c64_cpm_2_2":
        return 0 if layout.layout_id == "commodore_gcr_1541_cpm_170k" else 1
    if filesystem_name == "c128_cpm_3_0":
        return 0 if layout.layout_id.startswith("commodore_mfm_1571_cpm_") else 2
    if filesystem_name == "cpm":
        return 0 if layout.layout_id in modelled_cpm_layouts else 1
    if filesystem_name == "rt11":
        return 0 if layout.layout_id in {"generic_mfm_8inch_500k", "dec_dec_rx02_rx02_250k"} else 3
    if filesystem_name == "rt11_interchange":
        return 0 if layout.layout_id == "dec_fm_rx01_250k" else 3
    if filesystem_name == "cbm_dos" and layout.layout_id == "commodore_gcr_1541_cpm_170k":
        return 1
    return 0


def _cpm_layout_marker_score(data: bytes, layout_id: str) -> int:
    markers = {
        "kaypro_mfm_ssdd_40_200k": b"FLUXCTL CPM KAYPROII",
        "osborne_mfm_ssdd_200k": b"FLUXCTL CPM OSBORNE",
        "tandy_mfm_ssdd_180k": b"FLUXCTL CPM TANDY4",
    }
    active_markers = [marker for marker in markers.values() if data.startswith(marker)]
    if not active_markers:
        return 0
    return 1 if data.startswith(markers.get(layout_id, b"\x00")) else -1


def _sectors_from_blob(
    layout: LayoutDescriptor,
    data: bytes,
    *,
    allow_pad: bool = False,
    allow_prefix: bool = False,
) -> Optional[list[TrackSectors]]:
    if layout.sector_size <= 0:
        return None
    size_code = SECTOR_SIZE_TO_CODE.get(layout.sector_size)
    if size_code is None:
        return None
    if layout.tracks <= 0 or layout.sides <= 0:
        return None
    sectors_per_cylinder = list(layout.track_sectors) if layout.track_sectors else [layout.sectors_per_track] * layout.tracks
    if len(sectors_per_cylinder) < layout.tracks:
        sectors_per_cylinder.extend([layout.sectors_per_track] * (layout.tracks - len(sectors_per_cylinder)))
    expected_bytes = _expected_bytes_for_layout(layout)
    if len(data) < expected_bytes:
        prefix_tracks = _prefix_track_count_for_size(layout, len(data)) if allow_prefix else None
        if prefix_tracks is not None:
            sectors_per_cylinder = sectors_per_cylinder[:prefix_tracks]
            expected_bytes = sum(sectors_per_cylinder) * layout.sides * layout.sector_size
        elif not allow_pad:
            return None
        else:
            missing_ratio = 1 - (len(data) / expected_bytes)
            if missing_ratio > 0.6:
                return None
            data = data.ljust(expected_bytes, b"\x00")
    if len(data) != expected_bytes:
        if not allow_pad:
            return None
        data = data[:expected_bytes]
    tracks: list[TrackSectors] = []
    offset = 0
    effective_tracks = len(sectors_per_cylinder)
    side_blocked_flat = layout.layout_id == "commodore_gcr_1571_341k"
    order = (
        ((cylinder, head) for head in range(layout.sides) for cylinder in range(effective_tracks))
        if side_blocked_flat
        else ((cylinder, head) for cylinder in range(effective_tracks) for head in range(layout.sides))
    )
    for cylinder, head in order:
        sectors_on_track = sectors_per_cylinder[cylinder]
        sector_size = layout.sector_size
        sector_sizes = list(layout.sector_sizes) if layout.sector_sizes else None
        if layout.track_overrides:
            for override in layout.track_overrides:
                if _track_in_range(override.get("track_range", ""), cylinder) and (
                    override.get("head") is None or override.get("head") == head
                ):
                    sector_size = override.get("sector_size", sector_size)
                    sectors_on_track = override.get("sectors_per_track", sectors_on_track)
                    sector_sizes = list(override.get("sector_sizes", [])) or None
                    break
        sectors: list[Sector] = []
        sector_base = int(layout.id_rules.get("sector_number_base", 1))
        sizes_this_track = sector_sizes or [int(sector_size)] * int(sectors_on_track)
        for sector_offset, sector_size_this in enumerate(sizes_this_track):
            size_code = SECTOR_SIZE_TO_CODE.get(int(sector_size_this))
            if size_code is None:
                return None
            chunk = data[offset : offset + int(sector_size_this)]
            if len(chunk) < int(sector_size_this):
                return None
            sectors.append(
                Sector(
                    cylinder=cylinder,
                    head=head,
                    sector_id=sector_base + sector_offset,
                    size_code=size_code,
                    data=chunk,
                    crc_ok=True,
                    confidence=1.0,
                    deleted=False,
                )
            )
            offset += int(sector_size_this)
        tracks.append(TrackSectors(track=cylinder, head=head, sectors=sectors))
    return tracks


def _filesystem_name_for_image(image, path: Optional[Path] = None) -> Optional[str]:
    detection = _filesystem_detection_for_image(image, path)
    if detection.primary:
        return detection.primary
    layout_id = getattr(getattr(image, "layout", None), "layout_id", None)
    return LAYOUT_FILESYSTEM_HINTS.get(layout_id) if layout_id else None


def _filesystem_evidence_for_image(image, path: Optional[Path] = None) -> tuple[Optional[str], list[str]]:
    detection = _filesystem_detection_for_image(image, path)
    if detection.primary:
        return _filesystem_probe_payload(detection)
    layout_id = getattr(getattr(image, "layout", None), "layout_id", None)
    layout_hint = LAYOUT_FILESYSTEM_HINTS.get(layout_id) if layout_id else None
    evidence = detection.evidence
    if layout_hint:
        evidence = [*evidence, f"filesystem_layout_hint={layout_hint}"]
    return layout_hint, evidence


def _image_from_tracks(
    tracks: list[TrackSectors],
    geom,
    layout: Optional[LayoutDescriptor] = None,
) -> TrackSectorImage:
    image = TrackSectorImage(tracks, bytes_per_sector=getattr(geom, "sector_size", None))
    sector_ids = [sec.sector_id for ts in tracks for sec in ts.sectors]
    inferred_base = min(sector_ids) if sector_ids else 1
    sector_base = int(layout.id_rules.get("sector_number_base", inferred_base)) if layout else inferred_base
    sectors_per_track = layout.sectors_per_track if layout else (geom.spt or geom.tracks)
    image.set_geometry(sectors_per_track, geom.heads, sector_base)
    if layout:
        image.layout = layout
    return image


def _flatten_track_container(tracks: list[TrackSectors], geom, sector_base: int) -> bytes:
    data = bytearray(geom.tracks * geom.heads * geom.spt * geom.sector_size)
    for ts in tracks:
        for sec in ts.sectors:
            sector_offset = sec.sector_id - sector_base
            if sector_offset < 0 or sector_offset >= geom.spt:
                continue
            off = ((ts.track * geom.heads + ts.head) * geom.spt + sector_offset) * geom.sector_size
            payload = (sec.data + b"\x00" * geom.sector_size)[: geom.sector_size]
            data[off : off + geom.sector_size] = payload
    return bytes(data)


def _track_sector_profile(tracks: list[TrackSectors]) -> tuple[dict[int, int], dict[int, int], int]:
    counts: dict[int, int] = {}
    sizes: dict[int, int] = {}
    min_sector_id = 1
    sector_ids = [sec.sector_id for ts in tracks for sec in ts.sectors]
    if sector_ids:
        min_sector_id = min(sector_ids)
    for ts in tracks:
        if ts.head != 0:
            continue
        counts[ts.track] = len(ts.sectors)
        track_sizes = {len(sec.data) for sec in ts.sectors if sec.data}
        if track_sizes:
            sizes[ts.track] = max(track_sizes)
    return counts, sizes, min_sector_id


def _tandy_candidate_for_tracks(
    path: Path,
    tracks: list[TrackSectors],
    geom,
    evidence: list[str],
) -> Optional[CandidateFormat]:
    counts, sizes, min_sector_id = _track_sector_profile(tracks)
    if geom.tracks != 40 or geom.heads != 1:
        return None

    layout_id: Optional[str] = None
    if counts.get(0) == 18 and sizes.get(0) == 256:
        data_track_counts = {counts.get(track) for track in range(1, 40) if track in counts}
        data_track_sizes = {sizes.get(track) for track in range(1, 40) if track in sizes}
        if data_track_counts == {8} and data_track_sizes == {512}:
            layout_id = "tandy_mfm_cpmplus_156k"
        elif data_track_counts == {18} and data_track_sizes == {512}:
            layout_id = "tandy_mfm_cpmplus_hxc_360k"
    if layout_id is None and geom.spt == 18 and geom.sector_size == 256:
        layout_id = "tandy_mfm_ssdd_180k_s0" if min_sector_id == 0 else "tandy_mfm_ssdd_180k"
    if layout_id is None:
        return None

    layout = registry.layout.get(layout_id)
    if layout is None:
        return None
    image_obj = _image_from_tracks(tracks, geom, layout)
    fs_name, fs_evidence = _filesystem_evidence_for_image(image_obj, path)
    layout_evidence = evidence + [f"layout={layout_id}"]
    if fs_name:
        layout_evidence.append(f"filesystem={fs_name}")
    layout_evidence.extend(fs_evidence)
    return CandidateFormat(
        candidate_id=layout_id,
        encoding=layout.encoding,
        layout_id=layout_id,
        filesystem=fs_name,
        score=1.0,
        evidence=layout_evidence,
    )


def _probe_flat_image(path: Path) -> list[CandidateFormat]:
    ext = path.suffix.lower()
    imd_tracks = None
    imd_geom = None
    imd_image = None
    imd_filesystem_name = None
    if ext in {".woz", ".po", ".do", ".nib"}:
        layout = registry.layout.get("apple2_gcr_nofs_140_140k")
        if layout is None:
            return []
        tracks, metadata = load_apple2_tracks(path)
        image_obj = Apple2SectorImage(tracks, layout)
        fs_name, fs_evidence = _filesystem_evidence_for_image(image_obj, path)
        good = sum(1 for ts in tracks for sector in ts.sectors if sector.data and sector.crc_ok)
        total = layout.tracks * layout.sectors_per_track
        return [
            CandidateFormat(
                candidate_id=layout.layout_id,
                encoding=layout.encoding,
                layout_id=layout.layout_id,
                filesystem=fs_name,
                score=good / total if total else 0.0,
                evidence=[
                    f"format={metadata.get('format', ext.lstrip('.'))}",
                    f"size={path.stat().st_size}",
                    f"decoded_sectors={good}/{total}",
                    f"layout={layout.layout_id}",
                    *fs_evidence,
                ],
            )
        ]
    if ext == ".dsk" and path.stat().st_size == 143_360:
        layout = registry.layout.get("apple2_gcr_nofs_140_140k")
        if layout is not None:
            for order_name, order in (("po", APPLE2_PO_ORDER), ("do", APPLE2_DO_ORDER)):
                tracks = tracks_from_apple2_sector_image(path.read_bytes(), order)
                image_obj = Apple2SectorImage(tracks, layout)
                fs_name, fs_evidence = _filesystem_evidence_for_image(image_obj, path)
                if fs_name in {"prodos", "apple_dos_3_3"}:
                    return [
                        CandidateFormat(
                            candidate_id=layout.layout_id,
                            encoding=layout.encoding,
                            layout_id=layout.layout_id,
                            filesystem=fs_name,
                            score=1.0,
                            evidence=[
                                "format=apple2_dsk",
                                f"size={path.stat().st_size}",
                                f"apple2_sector_order={order_name}",
                                *fs_evidence,
                            ],
                        )
                    ]
    if ext == ".imd":
        imd_tracks, imd_geom, imd_meta = load_imd_image(path)
        imd_image = TrackSectorImage(imd_tracks)
        if imd_geom and imd_geom.spt and imd_geom.heads:
            sector_ids = [sec.sector_id for ts in imd_tracks for sec in ts.sectors]
            sector_base = min(sector_ids) if sector_ids else 1
            imd_image.set_geometry(imd_geom.spt, imd_geom.heads, sector_base)
        imd_total_bytes = sum(len(sec.data) for ts in imd_tracks for sec in ts.sectors)
        data = bytearray(imd_geom.tracks * imd_geom.heads * imd_geom.spt * imd_geom.sector_size)
        for ts in imd_tracks:
            for sec in ts.sectors:
                sector_offset = sec.sector_id - sector_base
                if sector_offset < 0 or sector_offset >= imd_geom.spt:
                    continue
                off = ((ts.track * imd_geom.heads + ts.head) * imd_geom.spt + sector_offset) * imd_geom.sector_size
                # Ensure we never change the bytearray length even when sector sizes vary.
                payload = (sec.data + b"\x00" * imd_geom.sector_size)[: imd_geom.sector_size]
                data[off : off + imd_geom.sector_size] = payload
        size = imd_total_bytes
        evidence = [
            "format=imd",
            f"size={size}",
            f"geom={imd_geom.tracks}x{imd_geom.heads}x{imd_geom.spt}x{imd_geom.sector_size}",
        ]
        imd_filesystem_name = _filesystem_name_for_image(imd_image, path) if imd_image else None
        data_bytes = bytes(data)
    elif ext in {".dsk", ".dmk"}:
        imd_tracks, imd_geom, imd_meta = load_trs80_image(path)
        sector_ids = [sec.sector_id for ts in imd_tracks for sec in ts.sectors]
        sector_base = min(sector_ids) if sector_ids else 1
        imd_image = _image_from_tracks(imd_tracks, imd_geom)
        imd_total_bytes = sum(len(sec.data) for ts in imd_tracks for sec in ts.sectors)
        evidence = [
            f"format={imd_meta.get('format', ext.lstrip('.'))}",
            f"size={imd_total_bytes}",
            f"geom={imd_geom.tracks}x{imd_geom.heads}x{imd_geom.spt}x{imd_geom.sector_size}",
        ]
        tandy_candidate = _tandy_candidate_for_tracks(path, imd_tracks, imd_geom, evidence)
        if tandy_candidate:
            return [tandy_candidate]
        imd_filesystem_name = _filesystem_name_for_image(imd_image, path) if imd_image else None
        data_bytes = _flatten_track_container(imd_tracks, imd_geom, sector_base)
    else:
        data_bytes = path.read_bytes()
        size = len(data_bytes)
        evidence = [f"size={size}"]

    if ext == ".img" and len(data_bytes) == 143360:
        layout = registry.layout.get("apple2_gcr_nofs_140_140k")
        if layout is not None:
            for order_name, order in (("po", APPLE2_PO_ORDER), ("do", APPLE2_DO_ORDER)):
                try:
                    tracks = tracks_from_apple2_sector_image(data_bytes, order)
                    image_obj = Apple2SectorImage(tracks, layout)
                    fs_name, fs_evidence = _filesystem_evidence_for_image(image_obj, path)
                except Exception:
                    continue
                if fs_name in {"prodos", "apple_dos_3_3"}:
                    return [
                        CandidateFormat(
                            candidate_id=layout.layout_id,
                            encoding=layout.encoding,
                            layout_id=layout.layout_id,
                            filesystem=fs_name,
                            score=1.0,
                            evidence=evidence + [f"apple2_sector_order={order_name}", *fs_evidence],
                        )
                    ]

    # RX02 media may be a complete 77-track single-sided image (512512 bytes)
    # or a truncated/placeholder capture produced by tools that omit empty
    # sectors.  Only the complete physical geometry is unambiguous here.
    wang_catalog_block = int.from_bytes(data_bytes[22:24], "little") if len(data_bytes) >= 24 else 0
    wang_catalog_base = 0
    wang_catalog_unit = ""
    for unit, multiplier in (
        ("allocation_block", 1024),
        ("sector", 256),
        ("double_allocation_block", 2048),
    ):
        candidate_base = wang_catalog_block * multiplier
        if (
            len(data_bytes) == 77 * 16 * 256
            and 0 < candidate_base <= len(data_bytes) - 48
            and data_bytes[candidate_base + 1 : candidate_base + 8] == b"Catalog"
        ):
            wang_catalog_base = candidate_base
            wang_catalog_unit = unit
            break
    wang_catalog = (
        len(data_bytes) == 77 * 16 * 256
        and 0 < wang_catalog_base <= len(data_bytes) - 48
        and data_bytes[wang_catalog_base + 1 : wang_catalog_base + 8] == b"Catalog"
    )
    if ext == ".img" and wang_catalog:
        layout = registry.layout.get("wang_ois_hs32_fm_315k")
        if layout is not None:
            track_data = _sectors_from_blob(layout, data_bytes)
            if track_data is not None:
                image_obj = TrackSectorImage(track_data, bytes_per_sector=layout.sector_size)
                image_obj.layout = layout
                _apply_layout_geometry(image_obj, layout)
                fs_name, fs_evidence = _filesystem_evidence_for_image(image_obj, path)
                return [
                    CandidateFormat(
                        candidate_id=layout.layout_id,
                        encoding=layout.encoding,
                        layout_id=layout.layout_id,
                        filesystem=fs_name,
                        score=1.0,
                        evidence=evidence
                        + [
                            "wang_geometry=77x16x256",
                            "physical_hard_sector_count=32",
                            f"wang_label={data_bytes[:8].rstrip(bytes([0])).decode('ascii', errors='replace')}",
                            f"wang_catalog_block={wang_catalog_block}",
                            f"wang_catalog_pointer_unit={wang_catalog_unit}",
                            f"layout={layout.layout_id}",
                        ]
                        + fs_evidence,
                    )
                ]

    if ext == ".img" and len(data_bytes) in {77 * 26 * 256, 255872, 511872}:
        layout = registry.layout.get("dec_dec_rx02_rx02_250k")
        if layout is not None:
            track_data = _sectors_from_blob(layout, data_bytes) if len(data_bytes) == 77 * 26 * 256 else None
            if track_data is not None:
                image_obj = TrackSectorImage(track_data, bytes_per_sector=layout.sector_size)
                image_obj.layout = layout
                _apply_layout_geometry(image_obj, layout)
                fs_name, fs_evidence = _filesystem_evidence_for_image(image_obj, path)
                return [
                    CandidateFormat(
                        candidate_id=layout.layout_id,
                        encoding=layout.encoding,
                        layout_id=layout.layout_id,
                        filesystem=fs_name or LAYOUT_FILESYSTEM_HINTS.get(layout.layout_id),
                        score=1.0,
                        evidence=evidence + [f"layout={layout.layout_id}", *fs_evidence],
                    )
                ]
            if len(data_bytes) in {255872, 511872}:
                return [
                    CandidateFormat(
                        candidate_id=layout.layout_id,
                        encoding=layout.encoding,
                        layout_id=layout.layout_id,
                        filesystem="rt11",
                        score=0.98,
                        evidence=evidence + [f"layout={layout.layout_id}", "rx02_truncated_flat_geometry=1"],
                    )
                ]

    if ext == ".img" and "displaywriter" in path.stem.lower():
        lid = "ibm_displaywriter_fm_284k"
        layout = registry.layout.get(lid)
        fs_name = LAYOUT_FILESYSTEM_HINTS.get(lid)
        return [
            CandidateFormat(
                candidate_id=lid,
                encoding=layout.encoding if layout else None,
                layout_id=lid,
                filesystem=fs_name,
                score=1.0,
                evidence=evidence + [f"layout={lid}"] + ([f"filesystem={fs_name}"] if fs_name else []),
            )
        ]

    ext_for_layouts = ext
    if ext == ".imd" and imd_geom:
        tandy_candidate = _tandy_candidate_for_tracks(path, imd_tracks or [], imd_geom, evidence)
        if tandy_candidate:
            return [tandy_candidate]
        if imd_filesystem_name == "displaywriter":
            lid = "ibm_displaywriter_fm_284k"
            layout = registry.layout.get(lid)
            return [
                CandidateFormat(
                    candidate_id=lid,
                    encoding=layout.encoding if layout else "fm",
                    layout_id=lid,
                    filesystem=imd_filesystem_name,
                    score=1.0,
                    evidence=evidence + [f"layout={lid}", "filesystem=displaywriter"],
                )
            ]
        if imd_filesystem_name == "rt11_interchange":
            lid = "dec_fm_rx01_250k"
            layout = registry.layout.get(lid)
            return [
                CandidateFormat(
                    candidate_id=lid,
                    encoding=layout.encoding if layout else "fm",
                    layout_id=lid,
                    filesystem=imd_filesystem_name,
                    score=1.0,
                    evidence=evidence + [f"layout={lid}", "filesystem=rt11_interchange"],
                )
            ]
        # Fast-path common IMD geometries.
        if imd_geom.sector_size == 128 and imd_geom.spt == 26 and imd_geom.tracks >= 77:
            lid = "generic_mfm_8inch_500k"
            layout = registry.layout.get(lid)
            return [
                CandidateFormat(
                    candidate_id=lid,
                    encoding=layout.encoding if layout else "mfm",
                    layout_id=lid,
                    filesystem="rt11",
                    score=1.0,
                    evidence=evidence + [f"layout={lid}", "filesystem=rt11"],
                )
            ]
        if imd_geom.sector_size == 512 and imd_geom.spt in {15, 16} and imd_geom.heads == 2 and imd_geom.tracks >= 77:
            lid = "ibm_mfm_8inch_1200k" if registry.layout.get("ibm_mfm_8inch_1200k") else "ibm_mfm_1200k"
            layout = registry.layout.get(lid)
            fs_name, fs_evidence = _filesystem_evidence_for_image(imd_image, path) if imd_image else (None, [])
            return [
                CandidateFormat(
                    candidate_id=lid,
                    encoding=layout.encoding if layout else "mfm",
                    layout_id=lid,
                    filesystem=fs_name,
                    score=1.0,
                    evidence=evidence + [f"layout={lid}"] + ([f"filesystem={fs_name}"] if fs_name else []) + fs_evidence,
                )
            ]
    def _imd_penalty(layout: LayoutDescriptor) -> int:
        if not imd_geom:
            return 0
        penalty = 0
        penalty += abs(layout.sectors_per_track - imd_geom.spt)
        penalty += abs(layout.sides - imd_geom.heads)
        penalty += abs(layout.sector_size - imd_geom.sector_size) // 16
        if imd_tracks:
            mismatch = sum(1 for ts in imd_tracks for sec in ts.sectors if len(sec.data) != layout.sector_size)
            penalty += mismatch
        return penalty

    best_candidate: Optional[CandidateFormat] = None
    best_score: Optional[tuple[int, int, int]] = None
    for layout in _flat_layout_candidates(ext_for_layouts):
        expected_bytes = _expected_bytes_for_layout(layout)
        if expected_bytes <= 0:
            continue
        track_data = _sectors_from_blob(
            layout,
            data_bytes,
            allow_pad=ext_for_layouts in {".imd", ".img"},
            allow_prefix=ext_for_layouts in {".d64"},
        )
        if track_data is None:
            continue
        image_obj = TrackSectorImage(track_data, bytes_per_sector=layout.sector_size)
        image_obj.layout = layout
        _apply_layout_geometry(image_obj, layout)
        filesystem_name, filesystem_evidence = _filesystem_evidence_for_image(image_obj, path)
        layout_evidence = evidence + [f"layout={layout.layout_id}"]
        if filesystem_name:
            layout_evidence.append(f"filesystem={filesystem_name}")
        layout_evidence.extend(filesystem_evidence)
        distance = _layout_data_distance(layout, len(data_bytes))
        candidate = CandidateFormat(
            candidate_id=layout.layout_id,
            encoding=layout.encoding,
            layout_id=layout.layout_id,
            filesystem=filesystem_name,
            score=1.0,
            evidence=layout_evidence,
        )
        filesystem_penalty = _flat_layout_filesystem_penalty(layout, filesystem_name)
        cpm_directory_penalty = 0
        if filesystem_name == "cpm":
            cpm_marker_score = _cpm_layout_marker_score(data_bytes, layout.layout_id)
            cpm_directory_score = cpm_directory_score_for_layout(image_obj, layout.layout_id)
            cpm_directory_penalty = 0 if cpm_directory_score >= 2 or cpm_marker_score > 0 else 3
            if cpm_directory_score:
                candidate.evidence.append(f"cpm_layout_directory_entries={cpm_directory_score}")
            if cpm_marker_score > 0:
                candidate.evidence.append("cpm_blank_marker_match=1")
            elif cpm_marker_score < 0:
                candidate.evidence.append("cpm_blank_marker_mismatch=1")
        geometry_penalty = _imd_penalty(layout)
        score = (filesystem_penalty, cpm_directory_penalty, geometry_penalty, distance)
        if best_score is None or score < best_score:
            best_candidate = candidate
            best_score = score

    if best_candidate:
        if imd_filesystem_name and best_candidate.filesystem is None:
            best_candidate.filesystem = imd_filesystem_name
            best_candidate.evidence = [entry for entry in best_candidate.evidence if not entry.startswith("filesystem=")]
            best_candidate.evidence.append(f"filesystem={imd_filesystem_name}")
        return [best_candidate]

    bytes_per_sector = imd_geom.sector_size if imd_geom else _default_bytes_per_sector(ext_for_layouts)
    fallback_image = (
        TrackSectorImage(imd_tracks, bytes_per_sector=bytes_per_sector)
        if imd_tracks
        else RawSectorImage(data_bytes, bytes_per_sector=bytes_per_sector)
    )
    if imd_geom and imd_geom.spt and imd_geom.heads:
        fallback_image.set_geometry(imd_geom.spt, imd_geom.heads)

    fallback_fs, fallback_fs_evidence = _filesystem_evidence_for_image(fallback_image, path)
    fallback_evidence = evidence + ([f"filesystem={fallback_fs}"] if fallback_fs else [])
    fallback_evidence.extend(fallback_fs_evidence)
    fallback_candidate = CandidateFormat(
        candidate_id=f"flat_{ext_for_layouts.lstrip('.')}",
        encoding=None,
        layout_id=None,
        filesystem=fallback_fs,
        score=0.0,
        evidence=fallback_evidence,
    )
    return [fallback_candidate]


def _is_lossy(track_data: Optional[list[TrackSectors]], exporter_metadata: dict) -> bool:
    if not track_data:
        return bool(exporter_metadata.get("padded_missing"))
    missing = any(ts.missing or ts.weak for ts in track_data)
    sector_health = any((not sec.crc_ok) or (not sec.data) for ts in track_data for sec in ts.sectors)
    return missing or sector_health or exporter_metadata.get("padded_missing", False)


@dataclass(slots=True)
class ConvertPayload:
    payload: bytes
    layout: Optional[LayoutDescriptor]
    encoding: str
    track_data: Optional[list[TrackSectors]]
    exporter_name: str
    exporter_version: str
    exporter_metadata: dict

    @property
    def layout_id(self) -> str:
        return self.layout.layout_id if self.layout else ""


def _prepare_convert_payload(path: Path, to: str, layout: Optional[str], encoding: str) -> ConvertPayload:
    load_builtin_decoders()
    load_builtin_layouts()
    load_builtin_exporters()
    layout_desc = ensure_layout_loaded(layout) if layout else None
    decoder_used = layout_desc.encoding if layout_desc else encoding
    if layout_desc is None and path.suffix.lower() == ".d81":
        layout_desc = ensure_layout_loaded("commodore_mfm_1581_800k")
        decoder_used = layout_desc.encoding
    track_data: Optional[list[TrackSectors]] = None
    track_nibbles: list[TrackNibbles] = []

    if path.suffix.lower() == ".scp":
        if layout_desc is None:
            scp_image = parse_scp(path)
            encoding_candidate = detect_encoding(scp_image, path=path)
            if encoding_candidate is None:
                raise FluxDecodeError("Unable to auto-detect SCP encoding; pass --layout and --encoding")
            layout_candidate = detect_layout(scp_image, encoding_candidate.encoding, path)
            if layout_candidate is None:
                raise FluxDecodeError("Unable to auto-detect SCP layout; pass --layout explicitly")
            layout_desc = layout_candidate.layout
            decoder_used = layout_desc.encoding
            typer.echo(f"Auto-detected layout {layout_desc.layout_id} ({decoder_used})")
        if layout_desc.layout_id.startswith("amiga_"):
            track_data = _decode_amiga_tracks(path, layout_desc)
        else:
            decode_result = _decode_tracks(
                path, layout_desc.layout_id, encoding=decoder_used, capture_nibbles=to == "g64"
            )
            if isinstance(decode_result, tuple):
                track_data, track_nibbles = decode_result
            else:
                track_data = decode_result
        image_obj = (
            Apple2SectorImage(track_data, layout_desc)
            if layout_desc and layout_desc.layout_id.startswith("apple2_")
            else TrackSectorImage(track_data, bytes_per_sector=layout_desc.sector_size if layout_desc else None)
        )
        if track_nibbles:
            image_obj.tracks_nibbles = track_nibbles
        if layout_desc:
            image_obj.layout = layout_desc
            geometry_sectors = None
            if track_data:
                try:
                    geometry_sectors = layout_desc.expected_sectors_for_track(track_data[0].track, track_data[0].head)
                except Exception:
                    geometry_sectors = layout_desc.sectors_per_track
            image_obj.set_geometry(
                geometry_sectors or layout_desc.sectors_per_track,
                layout_desc.sides,
                int(layout_desc.id_rules.get("sector_number_base", 1)),
            )
    elif path.suffix.lower() in {".woz", ".po", ".do", ".nib"}:
        if layout_desc is None:
            layout_desc = ensure_layout_loaded("apple2_gcr_nofs_140_140k")
            decoder_used = layout_desc.encoding
        track_data, _metadata = load_apple2_tracks(path)
        image_obj = Apple2SectorImage(track_data, layout_desc)
    elif path.suffix.lower() == ".imd":
        track_data, imd_geom, _meta = load_imd_image(path)
        image_obj = _image_from_tracks(track_data, imd_geom, layout_desc)
    elif (
        path.suffix.lower() == ".dsk"
        and layout_desc is not None
        and layout_desc.layout_id.startswith("apple2_")
    ):
        track_data, _metadata = load_apple2_tracks(path)
        image_obj = Apple2SectorImage(track_data, layout_desc)
    elif path.suffix.lower() in {".dsk", ".dmk"}:
        track_data, trs_geom, _meta = load_trs80_image(path)
        image_obj = _image_from_tracks(track_data, trs_geom, layout_desc)
    elif layout_desc:
        image_obj = _prepare_image(path, layout_desc.layout_id, decoder_used)
        if isinstance(image_obj, TrackSectorImage):
            track_data = image_obj.tracks
    else:
        image_obj = RawSectorImage(path.read_bytes())

    plugin = registry.exporter.get(to)
    if plugin is None:
        raise typer.BadParameter("Unsupported exporter")

    exporter = plugin.entry
    if not exporter.supports(image_obj):
        raise ExportError(f"Exporter '{to}' does not support this image type")

    payload = exporter.export(image_obj)
    return ConvertPayload(
        payload=payload,
        layout=layout_desc,
        encoding=decoder_used,
        track_data=track_data,
        exporter_name=plugin.name,
        exporter_version=plugin.version,
        exporter_metadata=exporter.metadata(),
    )


def _exporter_suffix(exporter: str) -> str:
    return ".img" if exporter == "raw" else f".{exporter}"


def _infer_roundtrip_back_exporter(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in {".img", ".ima", ".raw", ".scp", ".imd", ".dsk", ".dmk"}:
        return "raw"
    if suffix in {".adf", ".d64", ".d71", ".g64"}:
        return suffix.lstrip(".")
    if suffix in {".po", ".do"}:
        return suffix.lstrip(".")
    if suffix == ".d81":
        return "raw"
    return "raw"


@provenance_app.command("show")
def provenance_show(path: Path = typer.Argument(..., exists=True, readable=True)) -> None:
    """Print a provenance JSON file."""

    typer.echo(path.read_text(encoding="utf-8"))


@app.command()
@_handle_cli_errors
def compare(
    a: Path = typer.Argument(..., exists=True, readable=True),
    b: Path = typer.Argument(..., exists=True, readable=True),
    layout_a: Optional[str] = typer.Option(None, "--layout-a", help="Layout ID for decoding input A (SCP only)"),
    layout_b: Optional[str] = typer.Option(None, "--layout-b", help="Layout ID for decoding input B (SCP only)"),
    encoding_a: str = typer.Option("auto", "--encoding-a", help="Encoding for input A (mfm, fm, gcr, apple2_gcr, auto for SCP)"),
    encoding_b: str = typer.Option("auto", "--encoding-b", help="Encoding for input B (mfm, fm, gcr, apple2_gcr, auto for SCP)"),
    json_out: Optional[Path] = typer.Option(None, "--json-out", help="Write compare report to JSON"),
    prov_out: Optional[Path] = typer.Option(None, "--prov-out", help="Provenance sidecar for --json-out"),
):
    """Compare two images by content; SCP inputs are decoded before comparison.

    Examples:
    fluxctl compare disk.scp disk.img --layout-a ibm_mfm_720k --json-out diff.json
    fluxctl compare before.img after.img
    """

    bytes_a, meta_a = _image_bytes_for_compare(a, layout_a, encoding_a)
    bytes_b, meta_b = _image_bytes_for_compare(b, layout_b, encoding_b)

    sha_a = hashlib.sha256(bytes_a).hexdigest()
    sha_b = hashlib.sha256(bytes_b).hexdigest()
    diff = _first_diff_offset(bytes_a, bytes_b)
    identical = diff is None and len(bytes_a) == len(bytes_b)

    report = {
        "path_a": str(a),
        "path_b": str(b),
        "len_a": len(bytes_a),
        "len_b": len(bytes_b),
        "sha256_a": sha_a,
        "sha256_b": sha_b,
        "identical": identical,
        "first_diff_offset": diff,
        "meta_a": meta_a,
        "meta_b": meta_b,
    }

    typer.echo(f"A: {a} ({len(bytes_a)} bytes) sha256={sha_a}")
    typer.echo(f"B: {b} ({len(bytes_b)} bytes) sha256={sha_b}")
    if identical:
        typer.secho("Result: MATCH (byte-identical)", fg=typer.colors.GREEN)
    else:
        typer.secho("Result: DIFFER", fg=typer.colors.YELLOW)
        if diff is not None:
            typer.echo(f"First difference at offset {diff}")

    if json_out:
        json_out.parent.mkdir(parents=True, exist_ok=True)
        json_out.write_text(json.dumps(report, indent=2), encoding="utf-8")
        prov_target = prov_out or json_out.with_suffix(json_out.suffix + ".provenance.json")
        record = ProvenanceRecord(
            tool_name="fluxctl",
            tool_version=__version__,
            operation="compare",
            input_path=a,
            input_sha256=sha256_file(a),
            output_path=json_out,
            output_sha256=ProvenanceRecord.sha256_file(json_out),
            parameters={
                "path_a": str(a),
                "path_b": str(b),
                "layout_a": layout_a or "",
                "layout_b": layout_b or "",
                "encoding_a": encoding_a,
                "encoding_b": encoding_b,
                "json_out": str(json_out),
            },
            plugins={"decoder_a": meta_a["encoding"], "decoder_b": meta_b["encoding"]},
            decoder=f"{meta_a['encoding']},{meta_b['encoding']}",
            evidence=[
                f"path_b={b}",
                f"path_b_sha256={sha256_file(b)}",
                f"decoded_sha256_a={sha_a}",
                f"decoded_sha256_b={sha_b}",
            ],
        )
        write_provenance(record, prov_target)
        typer.echo(f"Wrote compare report to {json_out}")

    raise typer.Exit(code=0 if identical else 1)


@app.command()
@_handle_cli_errors
def sectors(
    path: Path = typer.Argument(..., exists=True, readable=True),
    track: int = typer.Option(0, "--track", help="Cylinder index"),
    head: int = typer.Option(0, "--head", help="Head index"),
    encoding: str = typer.Option("mfm", "--encoding", help="Bitstream encoding (mfm, fm, gcr, apple2_gcr)"),
):
    """Decode a specific track/head and list reconstructed sectors."""

    scp = parse_scp(path)
    track_flux = next((t for t in scp.tracks if t.track == track and t.side == head), None)
    if track_flux is None:
        raise FluxctlError(f"Track {track} head {head} not found in image")
    if not track_flux.revolutions:
        raise FluxDecodeError("No revolutions captured for the selected track")

    decoder = _get_decoder(encoding)
    track_sectors = build_track_sectors_from_revolutions(
        track_flux.revolutions,
        decoder,
        cylinder=track,
        head=head,
        encoding=encoding,
        timebase_ns=scp.timebase_ns,
    )
    typer.echo(
        f"Track {track_sectors.track} head {track_sectors.head}: "
        f"{len(track_sectors.sectors)} sectors (weak={track_sectors.weak} missing={track_sectors.missing})"
    )
    for sector in sorted(track_sectors.sectors, key=lambda s: s.sector_id):
        crc_status = "ok" if sector.crc_ok else "bad"
        typer.echo(
            f"ID {sector.sector_id:02d} size={sector.size} crc={crc_status} "
            f"deleted={'yes' if sector.deleted else 'no'} conf={sector.confidence:.2f}"
        )


@app.command()
@_handle_cli_errors
def dump(
    path: Path = typer.Argument(..., exists=True, readable=True),
    layout: str = typer.Option(..., "--layout"),
    track: int = typer.Option(0),
    side: int = typer.Option(0),
    sector: int = typer.Option(1),
):
    """Dump sector data as hex."""
    track_data = _decode_tracks(path, layout)
    selected_track: Optional[TrackSectors] = None
    for ts in track_data:
        if ts.track == track and ts.side == side:
            selected_track = ts
            break
    if selected_track is None:
        raise FluxctlError(f"Track {track} side {side} not found in decoded image")
    for sec in selected_track.sectors:
        if sec.sector_id == sector:
            typer.echo(sec.data.hex())
            return
    raise FluxctlError(f"Sector {track}:{side}:{sector} not found in decoded image")


@app.command()
@_handle_cli_errors
def qc(
    path: Path = typer.Argument(..., exists=True, readable=True),
    encoding: str = typer.Option("auto", "--encoding", help="Bitstream encoding (auto, mfm, gcr)"),
    layout: Optional[str] = typer.Option(None, "--layout", help="Layout identifier for geometry hints"),
    hxcfe: Optional[Path] = typer.Option(None, "--hxcfe", help="Path to an hxcfe binary for hints."),
    json_out: Optional[Path] = typer.Option(None, "--json-out", help="Write QC results to a JSON file"),
    text_out: Optional[Path] = typer.Option(None, "--text-out", help="Write QC results to a text file"),
    prov_out: Optional[Path] = typer.Option(None, "--prov-out", help="Override provenance output path"),
):
    """Assess image quality and emit QC reports."""
    load_builtin_decoders()
    load_builtin_layouts()
    selected_encoding = encoding.lower()

    if path.suffix.lower() == ".scp":
        scp = parse_scp(path)
        hxc_hint = _maybe_hxc_hint(path, hxcfe)
        if selected_encoding == "auto":
            encoding_candidate = detect_encoding(scp, path=path, hint=hxc_hint)
            if encoding_candidate is None:
                raise FluxDecodeError("Unable to infer encoding; specify --encoding")
            selected_encoding = encoding_candidate.encoding

        decoder = _get_decoder(selected_encoding)
        layout_desc = ensure_layout_loaded(layout) if layout else None
        if layout_desc is None:
            layout_candidate = detect_layout(scp, selected_encoding, path, hint=hxc_hint)
            layout_desc = layout_candidate.layout if layout_candidate else None
        track_step = infer_track_step([track.track for track in scp.tracks])
        report = build_qc_report(scp, decoder, layout=layout_desc, track_step=track_step)
    else:
        # Flat image QC path.
        layout_desc = ensure_layout_loaded(layout) if layout else None
        image_obj = _prepare_image(path, layout_desc.layout_id if layout_desc else None, selected_encoding)
        # If no layout given, probe to pick one and rebuild image if possible.
        if layout_desc is None:
            candidates = _probe_flat_image(path)
            if not candidates:
                raise FluxDecodeError("Unable to infer layout for flat image")
            layout_desc = ensure_layout_loaded(candidates[0].layout_id) if candidates[0].layout_id else None
            if layout_desc:
                image_obj = _prepare_image(path, layout_desc.layout_id, candidates[0].encoding or selected_encoding)
        # Build TrackSectors from TrackSectorImage if needed.
        if isinstance(image_obj, TrackSectorImage):
            tracks = image_obj.tracks
        else:
            raise FluxDecodeError("Flat image could not be reconstructed into sectors")
        report = build_qc_report_from_tracks(tracks, layout=layout_desc, track_step=1)
        scp = None

    targets: list[Path] = []
    if json_out:
        write_qc_report_json(report, json_out)
        targets.append(json_out)
    if text_out:
        write_qc_report_text(report, text_out, layout=layout_desc)
        targets.append(text_out)
    if not targets:
        if scp is not None:
            track_ids = [track.track for track in scp.tracks]
            heads_present = {track.side for track in scp.tracks}
        else:
            track_ids = [ts.track for ts in tracks]
            heads_present = {ts.head for ts in tracks}
        step = infer_track_step(track_ids) if track_ids else 1
        logical_tracks = logical_track_count(track_ids, step) if track_ids else 0
        if layout_desc:
            cylinders = layout_desc.tracks
            heads = layout_desc.sides
        else:
            cylinders = logical_tracks
            heads = len(heads_present) if heads_present else 0
        total_sectors = sum(track.total_sectors for track in report.tracks)
        good_sectors = sum(track.good_sectors for track in report.tracks)
        bad_sectors = sum(track.bad_sectors for track in report.tracks)
        decoded_sectors = sum(
            track.total_sectors - track.missing_sectors - track.no_data_sectors
            for track in report.tracks
        )
        suspect_sectors = report.suspect_sectors or (bad_sectors + sum(track.weak_sectors for track in report.tracks))
        status = report.status or ("good" if suspect_sectors == 0 and report.missing_tracks == 0 else "suspect")
        typer.echo(
            f"Analysed {len(report.tracks)} tracks; cylinders {cylinders}; heads {heads}; "
            f"total sectors {total_sectors}; decoded sectors {decoded_sectors}; "
            f"good sectors {good_sectors}; suspect sectors {suspect_sectors}; status {status}; "
            f"overall confidence {report.overall_confidence:.2f}; missing tracks {report.missing_tracks}"
        )
        if good_sectors == 0 and decoded_sectors:
            typer.echo("Note: CRC validation failed for decoded sectors. Use --text-out or --json-out for details.")
        elif suspect_sectors:
            typer.echo("Note: Suspect sectors detected. Use --text-out or --json-out for details.")
    if targets:
        target_path = targets[0]
        prov_target = prov_out or target_path.with_suffix(target_path.suffix + ".provenance.json")
        record = ProvenanceRecord(
            tool_name="fluxctl",
            tool_version=__version__,
            operation="qc",
            input_path=path,
            input_sha256=sha256_file(path),
            output_path=target_path,
            output_sha256=ProvenanceRecord.sha256_file(target_path),
            parameters={
                "encoding": selected_encoding,
                "layout": layout_desc.layout_id if layout_desc else "",
                "json_out": str(json_out or ""),
                "text_out": str(text_out or ""),
            },
            plugins={"decoder": selected_encoding},
            decoder=selected_encoding,
        )
        write_provenance(record, prov_target)


@app.command()
@_handle_cli_errors
def visualize(
    path: Path = typer.Argument(..., exists=True, readable=True),
    format: str = typer.Option("ascii", "--format", help="Output format: ascii or svg"),
    out: Optional[Path] = typer.Option(None, "--out", help="Write output to a file"),
    encoding: str = typer.Option("mfm", "--encoding", help="Bitstream encoding (mfm, fm, gcr, apple2_gcr)"),
    layout: Optional[str] = typer.Option(None, "--layout", help="Layout identifier for flat images"),
    prov_out: Optional[Path] = typer.Option(None, "--prov-out", help="Provenance sidecar path"),
):
    """Render a disk map in ASCII or SVG form."""

    format_lower = format.lower()
    if format_lower not in {"ascii", "svg"}:
        raise typer.BadParameter("--format must be 'ascii' or 'svg'")

    load_builtin_layouts()
    load_builtin_decoders()
    ext = path.suffix.lower()

    if ext == ".scp":
        image = parse_scp(path)
        selected_encoding = encoding
        layout_desc = ensure_layout_loaded(layout) if layout else None
        if layout_desc is None:
            # Fast-path: many captures (e.g., Amiga) are MFM even when encoding
            # detection leans GCR. Try MFM layout detection first.
            layout_candidate = detect_layout(image, "mfm", path)
            if layout_candidate:
                layout_desc = layout_candidate.layout
                selected_encoding = layout_desc.encoding
            else:
                encoding_candidate = detect_encoding(image, path=path)
                if encoding_candidate:
                    selected_encoding = encoding_candidate.encoding
                layout_candidate = detect_layout(image, selected_encoding, path)
                if layout_candidate:
                    layout_desc = layout_candidate.layout
                    selected_encoding = layout_desc.encoding
        decoder = _get_decoder(selected_encoding)
        disk_map = build_disk_map(image, decoder, layout=layout_desc)
    else:
        layout_desc = ensure_layout_loaded(layout) if layout else None
        tracks: list[TrackSectors]
        if layout_desc is None:
            candidates = _probe_flat_image(path)
            layout_desc = ensure_layout_loaded(candidates[0].layout_id) if candidates and candidates[0].layout_id else None
        image_obj = _prepare_image(path, layout_desc.layout_id if layout_desc else None, encoding)
        if isinstance(image_obj, TrackSectorImage):
            tracks = image_obj.tracks
        else:
            raise FluxDecodeError("Flat image could not be reconstructed into sectors for visualisation")
        disk_map = build_disk_map_from_tracksectors(tracks)

    output_path: Optional[Path] = None
    if format_lower == "ascii":
        ascii_map = render_ascii(disk_map)
        if out:
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(ascii_map, encoding="utf-8")
            output_path = out
        else:
            typer.echo(ascii_map)
    else:
        if out is None:
            raise typer.BadParameter("--out is required for SVG output")
        svg_map = render_svg(disk_map)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(svg_map, encoding="utf-8")
        typer.echo(f"Wrote SVG visualization to {out}")
        output_path = out

    if output_path:
        prov_target = prov_out or output_path.with_suffix(output_path.suffix + ".provenance.json")
        record = ProvenanceRecord(
            tool_name="fluxctl",
            tool_version=__version__,
            operation="visualize",
            input_path=path,
            input_sha256=sha256_file(path),
            output_path=output_path,
            output_sha256=ProvenanceRecord.sha256_file(output_path),
            parameters={"format": format_lower, "encoding": encoding, "out": str(out or "")},
            plugins={"decoder": encoding, "renderer": format_lower},
            decoder=encoding,
            encoder=None,
        )
        write_provenance(record, prov_target)


@app.command()
@_handle_cli_errors
def convert(
    path: Path = typer.Argument(..., exists=True, readable=True),
    to: str = typer.Option(..., "--to", help="Exporter key (raw, imd, adf, d64, d71, d81, g64)"),
    out: Path = typer.Option(..., "--out", help="Destination image path"),
    layout: Optional[str] = typer.Option(None, "--layout", help="Layout ID for SCP reconstruction or flat image geometry"),
    encoding: str = typer.Option("mfm", "--encoding", help="Bitstream encoding for SCP sources"),
    prov_out: Optional[Path] = typer.Option(None, "--prov-out", help="Provenance sidecar output"),
):
    """Convert SCP, IMD, TRS-80 DSK/DMK, or flat sector images to a supported output format.

    Examples:
    fluxctl convert disk.scp --layout ibm_mfm_720k --to raw --out disk.img
    fluxctl convert disk.img --layout ibm_mfm_720k --to imd --out disk.imd
    fluxctl convert c64.scp --layout commodore_gcr_1541_170k --to g64 --out disk.g64
    fluxctl convert c128.scp --layout commodore_gcr_1571_341k --to d71 --out disk.d71
    fluxctl convert 1581.scp --layout commodore_mfm_1581_800k --to d81 --out disk.d81
    """

    result = _prepare_convert_payload(path, to, layout, encoding)
    exported = result.payload
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(exported)

    provenance = ProvenanceRecord(
        tool_name="fluxctl",
        tool_version=__version__,
        operation="convert",
        input_path=path,
        input_sha256=sha256_file(path),
        output_path=out,
        output_sha256=hashlib.sha256(exported).hexdigest(),
        parameters={
            "layout": layout or "",
            "resolved_layout": result.layout_id,
            "encoding": result.encoding,
            "exporter": to,
            "output": str(out),
        },
        plugins={"exporter": result.exporter_name, "exporter_version": result.exporter_version, "decoder": result.encoding},
        decoder=result.encoding,
        encoder=to,
    )
    prov_target = prov_out or out.with_suffix(out.suffix + ".provenance.json")
    write_provenance(provenance, prov_target)

    if _is_lossy(result.track_data, result.exporter_metadata):
        typer.secho(
            "Warning: export may be lossy due to missing or low-confidence sectors", fg=typer.colors.YELLOW
        )
    typer.echo(f"Wrote {out}")


@app.command()
@_handle_cli_errors
def roundtrip(
    path: Path = typer.Argument(..., exists=True, readable=True),
    to: str = typer.Option(..., "--to", help="First exporter key to test (raw, imd, adf, d64, d71, d81, g64)"),
    back_to: Optional[str] = typer.Option(
        None,
        "--back-to",
        help="Exporter key for the return leg. Defaults to the source container where meaningful, or raw for SCP/IMD.",
    ),
    layout: Optional[str] = typer.Option(None, "--layout", help="Layout ID for SCP reconstruction or flat image geometry"),
    encoding: str = typer.Option("mfm", "--encoding", help="Bitstream encoding for SCP sources"),
    work_dir: Optional[Path] = typer.Option(None, "--work-dir", help="Keep intermediate images in this directory"),
    json_out: Optional[Path] = typer.Option(None, "--json-out", help="Write round-trip report to JSON"),
    prov_out: Optional[Path] = typer.Option(None, "--prov-out", help="Provenance sidecar for --json-out"),
):
    """Verify sector-level losslessness through a conversion round trip.

    Examples:
    fluxctl roundtrip disk.scp --layout amiga_mfm_880k --to adf
    fluxctl roundtrip disk.adf --to raw --back-to adf
    fluxctl roundtrip disk.img --layout amiga_mfm_880k --to adf --back-to raw
    """

    back_exporter = back_to or _infer_roundtrip_back_exporter(path)
    temp_context = tempfile.TemporaryDirectory(prefix="fluxctl-roundtrip-") if work_dir is None else None
    base_dir = Path(temp_context.name) if temp_context is not None else work_dir
    assert base_dir is not None
    base_dir.mkdir(parents=True, exist_ok=True)
    try:
        first_path = base_dir / f"{path.stem}-to-{to}{_exporter_suffix(to)}"
        final_path = base_dir / f"{path.stem}-roundtrip-{back_exporter}{_exporter_suffix(back_exporter)}"

        first = _prepare_convert_payload(path, to, layout, encoding)
        first_path.write_bytes(first.payload)

        resolved_layout = first.layout_id or layout
        original_bytes, original_meta = _image_bytes_for_compare(path, resolved_layout, first.encoding)
        first_bytes, first_meta = _image_bytes_for_compare(first_path, resolved_layout, first.encoding)
        second = _prepare_convert_payload(first_path, back_exporter, resolved_layout, first.encoding)
        final_path.write_bytes(second.payload)
        final_bytes, final_meta = _image_bytes_for_compare(final_path, resolved_layout, first.encoding)

        original_sha = hashlib.sha256(original_bytes).hexdigest()
        first_sha = hashlib.sha256(first_bytes).hexdigest()
        final_sha = hashlib.sha256(final_bytes).hexdigest()
        forward_diff = _first_diff_offset(original_bytes, first_bytes)
        final_diff = _first_diff_offset(original_bytes, final_bytes)
        forward_match = forward_diff is None and len(original_bytes) == len(first_bytes)
        roundtrip_match = final_diff is None and len(original_bytes) == len(final_bytes)
        lossy = _is_lossy(first.track_data, first.exporter_metadata) or _is_lossy(second.track_data, second.exporter_metadata)

        report = {
            "input": str(path),
            "to": to,
            "back_to": back_exporter,
            "layout": resolved_layout or "",
            "encoding": first.encoding,
            "work_dir": str(base_dir) if work_dir is not None else "",
            "first_path": str(first_path) if work_dir is not None else "",
            "final_path": str(final_path) if work_dir is not None else "",
            "original_sha256": original_sha,
            "first_sha256": first_sha,
            "final_sha256": final_sha,
            "original_length": len(original_bytes),
            "first_length": len(first_bytes),
            "final_length": len(final_bytes),
            "forward_match": forward_match,
            "roundtrip_match": roundtrip_match,
            "forward_first_diff_offset": forward_diff,
            "roundtrip_first_diff_offset": final_diff,
            "lossy_warning": lossy,
            "meta": {
                "original": original_meta,
                "first": first_meta,
                "final": final_meta,
            },
        }

        typer.echo(f"Input decoded sha256:      {original_sha} ({len(original_bytes)} bytes)")
        typer.echo(f"After --to {to} sha256:    {first_sha} ({len(first_bytes)} bytes)")
        typer.echo(f"After --back-to {back_exporter}: {final_sha} ({len(final_bytes)} bytes)")
        if work_dir is not None:
            typer.echo(f"Intermediate images: {base_dir}")
        if lossy:
            typer.secho("Warning: one conversion leg reported missing or low-confidence sectors", fg=typer.colors.YELLOW)
        if forward_match:
            typer.secho("Forward check: MATCH", fg=typer.colors.GREEN)
        else:
            typer.secho("Forward check: DIFFER", fg=typer.colors.YELLOW)
            if forward_diff is not None:
                typer.echo(f"Forward first difference at decoded offset {forward_diff}")
        if roundtrip_match:
            typer.secho("Round-trip check: MATCH", fg=typer.colors.GREEN)
        else:
            typer.secho("Round-trip check: DIFFER", fg=typer.colors.YELLOW)
            if final_diff is not None:
                typer.echo(f"Round-trip first difference at decoded offset {final_diff}")

        if json_out:
            json_out.parent.mkdir(parents=True, exist_ok=True)
            json_out.write_text(json.dumps(report, indent=2), encoding="utf-8")
            prov_target = prov_out or json_out.with_suffix(json_out.suffix + ".provenance.json")
            record = ProvenanceRecord(
                tool_name="fluxctl",
                tool_version=__version__,
                operation="roundtrip",
                input_path=path,
                input_sha256=sha256_file(path),
                output_path=json_out,
                output_sha256=ProvenanceRecord.sha256_file(json_out),
                parameters={
                    "to": to,
                    "back_to": back_exporter,
                    "layout": layout or "",
                    "resolved_layout": resolved_layout or "",
                    "encoding": first.encoding,
                    "work_dir": str(work_dir or ""),
                    "json_out": str(json_out),
                },
                plugins={"forward_exporter": first.exporter_name, "back_exporter": second.exporter_name},
                decoder=first.encoding,
                encoder=back_exporter,
                evidence=[
                    f"original_decoded_sha256={original_sha}",
                    f"first_decoded_sha256={first_sha}",
                    f"final_decoded_sha256={final_sha}",
                    f"forward_match={int(forward_match)}",
                    f"roundtrip_match={int(roundtrip_match)}",
                ],
            )
            write_provenance(record, prov_target)
            typer.echo(f"Wrote round-trip report to {json_out}")

        if not roundtrip_match:
            raise typer.Exit(code=1)
    finally:
        if temp_context is not None:
            temp_context.cleanup()


@app.command()
@_handle_cli_errors
def extract(
    path: Path = typer.Argument(..., exists=True, readable=True),
    layout: Optional[str] = typer.Option(None, "--layout", help="Layout identifier for reconstruction"),
    encoding: str = typer.Option("mfm", "--encoding", help="Bitstream encoding for SCP sources"),
    list_only: bool = typer.Option(False, "--list", help="List directory contents"),
    file_path: Optional[str] = typer.Option(None, "--path", help="Filesystem path to extract"),
    out: Optional[Path] = typer.Option(None, "--out", help="Destination for extracted data"),
    prov_out: Optional[Path] = typer.Option(None, "--prov-out", help="Provenance sidecar path"),
):
    """Detect filesystem, list directories, or extract a file."""

    if file_path and out is None and not list_only:
        raise typer.BadParameter("--out must be provided when --path is used")

    selected_layout = layout
    selected_encoding = encoding
    if selected_layout is None:
        load_builtin_decoders()
        load_builtin_layouts()
        if path.suffix.lower() == ".scp":
            scp = parse_scp(path)
            encoding_candidate = detect_encoding(scp, path=path)
            if encoding_candidate:
                selected_encoding = encoding_candidate.encoding
                layout_candidate = detect_layout(scp, selected_encoding, path)
                if layout_candidate:
                    selected_layout = layout_candidate.layout.layout_id
        else:
            candidates = _probe_flat_image(path)
            if candidates:
                primary = sorted(candidates, key=lambda c: c.score, reverse=True)[0]
                selected_layout = primary.layout_id
                selected_encoding = primary.encoding or selected_encoding

    image_obj = _prepare_image(path, selected_layout, selected_encoding)
    detection = _filesystem_detection_for_image(image_obj, path)
    filesystem = detection.plugin

    if filesystem is None:
        if out is None and (file_path or list_only):
            detail = "; ".join(
                evidence.replace("=", ": ", 1)
                for evidence in detection.evidence
                if evidence.startswith(("cbm_dos_", "filesystem_"))
            )
            message = "Filesystem metadata was detected, but no usable filesystem view is available"
            if detection.primary:
                message += f" ({detection.primary})"
            if detail:
                message += f": {detail}"
            raise FluxctlError(message)
        if out is None:
            typer.echo("No filesystem detected; provide --out to dump raw sectors")
            return
        raw_bytes = b"".join(image_obj.iter_sectors())
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(raw_bytes)
        typer.echo(f"No filesystem detected; wrote raw sector dump to {out}")
        record = ProvenanceRecord(
            tool_name="fluxctl",
            tool_version=__version__,
            operation="extract",
            input_path=path,
            input_sha256=sha256_file(path),
            output_path=out,
            output_sha256=ProvenanceRecord.sha256_bytes(raw_bytes),
            parameters={"layout": selected_layout or "", "encoding": selected_encoding, "path": file_path or ""},
            plugins={},
            decoder=selected_encoding,
            encoder=None,
        )
        prov_target = prov_out or out.with_suffix(out.suffix + ".provenance.json")
        write_provenance(record, prov_target)
        return

    if list_only or file_path is None:
        target_dir = "/" if file_path is None else file_path
        entries = filesystem.list_directory(target_dir)
        for entry in entries:
            type_label = "<DIR>" if entry.is_dir else f"{entry.size} bytes"
            typer.echo(f"{entry.name}\t{type_label}")
        return

    assert out is not None  # guarded above
    content = filesystem.extract_file(file_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(content)
    typer.echo(f"Extracted {file_path} to {out}")
    prov_target = prov_out or out.with_suffix(out.suffix + ".provenance.json")
    record = ProvenanceRecord(
        tool_name="fluxctl",
        tool_version=__version__,
        operation="extract",
        input_path=path,
        input_sha256=sha256_file(path),
        output_path=out,
        output_sha256=ProvenanceRecord.sha256_bytes(content),
        parameters={"layout": selected_layout or "", "encoding": selected_encoding, "path": file_path or ""},
        plugins={"filesystem": filesystem.__class__.__name__},
        decoder=selected_encoding,
        encoder=None,
    )
    write_provenance(record, prov_target)


@app.command()
@_handle_cli_errors
def decode(
    path: Path = typer.Argument(..., exists=True, readable=True),
    assume: str = typer.Option(..., "--assume"),
    policy: str = typer.Option("best_crc", "--policy"),
):
    """Decode using a selected layout candidate and print a summary."""
    track_data = _decode_tracks(path, assume)
    typer.echo(f"Decoded {len(track_data)} tracks using layout {assume} with policy {policy}")


@app.command()
@_handle_cli_errors
def patch(
    path: Path = typer.Argument(..., exists=True, readable=True),
    layout: str = typer.Option(..., "--layout", help="Layout ID used to decode and re-export the image"),
    write_sector: str = typer.Option(
        ..., "--write-sector", help="Patch target as T:S:HEX or side-aware T:H:S:HEX"
    ),
    out: Path = typer.Option(..., "--out", help="Destination raw image path"),
    prov_out: Optional[Path] = typer.Option(None, "--prov-out", help="Provenance sidecar output"),
):
    """Patch one full sector and export a raw image.

    Use T:S:HEX only when the sector is unambiguous. On double-sided media,
    use T:H:S:HEX, for example 0:1:1:DEADBEEF... for track 0, head 1,
    sector 1.
    """

    layout_desc = ensure_layout_loaded(layout)
    load_builtin_exporters()
    track_data = _decode_tracks(path, layout)
    try:
        track_idx, head_idx, sector_idx, payload = _parse_write_sector_spec(write_sector)
    except ValueError as exc:
        raise typer.BadParameter("Expected T:S:HEX or T:H:S:HEX") from exc
    _apply_sector_patch(track_data, track_idx, head_idx, sector_idx, payload)
    exporter_info = registry.exporter.get("raw")
    if exporter_info is None:
        raise ExportError("Raw exporter not available")
    exporter = exporter_info.entry
    image_obj = TrackSectorImage(track_data, bytes_per_sector=layout_desc.sector_size)
    _apply_layout_geometry(image_obj, layout_desc)
    exported = exporter.export(image_obj)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(exported)
    exporter_metadata = exporter.metadata()

    provenance = ProvenanceRecord(
        tool_name="fluxctl",
        tool_version=__version__,
        operation="patch",
        input_path=path,
        input_sha256=sha256_file(path),
        output_sha256=hashlib.sha256(exported).hexdigest(),
        parameters={
            "layout": layout, "patched_sector": write_sector, "encoding": layout_desc.encoding, "output": str(out)
        },
        plugins={"exporter": exporter_info.name, "exporter_version": exporter_info.version},
        output_path=out,
        decoder=layout_desc.encoding,
        encoder=exporter_info.name,
    )
    write_provenance(provenance, prov_out or out.with_suffix(out.suffix + ".provenance.json"))
    out.with_suffix(out.suffix + ".patchlog.json").write_text(
        json.dumps({"patched": write_sector}, indent=2), encoding="utf-8"
    )
    typer.echo(f"Patched image written to {out}")


if __name__ == "__main__":
    app()
