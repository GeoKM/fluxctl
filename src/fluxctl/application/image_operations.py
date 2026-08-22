"""Stable image-operation boundary for CLI and Studio.

The implementations are still hosted by the legacy CLI module during the
incremental refactor.  Keeping this compatibility bridge here lets callers
depend on application operations rather than private CLI helpers while the
implementation is moved in smaller, testable steps.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional


def _cli_module():
    # Lazy import avoids making the application layer depend on Typer during
    # module import and prevents a CLI/application import cycle.
    from .. import cli

    return cli


def get_decoder(encoding: str):
    return _cli_module()._get_decoder(encoding)


def prepare_image(path: Path, layout_id: Optional[str], encoding: str):
    return _cli_module()._prepare_image(path, layout_id, encoding)


def probe_flat_image(path: Path):
    return _cli_module()._probe_flat_image(path)


def prefix_track_count_for_size(layout, data_len: int):
    return _cli_module()._prefix_track_count_for_size(layout, data_len)


def track_in_range(range_expr: str, track: int) -> bool:
    return _cli_module()._track_in_range(range_expr, track)


def maybe_hxc_hint(path: Path, hxcfe: Optional[Path]):
    return _cli_module()._maybe_hxc_hint(path, hxcfe)


def doctor_report(hxcfe: Optional[Path] = None) -> dict:
    return _cli_module()._doctor_report(hxcfe)

