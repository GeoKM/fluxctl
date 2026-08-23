"""Text and HEX representations used by Fluxctl frontends."""
from __future__ import annotations

from typing import Optional


def _services():
    from .. import studio_services

    return studio_services


def format_hex_dump(data: bytes, *, width: int = 16, max_bytes: Optional[int] = None) -> str:
    return _services()._legacy_format_hex_dump(data, width=width, max_bytes=max_bytes)


def parse_hex_dump_text(text: str, *, expected_size: Optional[int] = None) -> bytes:
    return _services()._legacy_parse_hex_dump_text(text, expected_size=expected_size)


def apply_ascii_hex_dump_edits(text: str, original_data: bytes, *, width: int = 16) -> bytes:
    return _services()._legacy_apply_ascii_hex_dump_edits(text, original_data, width=width)
