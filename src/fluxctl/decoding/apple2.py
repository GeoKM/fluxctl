"""Apple II fixed-rate GCR flux-to-bitcell decoder."""
from __future__ import annotations

from bitarray import bitarray

from ..exceptions import FluxDecodeError
from ..models import BitDecodeMetrics, Bitstream, RevolutionFlux
from ..plugins import PluginInfo, registry
from .gcr import GCRDecoder


class Apple2GCRDecoder(GCRDecoder):
    encoding = "apple2_gcr"

    def __init__(self) -> None:
        super().__init__(cell_ns=3920.0)

    def set_track(self, cylinder: int) -> None:
        self.cell_ns = 3920.0

    def decode_revolution(self, rev: RevolutionFlux) -> Bitstream:
        if not rev.interval_ns:
            raise FluxDecodeError("No flux data available for Apple II GCR decoding")
        ordered = sorted(interval for interval in rev.interval_ns if interval > 0)
        sample_index = min(len(ordered) - 1, max(0, len(ordered) // 10))
        cell_ns = float(ordered[sample_index])
        if not 1500.0 <= cell_ns <= 5500.0:
            cell_ns = self.cell_ns
        bits: list[int] = []
        pending = 0.0
        for interval in rev.interval_ns:
            pending += float(interval)
            if pending < cell_ns * 0.55:
                continue
            cells = max(1, round(pending / cell_ns))
            bits.extend([0] * (cells - 1))
            bits.append(1)
            pending = 0.0
        stream = bitarray(bits, endian="big")
        address_prolog = bitarray(endian="big")
        address_prolog.frombytes(b"\xD5\xAA\x96")
        data_prolog = bitarray(endian="big")
        data_prolog.frombytes(b"\xD5\xAA\xAD")
        address_count = sum(1 for _ in stream.search(address_prolog))
        data_count = sum(1 for _ in stream.search(data_prolog))
        matched_pairs = min(address_count, data_count)
        confidence = min(1.0, matched_pairs / 16.0)
        return Bitstream(
            bits=bits,
            metrics=BitDecodeMetrics(pll_lock_score=confidence, rpm_estimate=None, confidence=confidence),
            source_revs=[rev.index],
        )


apple2_gcr_decoder = Apple2GCRDecoder()
registry.register_encoding(
    "apple2_gcr",
    PluginInfo(
        name="Apple II 6-and-2 GCR Decoder",
        version="0.1",
        entry=apple2_gcr_decoder,
        description="Apple II 16-sector 6-and-2 GCR decoder",
    ),
)
