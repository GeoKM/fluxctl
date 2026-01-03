"""Magnetic Flux Modulation (MFM) decoder."""
from __future__ import annotations

from math import ceil, fabs
from typing import List, Sequence

from ..exceptions import FluxDecodeError
from ..models import BitDecodeMetrics, Bitstream, RevolutionFlux
from ..plugins import PluginInfo, registry
from . import Decoder


class MFMDecoder(Decoder):
    """Very small MFM decoder based on timing heuristics.

    This implementation assumes a fixed data rate of 250 kbps (4 µs bit cells)
    and uses a lightweight phase lock approximation. Flux transition timings
    are translated into clock/data bits by estimating how many bit cells elapsed
    between transitions and inserting zero bits for skipped cells. A transition
    within a single cell is treated as a data ``1`` while longer gaps produce
    runs of ``0`` bits followed by a ``1`` that coincides with the observed
    transition.

    The goal is to provide a deterministic, readable decoder for tests while
    keeping the door open for a more sophisticated PLL in future iterations.
    """

    def __init__(self, cell_ns: float = 4000.0, max_cells: int = 64) -> None:
        self.cell_ns = cell_ns
        self.max_cells = max_cells

    def _intervals_to_bits(self, intervals_ns: Sequence[int]) -> List[int]:
        bits: List[int] = []
        for interval in intervals_ns:
            if interval <= 0:
                continue
            cells = max(1, min(int(ceil(interval / self.cell_ns)), self.max_cells))
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

        bits = self._intervals_to_bits(intervals_ns)
        # Estimate PLL stability as the mean fractional deviation from the ideal
        # bit cell. This is a coarse measure but yields a reproducible confidence
        # score for testing.
        deviations = []
        for interval in intervals_ns:
            cells = max(1, round(interval / self.cell_ns))
            expected = cells * self.cell_ns
            deviations.append(fabs(interval - expected) / expected if expected else 0.0)
        pll_lock = 1.0 - min(1.0, sum(deviations) / len(deviations)) if deviations else 0.0
        metrics = BitDecodeMetrics(pll_lock_score=pll_lock, rpm_estimate=None, confidence=pll_lock)
        return Bitstream(bits=bits, metrics=metrics, source_revs=[rev.index])


mfm_decoder = MFMDecoder()
registry.register_encoding(
    "mfm",
    PluginInfo(
        name="MFM Decoder",
        version="0.1",
        entry=mfm_decoder,
        description="Magnetic Flux Modulation bitstream decoder",
    ),
)
