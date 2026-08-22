"""Diagnostic operations shared by Fluxctl frontends."""
from __future__ import annotations

from pathlib import Path


def summarize_image(path: Path):
    """Probe an image and return the Studio summary model."""

    from .. import studio_services

    return studio_services.summarize_image(path)
