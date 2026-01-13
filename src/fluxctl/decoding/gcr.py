"""Minimal Commodore GCR decoder."""
from __future__ import annotations

from typing import List, Sequence

from ..encoding.gcr import GCR_DECODE_5TO4
from ..exceptions import FluxDecodeError
from ..models import BitDecodeMetrics, Bitstream, RevolutionFlux
from ..plugins import PluginInfo, registry
from . import Decoder


def cell_ns_for_1541_track(track_1based: int) -> float:
    """Return the nominal bit cell time for a 1541 track.

    Commodore 1541 drives vary the rotation speed by zone to maintain roughly
    constant linear density. Tracks are 1-based in the physical format; callers
    using 0-based cylinder numbers should adjust accordingly.
    """

    zones = (
        (1, 17, 3250.0),
        (18, 24, 3500.0),
        (25, 30, 3750.0),
        (31, 40, 4000.0),
    )
    for start, end, cell_ns in zones:
        if start <= track_1based <= end:
            return cell_ns
    return zones[-1][2]


class GCRDecoder(Decoder):
    """Translate flux intervals into Commodore GCR bitstreams."""

    encoding = "gcr"

    def __init__(self, cell_ns: float = 4000.0) -> None:
        self.cell_ns = cell_ns

    def set_track(self, cylinder: int) -> None:
        """Update the PLL cell timing based on the target cylinder."""

        track_1based = cylinder + 1
        self.cell_ns = cell_ns_for_1541_track(track_1based)

    def _lowpass_merge(self, intervals_ns: Sequence[int], thresh_ns: float) -> List[float]:
        merged: List[float] = []
        i = 0
        n = len(intervals_ns)
        while i < n:
            t = intervals_ns[i]
            if t < thresh_ns and i + 1 < n:
                t += intervals_ns[i + 1]
                if merged:
                    merged[-1] += t
                else:
                    merged.append(t)
                i += 2
                continue
            merged.append(t)
            i += 1
        return merged

    def _intervals_to_bits(
        self, intervals_ns: Sequence[int], cell_ns: float, lowpass_ns: float = 2000.0
    ) -> List[int]:
        """Convert flux intervals to bitcells using a PLL-style sampler (GW-like)."""

        if not intervals_ns:
            return []

        merged = self._lowpass_merge(intervals_ns, lowpass_ns)

        clock = cell_ns
        clock_min = cell_ns * 0.9
        clock_max = cell_ns * 1.1
        period_adj = 0.05
        phase_adj = 0.60
        phase = 0.0
        bits: List[int] = []

        for interval in merged:
            if interval <= 0:
                continue
            phase += interval
            cells = max(1, int((phase + clock * 0.5) // clock))
            phase -= cells * clock
            bits.extend([0] * (cells - 1))
            bits.append(1)
            measured = interval / cells
            error = measured - clock
            clock += error * period_adj
            clock = min(max(clock, clock_min), clock_max)
            phase += error * phase_adj

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
        ratio = valid / max(total, 1)
        if ratio <= 0.6:
            return 0.1
        scaled = (ratio - 0.6) / 0.4
        return max(0.1, min(1.0, scaled))

    def decode_revolution(self, rev: RevolutionFlux) -> Bitstream:
        if not rev.interval_ns:
            raise FluxDecodeError("No flux data available for GCR decoding")

        candidates = [self.cell_ns, 3250.0, 3500.0, 3750.0, 4000.0]
        best_bits: List[int] = []
        best_confidence = -1.0
        for cell_ns in candidates:
            bits = self._intervals_to_bits(rev.interval_ns, cell_ns)
            confidence = self._estimate_confidence(bits)
            if confidence > best_confidence:
                best_confidence = confidence
                best_bits = bits
        metrics = BitDecodeMetrics(pll_lock_score=best_confidence, rpm_estimate=None, confidence=best_confidence)
        bs = Bitstream(bits=best_bits, metrics=metrics, source_revs=[rev.index])
        # Stash intervals for downstream PLL fallback if needed.
        bs.intervals = rev.interval_ns  # type: ignore[attr-defined]
        return bs


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
