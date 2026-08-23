"""Diagnostic operations shared by Fluxctl frontends."""
from __future__ import annotations

from pathlib import Path
import json

from .image_operations import doctor_report as _doctor_report


def summarize_image(path: Path):
    """Probe an image and return the Studio summary model."""

    from .. import studio_services

    return studio_services.summarize_image(path)


def doctor_report(hxcfe: Path | None = None) -> dict:
    """Run the installation/runtime diagnostic checks."""

    return _doctor_report(hxcfe)


def provenance_json(path: Path) -> dict:
    """Read a provenance sidecar for display or logging."""

    return json.loads(path.read_text(encoding="utf-8"))
