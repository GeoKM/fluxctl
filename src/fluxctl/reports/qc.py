"""QC report builder adhering to qc.v1 schema."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

from ..models import QCReport, Sector, TrackSectors


def build_qc_report(
    tool_version: str,
    input_path: Path,
    input_sha256: str,
    scp_meta: dict,
    layout_id: Optional[str],
    rev_policy: str,
    track_sector_data: List[TrackSectors],
    min_confidence: float,
) -> QCReport:
    sectors_flat: List[Sector] = [s for ts in track_sector_data for s in ts.sectors]
    sectors_good = len([s for s in sectors_flat if s.state == "good"])
    sectors_weak = len([s for s in sectors_flat if s.state == "weak"])
    sectors_bad = len([s for s in sectors_flat if s.state == "bad"])
    sectors_missing = len([s for s in sectors_flat if s.state == "missing"])
    crc_failures = len([s for s in sectors_flat if not s.crc_ok])
    summary = {
        "overall_confidence": min(1.0, max([s.confidence for s in sectors_flat], default=0.0)),
        "status": "ok" if crc_failures == 0 else "warning",
        "tracks_total_expected": None,
        "tracks_analyzed": len(track_sector_data),
        "sectors_expected_total": None,
        "sectors_found_total": len(sectors_flat),
        "sectors_good": sectors_good,
        "sectors_weak": sectors_weak,
        "sectors_bad": sectors_bad,
        "sectors_missing": sectors_missing,
        "crc_failures": crc_failures,
    }
    track_metrics = [
        {
            "track": ts.track,
            "side": ts.side,
            "revolutions": 0,
            "rpm_estimates": [],
            "rpm_mean": None,
            "rpm_stddev": None,
            "index_jitter_ms": None,
            "flux_dropout_events": 0,
            "pll_lock_score": None,
            "sector_count_found": len(ts.sectors),
            "sector_count_expected": None,
            "crc_failures": len([s for s in ts.sectors if not s.crc_ok]),
            "weak_sectors": len([s for s in ts.sectors if s.state == "weak"]),
            "confidence_mean": sum(s.confidence for s in ts.sectors) / max(len(ts.sectors), 1),
        }
        for ts in track_sector_data
    ]
    sector_table = [
        {
            "track": s.track,
            "side": s.side,
            "sector_id": s.sector_id,
            "size": s.size,
            "crc_ok": s.crc_ok,
            "confidence": s.confidence,
            "state": s.state,
            "source_revolutions": s.source_revolutions,
        }
        for s in sectors_flat
    ]
    return QCReport(
        schema_version="qc.v1",
        tool={"name": "fluxctl", "version": tool_version},
        input={"path": str(input_path), "sha256": input_sha256, "size_bytes": input_path.stat().st_size},
        scp=scp_meta,
        analysis_params={
            "encoding_selected": "mfm" if layout_id else None,
            "layout_selected": layout_id,
            "rev_policy": rev_policy,
            "min_confidence": min_confidence,
        },
        summary=summary,
        track_metrics=track_metrics,
        sector_table=sector_table,
        evidence=[f"layout={layout_id}" if layout_id else "no layout"],
    )


def write_qc_report(report: QCReport, out_path: Path) -> None:
    payload = report.__dict__
    payload["tool"] = report.tool
    out_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
