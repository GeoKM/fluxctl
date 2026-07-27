from pathlib import Path
from typing import List

import pytest

from fluxctl.decoding.mfm import mfm_decoder
from fluxctl.models import BitDecodeMetrics, Bitstream
from fluxctl.models import RevolutionFlux
from fluxctl.scp import parse_scp
from fluxctl.sector.reconstruct import (
    ID_ADDRESS_MARK,
    SYNC_WORD,
    build_track_sectors_from_revolutions,
    build_track_sectors,
    reconstruct_track,
)


FIXTURES = [
    (
        Path("tests/fixtures/5.25inch/IBM/IBM-Generic-SSDD-MFM-IBMPC-180K.scp"),
        9,
    ),
    (
        Path("tests/fixtures/3.5inch/IBM/IBM-Generic-DSDD-MFM-IBMPC-720K.scp"),
        9,
    ),
    (
        Path("tests/fixtures/3.5inch/IBM/IBM-Generic-DSHD-MFM-IBMPC-1440K.scp"),
        18,
    ),
]


@pytest.mark.parametrize("path,expected_count", FIXTURES)
def test_reconstructs_mfm_track_zero(path: Path, expected_count: int) -> None:
    scp = parse_scp(path)
    track0 = next((t for t in scp.tracks if t.track == 0 and t.side == 0), None)
    assert track0 is not None, "Fixture missing track 0/side 0"
    assert track0.revolutions, "Expected at least one revolution in fixture"

    track_sectors = build_track_sectors(
        track0.revolutions[0], mfm_decoder, cylinder=track0.track, head=track0.side, expected_sectors=expected_count
    )

    assert len(track_sectors.sectors) == expected_count
    sector_ids = sorted(sec.sector_id for sec in track_sectors.sectors)
    assert sector_ids == list(range(1, expected_count + 1))
    assert all(len(sec.data) == (128 << sec.size_code) for sec in track_sectors.sectors)


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


def _encode_mfm_byte(value: int) -> List[int]:
    bits: List[int] = []
    for shift in range(7, -1, -1):
        bits.append(0)
        bits.append((value >> shift) & 1)
    return bits


def _sync_bits(count: int = 1) -> List[int]:
    word_bits = [int(bit) for bit in format(SYNC_WORD, "016b")]
    return word_bits * count


def _build_mfm_sector_bits(
    cylinder: int,
    head: int,
    sector_id: int,
    size_code: int,
    data_bytes: bytes,
) -> List[int]:
    header_field = bytes(
        [0xA1, 0xA1, 0xA1, ID_ADDRESS_MARK, cylinder, head, sector_id, size_code]
    )
    header_crc = _crc16(header_field)
    data_marker = 0xFB
    data_field = bytes([0xA1, 0xA1, 0xA1, data_marker, *data_bytes])
    data_crc = _crc16(data_field)

    bits: List[int] = []
    bits.extend(_sync_bits(3))
    bits.extend(_encode_mfm_byte(ID_ADDRESS_MARK))
    for value in (cylinder, head, sector_id, size_code):
        bits.extend(_encode_mfm_byte(value))
    bits.extend(_encode_mfm_byte((header_crc >> 8) & 0xFF))
    bits.extend(_encode_mfm_byte(header_crc & 0xFF))
    bits.extend(_sync_bits(3))
    bits.extend(_encode_mfm_byte(data_marker))
    for byte in data_bytes:
        bits.extend(_encode_mfm_byte(byte))
    bits.extend(_encode_mfm_byte((data_crc >> 8) & 0xFF))
    bits.extend(_encode_mfm_byte(data_crc & 0xFF))
    return bits


class _SequenceDecoder:
    encoding = "mfm"

    def __init__(self, streams: dict[int, Bitstream]) -> None:
        self.streams = streams
        self.calls: list[int] = []

    def decode_revolution(self, rev: RevolutionFlux) -> Bitstream:
        self.calls.append(rev.index)
        return self.streams[rev.index]


