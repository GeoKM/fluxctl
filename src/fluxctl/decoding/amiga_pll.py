"""Lightweight Amiga-tuned PLL/MFM demodulator.

Produces clock-stripped data bits and a confidence score so the Amiga sector
parser can operate without an external decoder such as Greaseweazle.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Sequence

from ..exceptions import FluxDecodeError
from ..models import BitDecodeMetrics, Bitstream, RevolutionFlux


@dataclass
class PllConfig:
    cell_ns: float = 2000.0  # Amiga DD: 250 kbps -> 4 µs per full MFM bitcell, but clock/data -> 2 µs per data bit
    period_adj_pct: float = 10.0
    phase_adj_pct: float = 60.0
    max_cells: int = 8


class AmigaPLLDecoder:
    """Minimal PLL that outputs one data bit per nominal half-cell."""

    def __init__(self, cfg: PllConfig | None = None) -> None:
        self.cfg = cfg or PllConfig()

    def _intervals_to_bits(self, intervals_ns: Sequence[int]) -> tuple[List[int], float]:
        cell = self.cfg.cell_ns
        bits: List[int] = []
        phase_err_acc = 0.0
        lock_samples = 0
        for interval in intervals_ns:
            if interval <= 0:
                continue
            cells_exact = interval / cell
            cells = max(1, min(int(round(cells_exact)), self.cfg.max_cells))
            # phase error estimate
            phase_err = abs(cells_exact - cells)
            phase_err_acc += phase_err
            lock_samples += 1
            # model MFM: every interval is a transition -> data bit 1, with (cells-1) zero bits inserted
            bits.extend([0] * (cells - 1))
            bits.append(1)
            # adjust period slightly
            if phase_err > 0 and cells <= self.cfg.max_cells:
                adj = (cells_exact - cells) * (self.cfg.period_adj_pct / 100.0)
                cell += cell * adj
        pll_lock = 1.0 - min(1.0, (phase_err_acc / lock_samples) if lock_samples else 1.0)
        return bits, pll_lock

    def decode_revolution(self, rev: RevolutionFlux) -> Bitstream:
        if not rev.interval_ns:
            raise FluxDecodeError("No flux intervals supplied for revolution")
        bits, lock = self._intervals_to_bits(rev.interval_ns)
        metrics = BitDecodeMetrics(pll_lock_score=lock, rpm_estimate=None, confidence=lock)
        return Bitstream(bits=bits, metrics=metrics, source_revs=[rev.index])


amiga_pll_decoder = AmigaPLLDecoder()

__all__ = ["AmigaPLLDecoder", "amiga_pll_decoder"]
