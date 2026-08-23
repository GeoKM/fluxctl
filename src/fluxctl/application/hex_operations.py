"""Read-only and editable HEX/ASCII transformations for frontends."""
from __future__ import annotations

from typing import Optional


def format_hex_dump(data: bytes, *, width: int = 16, max_bytes: Optional[int] = None) -> str:
    if width <= 0:
        raise ValueError("Hex dump width must be positive")
    shown = data[:max_bytes] if max_bytes is not None else data
    lines: list[str] = []
    for offset in range(0, len(shown), width):
        chunk = shown[offset : offset + width]
        hex_bytes = " ".join(f"{byte:02X}" for byte in chunk)
        padded_hex = hex_bytes.ljust(width * 3 - 1)
        ascii_bytes = "".join(chr(byte) if 32 <= byte < 127 else "." for byte in chunk)
        lines.append(f"{offset:08X}  {padded_hex}  |{ascii_bytes}|")
    if max_bytes is not None and len(data) > max_bytes:
        lines.append(f"... truncated, showing {max_bytes:,} of {len(data):,} bytes")
    return "\n".join(lines)


def parse_hex_dump_text(text: str, *, expected_size: Optional[int] = None) -> bytes:
    payload = bytearray()
    expected_offset = 0
    parsed_any = False
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("... truncated"):
            continue
        if "|" in line:
            line = line.split("|", 1)[0].rstrip()
        parts = line.split()
        try:
            offset = int(parts[0], 16)
        except (IndexError, ValueError) as exc:
            raise ValueError("Invalid hex dump offset") from exc
        if offset != expected_offset:
            raise ValueError(f"Hex dump offset jumps from {expected_offset:08X} to {offset:08X}")
        for token in parts[1:]:
            if len(token) != 2:
                raise ValueError(f"Invalid hex byte token: {token!r}")
            try:
                payload.append(int(token, 16))
            except ValueError as exc:
                raise ValueError(f"Invalid hex byte token: {token!r}") from exc
        expected_offset += len(parts) - 1
        parsed_any = True
    if not parsed_any:
        raise ValueError("No hex bytes found in edited dump")
    if expected_size is not None and len(payload) != expected_size:
        raise ValueError(f"Edited data is {len(payload)} bytes; expected {expected_size} bytes")
    return bytes(payload)


def apply_ascii_hex_dump_edits(text: str, original_data: bytes, *, width: int = 16) -> bytes:
    if width <= 0:
        raise ValueError("Hex dump width must be positive")
    payload = bytearray(original_data)
    expected_offset = 0
    parsed_any = False
    for raw_line in text.splitlines():
        if not raw_line or raw_line.startswith("... truncated"):
            continue
        marker = raw_line.find("|")
        closing_marker = raw_line.find("|", marker + 1)
        if marker < 0 or closing_marker < 0 or raw_line[closing_marker + 1 :].strip():
            raise ValueError("ASCII edit requires a complete HEX dump line")
        prefix_parts = raw_line[:marker].split()
        try:
            offset = int(prefix_parts[0], 16)
        except (IndexError, ValueError) as exc:
            raise ValueError("Invalid hex dump offset") from exc
        if offset != expected_offset:
            raise ValueError(f"Hex dump offset jumps from {expected_offset:08X} to {offset:08X}")
        row_size = min(width, len(original_data) - offset)
        ascii_text = raw_line[marker + 1 : closing_marker]
        if row_size <= 0:
            raise ValueError(f"Hex dump offset {offset:08X} is beyond the edited data")
        if len(ascii_text) != row_size:
            raise ValueError(
                f"ASCII column at {offset:08X} contains {len(ascii_text)} characters; expected {row_size}"
            )
        for index, character in enumerate(ascii_text):
            source_byte = original_data[offset + index]
            rendered = chr(source_byte) if 32 <= source_byte < 127 else "."
            if character == rendered:
                continue
            if not 32 <= ord(character) < 127:
                raise ValueError("ASCII edits must use printable 7-bit characters; edit other bytes as HEX")
            payload[offset + index] = ord(character)
        expected_offset += row_size
        parsed_any = True
    if not parsed_any or expected_offset != len(original_data):
        raise ValueError("Edited ASCII data does not cover the complete original buffer")
    return bytes(payload)


__all__ = ["format_hex_dump", "parse_hex_dump_text", "apply_ascii_hex_dump_edits"]