def test_multi_revolution_reconstruction_recovers_later_good_sector() -> None:
    data_bytes = bytes(range(128))
    good_bits = _build_mfm_sector_bits(0, 0, 1, 0, data_bytes)
    decoder = _SequenceDecoder(
        {
            0: Bitstream(bits=[], metrics=BitDecodeMetrics(confidence=0.1), source_revs=[0]),
            1: Bitstream(bits=good_bits, metrics=BitDecodeMetrics(confidence=0.9), source_revs=[1]),
        }
    )
    revolutions = [
        RevolutionFlux(index=0, interval_ns=[1]),
        RevolutionFlux(index=1, interval_ns=[1]),
    ]

    track = build_track_sectors_from_revolutions(
        revolutions,
        decoder,
        cylinder=0,
        head=0,
        expected_sectors=1,
        encoding="mfm",
    )

    assert track.missing == 0
    assert len(track.sectors) == 1
    assert track.sectors[0].data == data_bytes
    assert track.sectors[0].crc_ok is True
    assert track.sectors[0].source_revolutions == [1]


def test_multi_revolution_reconstruction_stops_after_complete_good_track() -> None:
    data_bytes = bytes(range(128))
    good_bits = _build_mfm_sector_bits(0, 0, 1, 0, data_bytes)
    decoder = _SequenceDecoder(
        {
            0: Bitstream(bits=good_bits, metrics=BitDecodeMetrics(confidence=0.9), source_revs=[0]),
            1: Bitstream(bits=[], metrics=BitDecodeMetrics(confidence=0.1), source_revs=[1]),
        }
    )
    revolutions = [
        RevolutionFlux(index=0, interval_ns=[1]),
        RevolutionFlux(index=1, interval_ns=[1]),
    ]

    track = build_track_sectors_from_revolutions(
        revolutions,
        decoder,
        cylinder=0,
        head=0,
        expected_sectors=1,
        encoding="mfm",
    )

    assert len(track.sectors) == 1
    assert track.sectors[0].crc_ok is True
    assert decoder.calls == [0]


def test_bad_header_crc_still_yields_sector_with_valid_data() -> None:
    cylinder = head = 0
    sector_id = 1
    size_code = 0
    data_bytes = bytes(range(128))
    header_field = bytes(
        [0xA1, 0xA1, 0xA1, ID_ADDRESS_MARK, cylinder, head, sector_id, size_code]
    )
    data_marker = 0xFB
    header_crc = _crc16(header_field)
    data_field = bytes([0xA1, 0xA1, 0xA1, data_marker, *data_bytes])
    data_crc = _crc16(data_field)
    corrupted_crc = header_crc ^ 0xFFFF

    bits: List[int] = []
    bits.extend(_sync_bits(3))
    bits.extend(_encode_mfm_byte(ID_ADDRESS_MARK))
    for value in (cylinder, head, sector_id, size_code):
        bits.extend(_encode_mfm_byte(value))
    # Intentionally corrupt header CRC so parser marks header_crc_ok False.
    bits.extend(_encode_mfm_byte((corrupted_crc >> 8) & 0xFF))
    bits.extend(_encode_mfm_byte(corrupted_crc & 0xFF))

    bits.extend(_sync_bits(3))
    bits.extend(_encode_mfm_byte(data_marker))
    for byte in data_bytes:
        bits.extend(_encode_mfm_byte(byte))
    bits.extend(_encode_mfm_byte((data_crc >> 8) & 0xFF))
    bits.extend(_encode_mfm_byte(data_crc & 0xFF))

    bitstream = Bitstream(bits=bits, metrics=BitDecodeMetrics(confidence=0.5), source_revs=[0])
    track_sectors = reconstruct_track(bitstream, cylinder=cylinder, head=head, expected_sectors=1)

    assert len(track_sectors.sectors) == 1
    sector = track_sectors.sectors[0]
    assert sector.sector_id == sector_id
    assert sector.data == data_bytes
    assert sector.crc_ok is False
    assert track_sectors.weak == 1
