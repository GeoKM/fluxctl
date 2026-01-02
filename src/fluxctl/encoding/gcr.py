"""Utilities for Commodore-style 4-to-5 GCR coding."""
from __future__ import annotations

from typing import List, Sequence, Tuple

# Canonical 4->5 mapping used by Commodore drives
GCR_DECODE_5TO4: dict[int, int] = {
    0b01010: 0x0,
    0b01011: 0x1,
    0b10010: 0x2,
    0b10011: 0x3,
    0b01110: 0x4,
    0b01111: 0x5,
    0b10110: 0x6,
    0b10111: 0x7,
    0b01001: 0x8,
    0b11001: 0x9,
    0b11010: 0xA,
    0b11011: 0xB,
    0b01101: 0xC,
    0b11101: 0xD,
    0b11110: 0xE,
    0b10101: 0xF,
}

# Reverse lookup for encoding nibbles back to 5-bit symbols
GCR_ENCODE_4TO5: List[int] = [0] * 16
for code, nibble in GCR_DECODE_5TO4.items():
    GCR_ENCODE_4TO5[nibble] = code


def decode_gcr_symbols_to_bytes(symbols: List[int]) -> Tuple[bytes, int]:
    """Decode a list of 5-bit symbols into a byte string.

    Two symbols form a byte (high nibble first). Invalid symbols are replaced
    with ``0x0`` in the output and counted in the returned error tally.
    """

    decoded: List[int] = []
    errors = 0
    # Process in pairs: high nibble then low nibble
    for hi, lo in zip(symbols[0::2], symbols[1::2]):
        if hi not in GCR_DECODE_5TO4:
            errors += 1
            high_nibble = 0
        else:
            high_nibble = GCR_DECODE_5TO4[hi]
        if lo not in GCR_DECODE_5TO4:
            errors += 1
            low_nibble = 0
        else:
            low_nibble = GCR_DECODE_5TO4[lo]
        decoded.append((high_nibble << 4) | low_nibble)
    return bytes(decoded), errors


def extract_gcr_symbols_from_bitstream(bits: Sequence[int], start_bit: int, count_symbols: int) -> List[int]:
    """Return ``count_symbols`` 5-bit symbols from ``bits`` starting at ``start_bit``.

    Bits are consumed MSB-first within each symbol. Partial symbols at the end
    of the stream are discarded if fewer than 5 bits remain.
    """

    symbols: List[int] = []
    cursor = start_bit
    for _ in range(count_symbols):
        if cursor + 5 > len(bits):
            break
        value = 0
        for bit in bits[cursor : cursor + 5]:
            value = (value << 1) | int(bit)
        symbols.append(value)
        cursor += 5
    return symbols


__all__ = [
    "GCR_DECODE_5TO4",
    "GCR_ENCODE_4TO5",
    "decode_gcr_symbols_to_bytes",
    "extract_gcr_symbols_from_bitstream",
]
