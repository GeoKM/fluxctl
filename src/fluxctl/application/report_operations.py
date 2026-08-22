"""Reporting operations exposed to Fluxctl frontends.

These wrappers are intentionally small during the migration. The report
implementation will move here after callers stop depending on the monolithic
Studio service module.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional


def build_qc_for_image(path: Path, layout_id: Optional[str], encoding: str):
    from .. import studio_services

    return studio_services.build_qc_for_image(path, layout_id, encoding)


def build_disk_map_for_image(path: Path, layout_id: Optional[str], encoding: str, map_view: str):
    from .. import studio_services

    return studio_services.build_disk_map_for_image(path, layout_id, encoding, map_view)

