from fluxctl.exporters.d64 import D64Exporter, DEFAULT_SECTORS_PER_TRACK, SECTOR_SIZE
from fluxctl.filesystems import TrackSectorImage
from fluxctl.models import LayoutDescriptor
from fluxctl.sector.models import Sector, TrackSectors


def _make_layout() -> LayoutDescriptor:
    return LayoutDescriptor(
        schema_version="layout.v1",
        layout_id="test_d64",
        name="Test D64",
        encoding="gcr",
        rpm_nominal=300,
        sides=1,
        tracks=35,
        sectors_per_track=21,
        sector_size=SECTOR_SIZE,
        gap3_hint=None,
        id_rules={},
        crc={},
        address_marks={},
        track_sectors=list(DEFAULT_SECTORS_PER_TRACK),
    )


def test_d64_exporter_writes_expected_length_and_data():
    sector_a = Sector(cylinder=0, head=0, sector_id=0, size_code=1, data=b"A" * SECTOR_SIZE, crc_ok=True, confidence=1.0)
    sector_b = Sector(cylinder=1, head=0, sector_id=0, size_code=1, data=b"B" * SECTOR_SIZE, crc_ok=True, confidence=1.0)
    tracks = [TrackSectors(track=0, head=0, sectors=[sector_a]), TrackSectors(track=1, head=0, sectors=[sector_b])]
    image = TrackSectorImage(tracks, bytes_per_sector=SECTOR_SIZE)
    image.layout = _make_layout()

    exporter = D64Exporter()
    payload = exporter.export(image)

    expected_size = sum(DEFAULT_SECTORS_PER_TRACK) * SECTOR_SIZE
    assert len(payload) == expected_size
    assert payload[:SECTOR_SIZE] == b"A" * SECTOR_SIZE
    offset_track1 = DEFAULT_SECTORS_PER_TRACK[0] * SECTOR_SIZE
    assert payload[offset_track1 : offset_track1 + SECTOR_SIZE] == b"B" * SECTOR_SIZE
    assert payload[-SECTOR_SIZE:] == b"\x00" * SECTOR_SIZE
    assert exporter.metadata()["padded_missing"] is True
