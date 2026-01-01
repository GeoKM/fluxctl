"""Fluxctl command line interface."""
from __future__ import annotations

import json
from functools import wraps
from pathlib import Path
from typing import Optional

import typer

from .decoding.mfm import mfm_decoder
from .exceptions import FluxDecodeError, FluxctlError
from .exporters.img import export_img
from .exporters.imd import export_imd
from .filesystems.fat12 import extract_fat12
from .layouts.loader import ensure_layout_loaded, load_builtin_layouts
from .models import CandidateFormat
from .plugins import registry
from .reports.map import build_map_json, write_map_outputs
from .reports.qc import build_qc_report, write_qc_report
from .scp import parse_scp, sha256_file
from .sector.models import TrackSectors
from .sector.reconstruct import build_track_sectors, reconstruct_track

app = typer.Typer(add_completion=False, help="Fluxctl modular SCP toolkit")


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
    """Run lightweight detection and print candidate layouts."""
    load_builtin_layouts()
    candidates = [
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
    typer.echo(json.dumps([c.__dict__ for c in candidates], indent=2))


def _decode_tracks(path: Path, layout_id: str, limit_tracks: Optional[int] = None) -> list[TrackSectors]:
    scp = parse_scp(path)
    layout = ensure_layout_loaded(layout_id)
    track_data: list[TrackSectors] = []
    decoder = _get_decoder(layout.encoding)
    for ts in scp.tracks[: limit_tracks or None]:
        if not ts.revolutions:
            continue
        bitstream = decoder.decode_revolution(ts.revolutions[0])
        track_data.append(
            reconstruct_track(
                bitstream,
                cylinder=ts.track,
                head=ts.side,
                expected_sectors=layout.sectors_per_track,
            )
        )
    return track_data


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
    layout: str = typer.Option(..., "--layout"),
    out: Path = typer.Option(Path("qc.json"), "--out"),
    rev_policy: str = typer.Option("best_crc"),
    min_confidence: float = typer.Option(0.5, "--min-confidence"),
):
    scp = parse_scp(path)
    layout_desc = ensure_layout_loaded(layout)
    track_data = _decode_tracks(path, layout)
    report = build_qc_report(
        tool_version="0.1.0",
        input_path=path,
        input_sha256=sha256_file(path),
        scp_meta={
            "version": scp.version,
            "drive_type": None,
            "revolutions_per_track": scp.revolutions_per_track,
            "timebase_ns": scp.timebase_ns,
            "tracks_present": len(scp.tracks),
        },
        layout_id=layout_desc.layout_id,
        rev_policy=rev_policy,
        track_sector_data=track_data,
        min_confidence=min_confidence,
    )
    write_qc_report(report, out)
    typer.echo(f"Wrote {out}")


@app.command()
@_handle_cli_errors
def map(
    path: Path = typer.Argument(..., exists=True, readable=True),
    layout: str = typer.Option(..., "--layout"),
    json_out: Optional[Path] = typer.Option(None, "--json"),
    svg: Optional[Path] = typer.Option(None, "--svg"),
    ascii: bool = typer.Option(False, "--ascii"),
):
    layout_desc = ensure_layout_loaded(layout)
    track_data = _decode_tracks(path, layout)
    map_json = build_map_json(layout_desc, track_data)
    write_map_outputs(map_json, ascii_out=ascii, json_path=json_out, svg_path=svg)


@app.command()
@_handle_cli_errors
def convert(
    path: Path = typer.Argument(..., exists=True, readable=True),
    layout: str = typer.Option(..., "--layout"),
    to: str = typer.Option(..., "--to", help="img or imd"),
    out: Path = typer.Option(..., "--out"),
    force: bool = typer.Option(False, "--force"),
):
    layout_desc = ensure_layout_loaded(layout)
    track_data = _decode_tracks(path, layout)
    provenance = {
        "input_path": str(path),
        "input_sha256": sha256_file(path),
        "tool": "fluxctl",
        "version": "0.1.0",
    }
    if to == "img":
        export_img(track_data, layout_desc, out, provenance)
    elif to == "imd":
        export_imd(track_data, layout_desc, out, provenance, force=force)
    else:
        raise typer.BadParameter("Unsupported exporter")
    typer.echo(f"Wrote {out}")


@app.command()
@_handle_cli_errors
def extract(
    path: Path = typer.Argument(..., exists=True, readable=True),
    layout: str = typer.Option(..., "--layout"),
    fs: str = typer.Option("fat12", "--fs"),
    out_dir: Path = typer.Option(Path("extract"), "--out"),
):
    layout_desc = ensure_layout_loaded(layout)
    track_data = _decode_tracks(path, layout)
    if fs != "fat12":
        raise typer.BadParameter("Only FAT12 is supported in MVP")
    extract_fat12(track_data, layout_desc.sector_size, out_dir)
    provenance = {
        "input_path": str(path),
        "input_sha256": sha256_file(path),
        "fs": fs,
        "layout": layout,
        "tool": "fluxctl",
        "version": "0.1.0",
    }
    (out_dir / "provenance.json").write_text(json.dumps(provenance, indent=2), encoding="utf-8")
    typer.echo(f"Extracted to {out_dir}")


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
    provenance = {
        "input_path": str(path),
        "input_sha256": sha256_file(path),
        "tool": "fluxctl",
        "version": "0.1.0",
        "patched_sector": write_sector,
    }
    export_img(track_data, layout_desc, out, provenance)
    out.with_suffix(out.suffix + ".patchlog.json").write_text(
        json.dumps({"patched": write_sector}, indent=2), encoding="utf-8"
    )
    typer.echo(f"Patched image written to {out}")


if __name__ == "__main__":
    app()
