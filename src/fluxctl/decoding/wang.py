"""Wang OIS hard-sector helpers.

Flux-sector reconstruction is intentionally kept separate from the generic
IBM FM decoder.  Wang's CRC covers two sync bits and the data field, rather
than an IBM-style address mark and header.  The flat-image path can use the
layout without this decoder; SCP reconstruction will add a Wang-specific
parser once both splice windows have been validated.
"""
from __future__ import annotations

from ..exceptions import FluxDecodeError
from ..plugins import PluginInfo, registry


def wang_crc16(data: bytes, *, sync_bits: tuple[int, int] = (1, 1)) -> int:
    """Return the Wang OIS CRC for sync bits followed by ``data``.

    Wang uses polynomial ``0x8005``, an all-zero initial state, MSB-first
    processing, and stores the resulting word high byte first.  The sync bits
    are part of the CRC input but are not byte-packed.
    """

    crc = 0
    for bit in (*sync_bits, *(bit for byte in data for bit in _byte_bits(byte))):
        feedback = ((crc >> 15) & 1) ^ bit
        crc = (crc << 1) & 0xFFFF
        if feedback:
            crc ^= 0x8005
    return crc


def _byte_bits(value: int):
    for shift in range(7, -1, -1):
        yield (value >> shift) & 1


class WangFMDecoder:
    """Registry marker for Wang FM; sector parsing is format-specific."""

    encoding = "wang_fm"

    def decode_revolution(self, rev):
        raise FluxDecodeError("Wang OIS requires hard-sector-aware reconstruction")


wang_fm_decoder = WangFMDecoder()
registry.register_encoding(
    "wang_fm",
    PluginInfo(
        name="Wang OIS hard-sector FM Decoder",
        version="0.1",
        entry=wang_fm_decoder,
        description="Wang OIS/100 32-hole hard-sector FM decoder",
    ),
)
