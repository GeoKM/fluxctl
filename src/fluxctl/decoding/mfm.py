"""Stub MFM decoder producing placeholder bitstreams."""
from __future__ import annotations

from typing import List

from ..models import BitDecodeMetrics, Bitstream, RevolutionFlux


class MFMDecoder:
    """Very small stand-in decoder.

    The goal for the MVP scaffold is to attach metrics and allow downstream
    components to reason about confidence values without needing to implement a
    full PLL in this iteration.
    """

    def decode_revolution(self, rev: RevolutionFlux) -> Bitstream:
        # Placeholder: in a real implementation, convert flux intervals into bits.
        metrics = BitDecodeMetrics(pll_lock_score=0.5, rpm_estimate=None, confidence=0.5)
        return Bitstream(bits=[], metrics=metrics, source_revs=[rev.index])


mfm_decoder = MFMDecoder()
