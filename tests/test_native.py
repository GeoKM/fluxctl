import pytest

from fluxctl.native import (
    gcr_estimate_confidence,
    gcr_intervals_to_bits,
    is_native_available,
    mfm_decode_auto,
    mfm_decode_best,
    mfm_intervals_to_bits,
    mfm_reconstruct_track,
    parse_scp_flux_bytes,
)
from fluxctl.sector.reconstruct import ID_ADDRESS_MARK, SYNC_WORD


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


def test_native_mfm_decode_auto_matches_explicit_candidates() -> None:
    intervals = [4000, 8000, 12000, 4000, 4000, 8000, 12000, 16000]
    base = 4000.0

    auto_bits, auto_pll, auto_sync = mfm_decode_auto(intervals, 4000.0, True, 64)
    best_bits, best_pll, best_sync = mfm_decode_best(intervals, [4000.0, base, base * 0.75, base / 2.0], 64)

    assert auto_bits == best_bits
    assert auto_pll == best_pll
    assert auto_sync == best_sync


def _crc16(data: bytes, poly: int = 0x1021, init: int = 0xFFFF) -> int:
    crc = init
    for byte in data:
        crc ^= byte << 8
        for _ in range(8):
            if crc & 0x8000:
                crc = (crc << 1) ^ poly
            else:
                crc <<= 1
            crc &= 0xFFFF
    return crc & 0xFFFF


def _encode_mfm_byte(value: int) -> list[int]:
    bits: list[int] = []
    for shift in range(7, -1, -1):
        bits.append(0)
        bits.append((value >> shift) & 1)
    return bits


def _mfm_sector_bits(data: bytes) -> list[int]:
    header_field = bytes([0xA1, 0xA1, 0xA1, ID_ADDRESS_MARK, 0, 0, 1, 0])
    header_crc = _crc16(header_field)
    data_marker = 0xFB
    data_field = bytes([0xA1, 0xA1, 0xA1, data_marker, *data])
    data_crc = _crc16(data_field)
    bits = [int(bit) for bit in format(SYNC_WORD, "016b")] * 3
    bits.extend(_encode_mfm_byte(ID_ADDRESS_MARK))
    for value in (0, 0, 1, 0, (header_crc >> 8) & 0xFF, header_crc & 0xFF):
        bits.extend(_encode_mfm_byte(value))
    bits.extend([int(bit) for bit in format(SYNC_WORD, "016b")] * 3)
    bits.extend(_encode_mfm_byte(data_marker))
    for value in data:
        bits.extend(_encode_mfm_byte(value))
    bits.extend(_encode_mfm_byte((data_crc >> 8) & 0xFF))
    bits.extend(_encode_mfm_byte(data_crc & 0xFF))
    return bits


def test_native_mfm_reconstruct_track_returns_sector_records() -> None:
    data = bytes(range(128))

    result = mfm_reconstruct_track(_mfm_sector_bits(data), expected_sectors=1)

    assert result is not None
    records, weak = result
    assert weak == 0
    assert records == [(0, 0, 1, 0, data, True, False)]


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
