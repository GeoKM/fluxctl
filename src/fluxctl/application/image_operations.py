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
    from ..decoding import load_builtin_decoders
    from ..decoding.mfm import mfm_decoder
    from ..exceptions import FluxDecodeError
    from ..plugins import registry

    load_builtin_decoders()
    if encoding == "mfm":
        return mfm_decoder
    plugin = registry.encoding.get(encoding)
    if plugin:
        return plugin.entry
    raise FluxDecodeError(f"Unknown encoding '{encoding}'")


def prepare_image(path: Path, layout_id: Optional[str], encoding: str):
    return _cli_module()._prepare_image(path, layout_id, encoding)


def probe_flat_image(path: Path):
    return _cli_module()._probe_flat_image(path)


def prefix_track_count_for_size(layout, data_len: int):
    if layout.sides != 1 or not layout.track_sectors or layout.sector_size <= 0:
        return None
    offset = 0
    for index, sectors in enumerate(layout.track_sectors):
        offset += sectors * layout.sector_size
        if offset == data_len:
            return index + 1
        if offset > data_len:
            return None
    return None


def track_in_range(range_expr: str, track: int) -> bool:
    if "-" in range_expr:
        start, end = range_expr.split("-", 1)
        try:
            return int(start) <= track <= int(end)
        except ValueError:
            return False
    try:
        return track == int(range_expr)
    except ValueError:
        return False


def maybe_hxc_hint(path: Path, hxcfe: Optional[Path]):
    return _cli_module()._maybe_hxc_hint(path, hxcfe)


def doctor_report(hxcfe: Optional[Path] = None) -> dict:
    return _cli_module()._doctor_report(hxcfe)
