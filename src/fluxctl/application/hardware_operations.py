"""Hardware imaging operations shared by Fluxctl frontends."""
from __future__ import annotations

from pathlib import Path
from typing import Optional


def _services():
    from .. import studio_services

    return studio_services


def _override(name: str):
    candidate = getattr(_services(), name)
    return candidate if getattr(candidate, "__module__", "") != "fluxctl.studio_services" else None


def greaseweazle_status():
    override = _override("greaseweazle_status")
    return override() if override is not None else _services()._legacy_greaseweazle_status()


def greaseweazle_formats():
    override = _override("greaseweazle_formats")
    return override() if override is not None else _services()._legacy_greaseweazle_formats()


def read_disk_with_greaseweazle(
    output: Path,
    *,
    drive: str = "A",
    gw_format: str = "",
    tracks: str = "",
    revs: Optional[int] = None,
    overwrite: bool = False,
):
    override = _override("read_disk_with_greaseweazle")
    if override is not None:
        return override(output, drive=drive, gw_format=gw_format, tracks=tracks, revs=revs, overwrite=overwrite)
    return _services()._legacy_read_disk_with_greaseweazle(
        output, drive=drive, gw_format=gw_format, tracks=tracks, revs=revs, overwrite=overwrite
    )
