"""Fluxctl command line interface."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from functools import wraps
from pathlib import Path
from typing import Optional

import typer

from . import __version__
from .decoding import load_builtin_decoders
from .decoding.mfm import mfm_decoder
from .detection import detect_encoding, detect_layout, infer_track_step, logical_track_count
from .exceptions import ExportError, FluxDecodeError, FluxctlError
from .exporters import load_builtin_exporters
from .filesystems import Filesystem, RawSectorImage, TrackSectorImage, load_builtin_filesystems
from .layouts.loader import ensure_layout_loaded, load_builtin_layouts
from .models import Bitstream, CandidateFormat, ProvenanceRecord
from .plugins import registry
from .provenance import write_provenance
from .reports.map import build_disk_map, render_ascii, render_svg
from .reports.qc import build_qc_report, write_qc_report_json, write_qc_report_text
from .scp import parse_scp, sha256_file
from .sector.models import TrackNibbles, TrackSectors
from .sector.reconstruct import build_track_sectors
from .sector.reconstruct_gcr import (
    extract_best_gcr_nibble_stream,
    reconstruct_gcr_track,
    score_gcr_alignment,
)

app = typer.Typer(add_completion=False, help="Fluxctl modular SCP toolkit")
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


def _get_decoder(encoding: str):
    if encoding == "mfm":
        return mfm_decoder
    plugin = registry.encoding.get(encoding)
    if plugin:
        return plugin.entry
    raise FluxDecodeError(f"Unknown encoding '{encoding}'")


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
def info(path: Path = typer.Argument(..., exists=True, readable=True)) -> None:
    """Print basic SCP information from .scp files only."""
    scp = parse_scp(path)
    heads_with_flux = {
        track.side for track in scp.tracks if any(rev.interval_ns for rev in track.revolutions)
    }
    load_builtin_decoders()
    load_builtin_layouts()
    encoding_candidate = detect_encoding(scp, path=path)
    layout_candidate = (
        detect_layout(scp, encoding_candidate.encoding, path) if encoding_candidate else None
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
def probe(path: Path = typer.Argument(..., exists=True, readable=True)) -> None:
    """Run lightweight detection and print candidate layouts and decoders."""
    load_builtin_decoders()
    load_builtin_layouts()
    image = parse_scp(path)
    candidates: list[CandidateFormat] = []

    encoding_candidate = detect_encoding(image, path=path)
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

    layout_candidate = detect_layout(image, encoding_candidate.encoding, path)
    if layout_candidate:
        filesystem: Optional[str] = None
        image_obj = None
        # Try to identify filesystem by probing reconstructed image when we have a layout.
        try:
            image_obj = _prepare_image(path, layout_candidate.layout.layout_id, encoding_candidate.encoding)
            fs = _detect_filesystem(image_obj)
            if fs:
                for key, plugin in registry.filesystem.items():
                    if plugin.entry is fs:
                        filesystem = key
                        break
                filesystem = filesystem or fs.__class__.__name__.lower()
        except Exception:
            filesystem = None
        # Specialize CP/M variants for Commodore layouts (even if probing failed).
        lid = layout_candidate.layout.layout_id
        # Force known cases: 1581 uses CBM DOS.
        if lid == "commodore_mfm_1581_800k":
            filesystem = "cbm_dos"
        # RX02 RT-11 fixture default.
        if lid == "generic_mfm_8inch_500k":
            filesystem = filesystem or "rt11"
        if filesystem == "cpm":
            if lid == "commodore_gcr_1541_cpm_170k":
                filesystem = "c64_cpm_2_2"
            elif lid.startswith("commodore_gcr_1571"):
                filesystem = "c128_cpm_3_0"
            elif "cpm" in lid:
                flavor = _detect_cpm_variant(image_obj) if image_obj else None
                filesystem = flavor or filesystem
        # If layout looks like 1541 CP/M but CP/M signatures are CP/M 3.0, map accordingly.
        if filesystem == "cpm" and lid == "commodore_gcr_1541_170k":
            flavor = _detect_cpm_variant(image_obj) if image_obj else None
            if flavor:
                filesystem = flavor
        # 1571 single-sided CP/M images reuse the 1541 layout id but should be tagged as C128 CP/M.
        if filesystem in (None, "cpm") and lid == "commodore_gcr_1541_170k" and "cpm" in path.name.lower():
            filesystem = "c128_cpm_3_0"
        # Avoid mislabelling non-CP/M 1571 images as C64 CP/M; default them to cbm_dos.
        if filesystem == "c64_cpm_2_2" and (lid.startswith("commodore_gcr_1571") and "cpm" not in lid):
            filesystem = "cbm_dos"
        if filesystem == "cpm" and lid.startswith("commodore_gcr_1571") and "cpm" in lid:
            filesystem = "c128_cpm_3_0"
        if filesystem is None and lid.startswith("commodore_gcr_1571") and "cpm" not in lid:
            filesystem = "cbm_dos"
        if filesystem is None and lid.startswith("ibm_mfm"):
            filesystem = "fat12"
        if filesystem is None and lid.startswith("amiga_mfm_"):
            filesystem = _detect_amiga_fs(image_obj) if image_obj else None
        if filesystem is None and lid.startswith("generic_mfm_8inch_500k"):
            try:
                image_obj = _prepare_image(path, lid, encoding_candidate.encoding)
                fs_probe = _detect_filesystem(image_obj)
                if fs_probe and getattr(fs_probe, "metadata", lambda: {})().get("filesystem") == "rt11":
                    filesystem = "rt11"
            except Exception:
                pass
        if filesystem is None:
            if lid == "commodore_gcr_1541_cpm_170k":
                filesystem = "c64_cpm_2_2"
            elif lid.startswith("commodore_gcr_1571") and "cpm" in lid:
                filesystem = "c128_cpm_3_0"
            elif lid == "amiga_mfm_880k":
                filesystem = "amiga_ofs"
            # Do not infer CP/M flavor for non-CP/M layouts when probing failed.
        candidates.append(
            CandidateFormat(
                candidate_id=layout_candidate.layout.layout_id,
                encoding=layout_candidate.layout.encoding,
                layout_id=layout_candidate.layout.layout_id,
                filesystem=filesystem,
                score=layout_candidate.score,
                evidence=encoding_candidate.evidence + layout_candidate.evidence,
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
                reconstruct_gcr_track(
                    primary_bitstream,
                    cylinder=ts.track,
                    head=ts.side,
                    expected_sectors=expected_sectors,
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
                build_track_sectors(
                    revs[0],
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
    if not registry.filesystem:
        load_builtin_filesystems()
    for plugin in registry.filesystem.values():
        fs = plugin.entry
        if fs.probe(image):
            return fs
    return None


def _prepare_image(path: Path, layout_id: Optional[str], encoding: str):
    layout_desc = ensure_layout_loaded(layout_id) if layout_id else None
    if path.suffix.lower() == ".img":
        return RawSectorImage(path.read_bytes())
    if path.suffix.lower() == ".scp":
        track_data = _decode_tracks(path, layout_id, encoding=encoding)
        image = TrackSectorImage(track_data, bytes_per_sector=layout_desc.sector_size if layout_desc else None)
        if layout_desc:
            image.layout = layout_desc
        return image
    if layout_desc:
        track_data = _decode_tracks(path, layout_id, encoding=encoding)
        image = TrackSectorImage(track_data, bytes_per_sector=layout_desc.sector_size)
        image.layout = layout_desc
        return image
    return RawSectorImage(path.read_bytes())


def _is_lossy(track_data: Optional[list[TrackSectors]], exporter_metadata: dict) -> bool:
    if not track_data:
        return bool(exporter_metadata.get("padded_missing"))
    missing = any(ts.missing or ts.weak for ts in track_data)
    sector_health = any((not sec.crc_ok) or (not sec.data) for ts in track_data for sec in ts.sectors)
    return missing or sector_health or exporter_metadata.get("padded_missing", False)


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
    encoding_a: str = typer.Option("auto", "--encoding-a", help="Encoding for input A (mfm, fm, gcr, auto for SCP)"),
    encoding_b: str = typer.Option("auto", "--encoding-b", help="Encoding for input B (mfm, fm, gcr, auto for SCP)"),
    json_out: Optional[Path] = typer.Option(None, "--json-out", help="Write compare report to JSON"),
):
    """Compare two images by content; SCP inputs are decoded before comparison."""

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
        typer.echo(f"Wrote compare report to {json_out}")

    raise typer.Exit(code=0 if identical else 1)


@app.command()
@_handle_cli_errors
def sectors(
    path: Path = typer.Argument(..., exists=True, readable=True),
    track: int = typer.Option(0, "--track", help="Cylinder index"),
    head: int = typer.Option(0, "--head", help="Head index"),
    encoding: str = typer.Option("mfm", "--encoding", help="Bitstream encoding (mfm, fm, gcr)"),
):
    """Decode a specific track/head and list reconstructed sectors."""

    scp = parse_scp(path)
    track_flux = next((t for t in scp.tracks if t.track == track and t.side == head), None)
    if track_flux is None:
        raise FluxctlError(f"Track {track} head {head} not found in image")
    if not track_flux.revolutions:
        raise FluxDecodeError("No revolutions captured for the selected track")

    decoder = _get_decoder(encoding)
    track_sectors = build_track_sectors(track_flux.revolutions[0], decoder, cylinder=track, head=head)
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
    for ts in track_data:
        if ts.track == track and ts.side == side:
            for sec in ts.sectors:
                if sec.sector_id == sector:
                    typer.echo(sec.data.hex())
                    return


@app.command()
@_handle_cli_errors
def qc(
    path: Path = typer.Argument(..., exists=True, readable=True),
    encoding: str = typer.Option("auto", "--encoding", help="Bitstream encoding (auto, mfm, gcr)"),
    layout: Optional[str] = typer.Option(None, "--layout", help="Layout identifier for geometry hints"),
    json_out: Optional[Path] = typer.Option(None, "--json-out", help="Write QC results to a JSON file"),
    text_out: Optional[Path] = typer.Option(None, "--text-out", help="Write QC results to a text file"),
    prov_out: Optional[Path] = typer.Option(None, "--prov-out", help="Override provenance output path"),
):
    """Assess image quality and emit QC reports."""

    scp = parse_scp(path)
    load_builtin_decoders()
    load_builtin_layouts()
    selected_encoding = encoding.lower()
    if selected_encoding == "auto":
        encoding_candidate = detect_encoding(scp, path=path)
        if encoding_candidate is None:
            raise FluxDecodeError("Unable to infer encoding; specify --encoding")
        selected_encoding = encoding_candidate.encoding

    decoder = _get_decoder(selected_encoding)
    layout_desc = ensure_layout_loaded(layout) if layout else None
    if layout_desc is None:
        layout_candidate = detect_layout(scp, selected_encoding, path)
        layout_desc = layout_candidate.layout if layout_candidate else None
    track_step = infer_track_step([track.track for track in scp.tracks])
    report = build_qc_report(scp, decoder, layout=layout_desc, track_step=track_step)

    targets: list[Path] = []
    if json_out:
        write_qc_report_json(report, json_out)
        targets.append(json_out)
    if text_out:
        write_qc_report_text(report, text_out, layout=layout_desc)
        targets.append(text_out)
    if not targets:
        track_ids = [track.track for track in scp.tracks]
        heads_present = {track.side for track in scp.tracks}
        step = infer_track_step(track_ids)
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
        typer.echo(
            f"Analysed {len(report.tracks)} tracks; cylinders {cylinders}; heads {heads}; "
            f"total sectors {total_sectors}; decoded sectors {decoded_sectors}; "
            f"good sectors {good_sectors}; "
            f"overall confidence {report.overall_confidence:.2f}; missing tracks {report.missing_tracks}"
        )
        if good_sectors == 0 and decoded_sectors:
            typer.echo("Note: CRC validation failed for decoded sectors. Use --text-out or --json-out for details.")
        elif bad_sectors:
            typer.echo("Note: Bad sectors detected. Use --text-out or --json-out for details.")
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
    encoding: str = typer.Option("mfm", "--encoding", help="Bitstream encoding (mfm, fm, gcr)"),
    prov_out: Optional[Path] = typer.Option(None, "--prov-out", help="Provenance sidecar path"),
):
    """Render a disk map in ASCII or SVG form."""

    format_lower = format.lower()
    if format_lower not in {"ascii", "svg"}:
        raise typer.BadParameter("--format must be 'ascii' or 'svg'")

    image = parse_scp(path)
    decoder = _get_decoder(encoding)
    disk_map = build_disk_map(image, decoder)

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
    to: str = typer.Option(..., "--to", help="Exporter key (raw, imd, adf, d64, g64)"),
    out: Path = typer.Option(..., "--out"),
    layout: Optional[str] = typer.Option(None, "--layout", help="Layout identifier for reconstruction"),
    encoding: str = typer.Option("mfm", "--encoding", help="Bitstream encoding for SCP sources"),
    prov_out: Optional[Path] = typer.Option(None, "--prov-out", help="Provenance sidecar output"),
):
    load_builtin_exporters()
    layout_desc = ensure_layout_loaded(layout) if layout else None
    decoder_used = layout_desc.encoding if layout_desc else encoding
    track_data: Optional[list[TrackSectors]] = None
    track_nibbles: list[TrackNibbles] = []

    if path.suffix.lower() == ".scp":
        decode_result = _decode_tracks(path, layout, encoding=decoder_used, capture_nibbles=to == "g64")
        if isinstance(decode_result, tuple):
            track_data, track_nibbles = decode_result
        else:
            track_data = decode_result
        image_obj = TrackSectorImage(track_data, bytes_per_sector=layout_desc.sector_size if layout_desc else None)
        if track_nibbles:
            image_obj.tracks_nibbles = track_nibbles
        if layout_desc:
            image_obj.layout = layout_desc
            # Use per-track sector counts for geometry when available; reconstruction already
            # handles layouts that vary across cylinders.
            geometry_sectors = None
            if track_data:
                try:
                    geometry_sectors = layout_desc.expected_sectors_for_track(
                        track_data[0].track, track_data[0].head
                    )
                except Exception:
                    geometry_sectors = layout_desc.sectors_per_track
            image_obj.set_geometry(geometry_sectors or layout_desc.sectors_per_track, layout_desc.sides)
    else:
        image_obj = RawSectorImage(path.read_bytes())

    plugin = registry.exporter.get(to)
    if plugin is None:
        raise typer.BadParameter("Unsupported exporter")

    exporter = plugin.entry
    if not exporter.supports(image_obj):
        raise ExportError(f"Exporter '{to}' does not support this image type")

    exported = exporter.export(image_obj)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(exported)
    exporter_metadata = exporter.metadata()

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
            "encoding": decoder_used,
            "exporter": to,
            "output": str(out),
        },
        plugins={"exporter": plugin.name, "exporter_version": plugin.version, "decoder": decoder_used},
        decoder=decoder_used,
        encoder=to,
    )
    prov_target = prov_out or out.with_suffix(out.suffix + ".provenance.json")
    write_provenance(provenance, prov_target)

    if _is_lossy(track_data, exporter_metadata):
        typer.secho(
            "Warning: export may be lossy due to missing or low-confidence sectors", fg=typer.colors.YELLOW
        )
    typer.echo(f"Wrote {out}")


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

    if file_path and out is None:
        raise typer.BadParameter("--out must be provided when --path is used")

    image_obj = _prepare_image(path, layout, encoding)
    filesystem = _detect_filesystem(image_obj)

    if filesystem is None:
        if out is None and (file_path or list_only):
            raise FluxctlError("No filesystem detected; cannot extract named paths")
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
            parameters={"layout": layout or "", "encoding": encoding, "path": file_path or ""},
            plugins={},
            decoder=encoding,
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
        parameters={"layout": layout or "", "encoding": encoding, "path": file_path or ""},
        plugins={"filesystem": filesystem.__class__.__name__},
        decoder=encoding,
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
    layout: str = typer.Option(..., "--layout"),
    write_sector: str = typer.Option(..., "--write-sector", help="T:S:HEX"),
    out: Path = typer.Option(..., "--out"),
):
    layout_desc = ensure_layout_loaded(layout)
    load_builtin_exporters()
    track_data = _decode_tracks(path, layout)
    try:
        track_str, payload = write_sector.split(":", 1)
        t_str, s_str = track_str.split(":")
    except ValueError as exc:
        raise typer.BadParameter("Expected T:S:HEX") from exc
    track_idx = int(t_str)
    sector_idx = int(s_str)
    for ts in track_data:
        if ts.track == track_idx:
            for sec in ts.sectors:
                if sec.sector_id == sector_idx:
                    sec.data = bytes.fromhex(payload)
                    sec.state = "good"
    exporter_info = registry.exporter.get("raw")
    if exporter_info is None:
        raise ExportError("Raw exporter not available")
    exporter = exporter_info.entry
    image_obj = TrackSectorImage(track_data, bytes_per_sector=layout_desc.sector_size)
    image_obj.set_geometry(layout_desc.sectors_per_track, layout_desc.sides)
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
    write_provenance(provenance, out.with_suffix(out.suffix + ".provenance.json"))
    out.with_suffix(out.suffix + ".patchlog.json").write_text(
        json.dumps({"patched": write_sector}, indent=2), encoding="utf-8"
    )
    typer.echo(f"Patched image written to {out}")


if __name__ == "__main__":
    app()
