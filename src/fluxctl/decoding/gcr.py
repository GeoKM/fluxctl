"""Minimal Commodore GCR decoder."""
from __future__ import annotations

from typing import List, Sequence

from ..encoding.gcr import GCR_DECODE_5TO4
from ..exceptions import FluxDecodeError
from ..models import BitDecodeMetrics, Bitstream, RevolutionFlux
from ..plugins import PluginInfo, registry
from . import Decoder


class GCRDecoder(Decoder):
    """Translate flux intervals into Commodore GCR bitstreams."""

    encoding = "gcr"

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

    def _estimate_confidence(self, bits: List[int]) -> float:
        if not bits:
            return 0.0
        valid = 0
        total = 0
        for idx in range(0, len(bits) - 4, 5):
            code = 0
            for offset in range(5):
                code = (code << 1) | bits[idx + offset]
            total += 1
            if code in GCR_DECODE_5TO4:
                valid += 1
        confidence = valid / max(total, 1)
        return confidence if confidence > 0 else 0.1

    def decode_revolution(self, rev: RevolutionFlux) -> Bitstream:
        if not rev.interval_ns:
            raise FluxDecodeError("No flux data available for GCR decoding")

        bits = self._intervals_to_bits(rev.interval_ns)
        confidence = self._estimate_confidence(bits)
        metrics = BitDecodeMetrics(pll_lock_score=confidence, rpm_estimate=None, confidence=confidence)
        return Bitstream(bits=bits, metrics=metrics, source_revs=[rev.index])


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
