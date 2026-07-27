import pytest

from fluxctl.native import (
    gcr_estimate_confidence,
    gcr_intervals_to_bits,
    is_native_available,
    mfm_decode_best,
    mfm_intervals_to_bits,
    parse_scp_flux_bytes,
)


pytestmark = pytest.mark.skipif(
    not is_native_available(), reason="optional native library is not built"
)


def test_native_mfm_intervals_to_bits_matches_python_shape() -> None:
    bits = mfm_intervals_to_bits([4000, 8000, 12000], 4000.0, 64)

    assert bits == bytes([1, 0, 1, 0, 0, 1])


def test_native_mfm_decode_best_returns_score_and_bits() -> None:
    bits, pll_lock, sync_count = mfm_decode_best([4000, 8000, 12000], [4000.0], 64)

    assert bits == bytes([1, 0, 1, 0, 0, 1])
    assert pll_lock == 1.0
    assert sync_count == 0


def test_native_gcr_intervals_to_bits_matches_python_shape() -> None:
    bits = gcr_intervals_to_bits([4000, 8000, 4000], 4000.0)

    assert bits == bytes([1, 0, 1, 1])


def test_native_gcr_estimate_confidence_scores_symbols() -> None:
    # 01010 and 01011 are valid; 00000 is invalid.
    bits = bytes([0, 1, 0, 1, 0, 0, 1, 0, 1, 1, 0, 0, 0, 0, 0])

    confidence = gcr_estimate_confidence(bits)

    assert confidence is not None
    assert round(confidence, 3) == 0.167


def test_native_parse_scp_flux_bytes_handles_overflows_and_truncation() -> None:
    payload = bytes.fromhex("0000000100000002FFFFAA")
    intervals = parse_scp_flux_bytes(payload, 1.0)

    assert intervals is not None
    assert list(intervals) == [65537, 65538, 65535]
