from fluxctl.encoding.gcr import GCR_ENCODE_4TO5
from fluxctl.models import BitDecodeMetrics, Bitstream
from fluxctl.sector.reconstruct_gcr import extract_best_gcr_nibble_stream


def _encode_bytes_to_bits(payload: bytes) -> list[int]:
    bits: list[int] = []
    for byte in payload:
        hi = byte >> 4
        lo = byte & 0x0F
        for symbol in (GCR_ENCODE_4TO5[hi], GCR_ENCODE_4TO5[lo]):
            bits.extend([(symbol >> shift) & 1 for shift in range(4, -1, -1)])
    return bits


def test_extract_best_gcr_nibble_stream_selects_alignment() -> None:
    payload = bytes([0xAB, 0xCD, 0xEF])
    encoded = _encode_bytes_to_bits(payload)
    bits = [0, 0] + encoded + [1, 1]
    stream = Bitstream(bits=bits, metrics=BitDecodeMetrics(confidence=0.5), source_revs=[0])

    nibble_bytes = extract_best_gcr_nibble_stream(stream)

    assert nibble_bytes == payload
