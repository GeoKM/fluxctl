from fluxctl.encoding.gcr import GCR_ENCODE_4TO5
from fluxctl.models import BitDecodeMetrics, Bitstream, RevolutionFlux
from fluxctl.sector.reconstruct import build_track_sectors
from fluxctl.sector.reconstruct_gcr import reconstruct_gcr_track


TRACK = 1
HEAD = 0
ID_LO = 0x34
ID_HI = 0x12


def _encode_bytes_to_bits(payload: bytes) -> list[int]:
    bits: list[int] = []
    for byte in payload:
        hi = byte >> 4
        lo = byte & 0x0F
        for symbol in (GCR_ENCODE_4TO5[hi], GCR_ENCODE_4TO5[lo]):
            bits.extend([(symbol >> shift) & 1 for shift in range(4, -1, -1)])
    return bits


def _build_sector_bits(sector_id: int, data: bytes, corrupt_checksum: bool = False) -> list[int]:
    header_checksum = sector_id ^ TRACK ^ ID_LO ^ ID_HI
    header = bytes([0x08, header_checksum, sector_id, TRACK, ID_LO, ID_HI, 0x0F, 0x0F])
    data_checksum = 0
    for value in data:
        data_checksum ^= value
    if corrupt_checksum:
        data_checksum ^= 0xFF
    data_block = bytes([0x07]) + data + bytes([data_checksum, 0x00, 0x00])

    bits: list[int] = []
    bits.extend([1] * 45)  # header sync
    bits.extend(_encode_bytes_to_bits(header))
    bits.extend([0] * 20)
    bits.extend([1] * 45)  # data sync
    bits.extend(_encode_bytes_to_bits(data_block))
    bits.extend([0] * 50)
    return bits


def _make_bitstream(bits: list[int]) -> Bitstream:
    metrics = BitDecodeMetrics(pll_lock_score=1.0, rpm_estimate=None, confidence=1.0)
    return Bitstream(bits=bits, metrics=metrics, source_revs=[0])


def test_reconstruct_valid_sector():
    data = bytes(range(256))
    bits = _build_sector_bits(sector_id=0, data=data)
    track = reconstruct_gcr_track(_make_bitstream(bits), cylinder=TRACK, head=HEAD, expected_sectors=1)
    assert len(track.sectors) == 1
    sector = track.sectors[0]
    assert sector.data == data
    assert sector.crc_ok is True
    assert sector.sector_id == 0


def test_reconstruct_bad_checksum_marks_crc_false():
    data = bytes(range(256))
    bits = _build_sector_bits(sector_id=1, data=data, corrupt_checksum=True)
    track = reconstruct_gcr_track(_make_bitstream(bits), cylinder=TRACK, head=HEAD, expected_sectors=1)
    assert len(track.sectors) == 1
    assert track.sectors[0].crc_ok is False


def test_reconstruct_prefers_best_duplicate():
    data = bytes(range(256))
    bad_bits = _build_sector_bits(sector_id=2, data=data, corrupt_checksum=True)
    good_bits = _build_sector_bits(sector_id=2, data=data)
    combined = bad_bits + good_bits
    track = reconstruct_gcr_track(_make_bitstream(combined), cylinder=TRACK, head=HEAD, expected_sectors=1)
    assert len(track.sectors) == 1
    assert track.sectors[0].crc_ok is True


def test_build_track_sectors_dispatches_to_gcr_path():
    data = bytes(range(256))
    bits = _build_sector_bits(sector_id=3, data=data)
    bitstream = _make_bitstream(bits)

    class FakeDecoder:
        encoding = "gcr"

        def decode_revolution(self, _rev: RevolutionFlux) -> Bitstream:
            return bitstream

    rev = RevolutionFlux(index=0, interval_ns=[])
    track = build_track_sectors(rev, FakeDecoder(), cylinder=TRACK, head=HEAD, expected_sectors=1, encoding="gcr")
    assert track.sectors and track.sectors[0].sector_id == 3
    assert track.sectors[0].crc_ok is True
