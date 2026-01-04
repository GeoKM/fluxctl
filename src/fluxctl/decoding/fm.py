"""Frequency Modulation (FM) decoder."""
from __future__ import annotations

from math import fabs
from typing import List, Sequence

from ..exceptions import FluxDecodeError
from ..models import BitDecodeMetrics, Bitstream, RevolutionFlux
from ..plugins import PluginInfo, registry
from . import Decoder


class FMDecoder(Decoder):
    """Basic FM decoder using a fixed bit-cell time."""

    encoding = "fm"

    def __init__(self, cell_ns: float = 4000.0, max_cells: int = 64) -> None:
        self.cell_ns = cell_ns
        self.max_cells = max_cells

    def _intervals_to_bits(self, intervals_ns: Sequence[int], cell_ns: float) -> List[int]:
        bits: List[int] = []
        for interval in intervals_ns:
            if interval <= 0:
                continue
            cells = max(1, min(int(round(interval / cell_ns)), self.max_cells))
            if cells == 1:
                bits.append(1)
            else:
                bits.extend([0] * (cells - 1))
                bits.append(1)
        return bits

    def decode_revolution(self, rev: RevolutionFlux) -> Bitstream:
        if not rev.interval_ns:
            raise FluxDecodeError("No flux intervals supplied for revolution")

        intervals_ns = list(rev.interval_ns)
        mean_interval = sum(intervals_ns) / len(intervals_ns)
        if mean_interval > 1_000_000:
            intervals_ns = [max(1, int(val / 1000)) for val in intervals_ns]

        bits = self._intervals_to_bits(intervals_ns, self.cell_ns)

        deviations = []
        for interval in intervals_ns:
            cells = max(1, round(interval / self.cell_ns))
            expected = cells * self.cell_ns
            deviations.append(fabs(interval - expected) / expected if expected else 0.0)
        pll_lock = 1.0 - min(1.0, sum(deviations) / len(deviations)) if deviations else 0.0
        metrics = BitDecodeMetrics(pll_lock_score=pll_lock, rpm_estimate=None, confidence=pll_lock)
        return Bitstream(bits=bits, metrics=metrics, source_revs=[rev.index])


fm_decoder = FMDecoder()
registry.register_encoding(
    "fm",
    PluginInfo(
        name="FM Decoder",
        version="0.1",
        entry=fm_decoder,
        description="Frequency Modulation bitstream decoder",
    ),
)
