"""Minimal Commodore GCR decoder."""
from __future__ import annotations

from typing import Dict, List, Sequence

from ..exceptions import FluxDecodeError
from ..models import BitDecodeMetrics, Bitstream, RevolutionFlux
from ..plugins import PluginInfo, registry
from . import Decoder

# 4-to-5 bit mapping used by Commodore 1541
GCR_TABLE: Dict[int, int] = {
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


class GCRDecoder(Decoder):
    """Translate flux intervals into Commodore GCR bitstreams."""

    def __init__(self, cell_ns: float = 4000.0) -> None:
        self.cell_ns = cell_ns

    def _intervals_to_bits(self, intervals_ns: Sequence[int]) -> List[int]:
        bits: List[int] = []
        for interval in intervals_ns:
            if interval <= 0:
                continue
            cells = max(1, round(interval / self.cell_ns))
            if cells == 1:
                bits.append(1)
            else:
                bits.extend([0] * (cells - 1))
                bits.append(1)
        return bits

    def decode_revolution(self, rev: RevolutionFlux) -> Bitstream:
        if not rev.interval_ns:
            raise FluxDecodeError("No flux data available for GCR decoding")

        bits = self._intervals_to_bits(rev.interval_ns)
        decoded_bits: List[int] = []
        confidence_hits = 0
        for idx in range(0, len(bits) - 4, 5):
            code = 0
            for offset in range(5):
                code = (code << 1) | bits[idx + offset]
            if code in GCR_TABLE:
                nibble = GCR_TABLE[code]
                decoded_bits.extend([(nibble >> shift) & 1 for shift in range(3, -1, -1)])
                confidence_hits += 1
            else:
                decoded_bits.extend([0, 0, 0, 0])
        confidence = confidence_hits / max(1, (len(bits) // 5))
        if confidence == 0 and bits:
            confidence = 0.1
        metrics = BitDecodeMetrics(pll_lock_score=confidence, rpm_estimate=None, confidence=confidence)
        return Bitstream(bits=decoded_bits, metrics=metrics, source_revs=[rev.index])


gcr_decoder = GCRDecoder()
registry.register_encoding(
    "gcr",
    PluginInfo(
        name="GCR Decoder",
        version="0.1",
        entry=gcr_decoder,
        description="Commodore 1541 GCR decoder",
    ),
)
