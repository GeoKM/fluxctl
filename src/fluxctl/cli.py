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
from .exceptions import ExportError, FluxDecodeError, FluxctlError
from .exporters import load_builtin_exporters
from .filesystems import Filesystem, RawSectorImage, TrackSectorImage, load_builtin_filesystems
from .layouts.loader import ensure_layout_loaded, load_builtin_layouts
from .models import CandidateFormat, ProvenanceRecord
from .plugins import registry
from .provenance import write_provenance
from .reports.map import build_disk_map, render_ascii, render_svg
from .reports.qc import build_qc_report, write_qc_report_json, write_qc_report_text
from .scp import parse_scp, sha256_file
from .sector.models import TrackSectors
from .sector.reconstruct import build_track_sectors, reconstruct_track

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


@app.command()
@_handle_cli_errors
def info(path: Path = typer.Argument(..., exists=True, readable=True)) -> None:
    """Print basic SCP information."""
    scp = parse_scp(path)
    typer.echo(f"SCP version: {scp.version}")
    typer.echo(f"Tracks parsed: {len(scp.tracks)}")
    typer.echo(f"Revolutions per track: {scp.revolutions_per_track}")


@app.command()
@_handle_cli_errors
def probe(path: Path = typer.Argument(..., exists=True, readable=True)) -> None:
    """Run lightweight detection and print candidate layouts and decoders."""
    load_builtin_decoders()
    load_builtin_layouts()
    candidates: list[CandidateFormat] = [
        CandidateFormat(
            candidate_id=layout_id,
            encoding=descriptor.encoding,
            layout_id=descriptor.layout_id,
            filesystem="fat12",
            score=0.6,
            evidence=["builtin layout"],
        )
        for layout_id, descriptor in registry.layout.items()
    ]
    for encoding_key, plugin in registry.encoding.items():
        candidates.append(
            CandidateFormat(
                candidate_id=f"{encoding_key}_decoder",
                encoding=encoding_key,
                layout_id=None,
                filesystem=None,
                score=0.3,
                evidence=[f"builtin decoder: {plugin.name}"],
            )
        )
    typer.echo(json.dumps([c.__dict__ for c in candidates], indent=2))


def _decode_tracks(
    path: Path, layout_id: Optional[str], limit_tracks: Optional[int] = None, encoding: Optional[str] = None
) -> list[TrackSectors]:
    scp = parse_scp(path)
    layout = ensure_layout_loaded(layout_id) if layout_id else None
    track_data: list[TrackSectors] = []
    decoder = _get_decoder(layout.encoding if layout else (encoding or "mfm"))
    for ts in scp.tracks[: limit_tracks or None]:
        if not ts.revolutions:
            continue
        bitstream = decoder.decode_revolution(ts.revolutions[0])
        track_data.append(
            reconstruct_track(
                bitstream,
                cylinder=ts.track,
                head=ts.side,
                expected_sectors=layout.sectors_per_track if layout else None,
            )
        )
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
    if path.suffix.lower() == ".img":
        return RawSectorImage(path.read_bytes())
    if path.suffix.lower() == ".scp":
        track_data = _decode_tracks(path, layout_id, encoding=encoding)
        return TrackSectorImage(track_data)
    if layout_id:
        track_data = _decode_tracks(path, layout_id, encoding=encoding)
        return TrackSectorImage(track_data)
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
    encoding: str = typer.Option("mfm", "--encoding", help="Bitstream encoding for analysis"),
    json_out: Optional[Path] = typer.Option(None, "--json-out", help="Write QC results to a JSON file"),
    text_out: Optional[Path] = typer.Option(None, "--text-out", help="Write QC results to a text file"),
    prov_out: Optional[Path] = typer.Option(None, "--prov-out", help="Override provenance output path"),
):
    """Assess image quality and emit QC reports."""

    scp = parse_scp(path)
    decoder = _get_decoder(encoding)
    report = build_qc_report(scp, decoder)

    targets: list[Path] = []
    if json_out:
        write_qc_report_json(report, json_out)
        targets.append(json_out)
    if text_out:
        write_qc_report_text(report, text_out)
        targets.append(text_out)
    if not targets:
        typer.echo(
            f"Analysed {len(report.tracks)} tracks; overall confidence {report.overall_confidence:.2f}; "
            f"missing tracks {report.missing_tracks}"
        )
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
            parameters={"encoding": encoding, "json_out": str(json_out or ""), "text_out": str(text_out or "")},
            plugins={"decoder": encoding},
            decoder=encoding,
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
    to: str = typer.Option(..., "--to", help="Exporter key (raw, imd)"),
    out: Path = typer.Option(..., "--out"),
    layout: Optional[str] = typer.Option(None, "--layout", help="Layout identifier for reconstruction"),
    encoding: str = typer.Option("mfm", "--encoding", help="Bitstream encoding for SCP sources"),
    prov_out: Optional[Path] = typer.Option(None, "--prov-out", help="Provenance sidecar output"),
):
    load_builtin_exporters()
    layout_desc = ensure_layout_loaded(layout) if layout else None
    decoder_used = layout_desc.encoding if layout_desc else encoding
    track_data: Optional[list[TrackSectors]] = None

    if path.suffix.lower() == ".scp":
        track_data = _decode_tracks(path, layout, encoding=decoder_used)
        image_obj = TrackSectorImage(track_data, bytes_per_sector=layout_desc.sector_size if layout_desc else None)
        if layout_desc:
            image_obj.set_geometry(layout_desc.sectors_per_track, layout_desc.sides)
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
