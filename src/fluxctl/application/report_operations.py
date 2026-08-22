"""Reporting operations exposed to Fluxctl frontends.

These wrappers are intentionally small during the migration. The report
implementation will move here after callers stop depending on the monolithic
Studio service module.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from ..output import atomic_write_text
from ..reports.map import render_svg
from ..reports.qc import write_qc_report_json


def build_qc_for_image(path: Path, layout_id: Optional[str], encoding: str):
    from .. import studio_services

    return studio_services.build_qc_for_image(path, layout_id, encoding)


def build_disk_map_for_image(path: Path, layout_id: Optional[str], encoding: str, map_view: str):
    from .. import studio_services

    return studio_services.build_disk_map_for_image(path, layout_id, encoding, map_view)


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
