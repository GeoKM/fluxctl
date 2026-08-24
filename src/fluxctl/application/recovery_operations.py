"""Explicit multi-revolution recovery operations shared by CLI and Studio."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from .. import __version__
from ..exceptions import ExportError, FluxDecodeError
from ..filesystems import TrackSectorImage
from ..layouts.loader import ensure_layout_loaded, load_builtin_layouts
from ..output import atomic_write_bytes, atomic_write_text
from ..scp import parse_scp, sha256_file
from ..sector.models import Sector, TrackSectors
from ..sector.reconstruct import build_track_sectors


@dataclass(frozen=True)
class RecoveryResult:
    output_path: Path
    manifest_path: Path
    report: dict[str, Any]


def _sector_summary(sector: Sector, revolution: int) -> dict[str, Any]:
    return {
        "revolution": revolution,
        "sector": sector.sector_id,
        "size": sector.size,
        "data_sha256": hashlib.sha256(sector.data).hexdigest() if sector.data else None,
        "has_data": bool(sector.data),
        "crc_ok": bool(sector.crc_ok),
        "deleted": bool(sector.deleted),
        "confidence": sector.confidence,
    }


def _candidate_quality(sector: Sector) -> tuple[int, int, float, int]:
    return (
        1 if sector.crc_ok else 0,
        1 if sector.data else 0,
        float(sector.confidence),
        len(sector.data),
    )


def _recover_tracks(
    path: Path,
    layout_id: str,
    encoding: str,
    policy: str,
    operation=None,
) -> tuple[list[TrackSectors], list[dict[str, Any]], dict[str, Any]]:
    """Decode each revolution independently and select sector candidates."""

    if path.suffix.lower() != ".scp":
        raise FluxDecodeError("Recovery requires an SCP flux capture with multiple revolutions")
    if policy not in {"strict-crc", "best-effort"}:
        raise ValueError("Recovery policy must be strict-crc or best-effort")

    load_builtin_layouts()
    layout = ensure_layout_loaded(layout_id)
    image = parse_scp(path)
    from .image_operations import get_decoder
    decoder = get_decoder(encoding or layout.encoding)
    recovered: list[TrackSectors] = []
    track_reports: list[dict[str, Any]] = []
    selected_count = 0
    rejected_count = 0
    missing_count = 0

    track_total = len(image.tracks)
    for track_index, track_flux in enumerate(image.tracks, start=1):
        if operation is not None:
            operation.checkpoint("track", track_index, track_total)
        if track_flux.track >= layout.tracks or track_flux.side >= layout.sides:
            continue
        expected = layout.expected_sectors_for_track(track_flux.track, track_flux.side)
        candidates: list[tuple[int, TrackSectors]] = []
        revolution_total = len(track_flux.revolutions)
        for revolution_index, revolution in enumerate(track_flux.revolutions, start=1):
            if operation is not None:
                operation.checkpoint("revolution", revolution_index, revolution_total)
            if not getattr(revolution, "interval_ns", None):
                continue
            try:
                if operation is not None:
                    operation.checkpoint("candidate decoder", revolution_index, revolution_total)
                candidate = build_track_sectors(
                    revolution,
                    decoder,
                    cylinder=track_flux.track,
                    head=track_flux.side,
                    expected_sectors=expected,
                    encoding=layout.encoding,
                )
            except Exception:
                if operation is not None:
                    operation.checkpoint("cancelled")
                continue
            candidates.append((int(getattr(revolution, "index", len(candidates))), candidate))

        by_sector: dict[int, list[tuple[int, Sector]]] = {}
        revolution_reports: list[dict[str, Any]] = []
        for revolution, candidate_track in candidates:
            revolution_reports.append({
                "revolution": revolution,
                "missing": candidate_track.missing,
                "weak": candidate_track.weak,
                "sectors": [_sector_summary(sector, revolution) for sector in candidate_track.sectors],
            })
            for sector in candidate_track.sectors:
                by_sector.setdefault(sector.sector_id, []).append((revolution, sector))

        selected: list[Sector] = []
        selections: list[dict[str, Any]] = []
        track_missing = 0
        base = int(layout.id_rules.get("sector_number_base", 1))
        for sector_id in range(base, base + expected):
            options = by_sector.get(sector_id, [])
            valid_options = [(rev, sector) for rev, sector in options if sector.data and sector.crc_ok]
            eligible = valid_options if policy == "strict-crc" else [(rev, sector) for rev, sector in options if sector.data]
            if not eligible:
                rejected_count += len(options)
                missing_count += 1
                track_missing += 1
                selections.append({
                    "sector": sector_id,
                    "selected_revolution": None,
                    "status": "missing",
                    "reason": "no CRC-valid candidate" if policy == "strict-crc" else "no populated candidate",
                    "candidate_revolutions": [rev for rev, _ in options],
                })
                continue
            selected_revolution, selected_sector = max(eligible, key=lambda item: _candidate_quality(item[1]))
            selected.append(selected_sector)
            selected_count += 1
            rejected_count += max(0, len(options) - 1)
            selections.append({
                "sector": sector_id,
                "selected_revolution": selected_revolution,
                "status": "selected",
                "reason": "CRC-valid candidate" if selected_sector.crc_ok else "best populated candidate",
                "candidate_revolutions": [rev for rev, _ in options],
                "selected": _sector_summary(selected_sector, selected_revolution),
            })

        recovered.append(TrackSectors(
            track=track_flux.track,
            head=track_flux.side,
            sectors=sorted(selected, key=lambda sector: sector.sector_id),
            missing=track_missing,
            weak=sum(1 for sector in selected if not sector.crc_ok),
        ))
        track_reports.append({
            "track": track_flux.track,
            "head": track_flux.side,
            "revolutions": revolution_reports,
            "selections": selections,
        })

    summary = {
        "tracks": len(recovered),
        "selected_sectors": selected_count,
        "missing_sectors": missing_count,
        "rejected_candidates": rejected_count,
        "policy": policy,
    }
    return recovered, track_reports, summary


def recover_image(
    path: Path,
    output: Path,
    manifest: Optional[Path],
    layout_id: str,
    encoding: str,
    policy: str,
    exporter_name: str = "raw",
    *,
    force: bool = False,
    operation=None,
) -> RecoveryResult:
    """Recover an SCP into a new image and write a decision manifest."""

    from .conversion_planner import ConversionContext, plan_conversion
    from ..decoding import load_builtin_decoders
    from ..exporters import load_builtin_exporters

    load_builtin_decoders()
    load_builtin_exporters()
    layout = ensure_layout_loaded(layout_id)
    effective_encoding = layout.encoding if encoding.lower() == "auto" else encoding
    source_hash = sha256_file(path)
    tracks, track_reports, summary = _recover_tracks(path, layout_id, effective_encoding, policy, operation=operation)
    if not tracks:
        raise FluxDecodeError("No recoverable tracks were decoded from the SCP")

    image = TrackSectorImage(tracks, bytes_per_sector=layout.sector_size)
    image.layout = layout
    image.set_geometry(
        layout.sectors_per_track,
        layout.sides,
        int(layout.id_rules.get("sector_number_base", 1)),
    )
    from ..plugins import registry
    plugin = registry.exporter.get(exporter_name)
    if plugin is None:
        raise ExportError(f"Unsupported recovery exporter '{exporter_name}'")
    filesystem = ""
    try:
        from ..filesystem_detection import detect_filesystem
        filesystem = detect_filesystem(image).primary or ""
    except Exception:
        pass
    plan = plan_conversion(
        ConversionContext.from_image(
            image,
            source_kind="scp",
            layout=layout,
            encoding=effective_encoding,
            filesystem=filesystem,
        ),
        exporter_name,
    )
    if not plan.allowed or not plugin.entry.supports(image):
        raise ExportError(plan.reason or f"Exporter '{exporter_name}' does not support recovered sectors")
    payload = plugin.entry.export(image)
    manifest_path = manifest or output.with_suffix(output.suffix + ".recovery.json")
    from ..output import validate_output_path
    validate_output_path(output, overwrite=force, source_paths=[path])
    validate_output_path(manifest_path, overwrite=force, source_paths=[path])
    atomic_write_bytes(output, payload, overwrite=force, source_paths=[path])
    source_unchanged = sha256_file(path) == source_hash
    report: dict[str, Any] = {
        "schema_version": "1",
        "tool": {"name": "fluxctl", "version": __version__},
        "operation": "recover",
        "input": {"path": str(path), "sha256": source_hash},
        "output": {"path": str(output), "sha256": hashlib.sha256(payload).hexdigest(), "format": exporter_name},
        "layout": layout_id,
        "encoding": effective_encoding,
        "policy": policy,
        "summary": summary,
        "tracks": track_reports,
        "source_unchanged": source_unchanged,
        "conversion_classification": plan.classification,
        "conversion_reason": plan.reason,
    }
    atomic_write_text(manifest_path, json.dumps(report, indent=2), overwrite=force, source_paths=[path])
    return RecoveryResult(output_path=output, manifest_path=manifest_path, report=report)


__all__ = ["RecoveryResult", "recover_image"]
