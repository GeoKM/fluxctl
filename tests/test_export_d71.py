from fluxctl.exporters.d64 import DEFAULT_SECTORS_PER_TRACK, SECTOR_SIZE
from fluxctl.exporters.d71 import D71Exporter
from fluxctl.filesystems import TrackSectorImage
from fluxctl.models import LayoutDescriptor
from fluxctl.sector.models import Sector, TrackSectors


def _make_layout() -> LayoutDescriptor:
    return LayoutDescriptor(
        schema_version="layout.v1",
        layout_id="test_d71",
        name="Test D71",
        encoding="gcr",
        rpm_nominal=300,
        sides=2,
        tracks=35,
        sectors_per_track=21,
        sector_size=SECTOR_SIZE,
        gap3_hint=None,
        id_rules={"sector_number_base": 0},
        crc={},
        address_marks={},
        track_sectors=list(DEFAULT_SECTORS_PER_TRACK),
    )


def test_d71_exporter_writes_side_blocked_tracks():
    sector_a = Sector(cylinder=0, head=0, sector_id=0, size_code=1, data=b"A" * SECTOR_SIZE, crc_ok=True, confidence=1.0)
    sector_b = Sector(cylinder=0, head=1, sector_id=0, size_code=1, data=b"B" * SECTOR_SIZE, crc_ok=True, confidence=1.0)
    image = TrackSectorImage(
        [
            TrackSectors(track=0, head=0, sectors=[sector_a]),
            TrackSectors(track=0, head=1, sectors=[sector_b]),
        ],
        bytes_per_sector=SECTOR_SIZE,
    )
    image.layout = _make_layout()

    exporter = D71Exporter()
    payload = exporter.export(image)

    side_size = sum(DEFAULT_SECTORS_PER_TRACK) * SECTOR_SIZE
    assert len(payload) == side_size * 2
    assert payload[:SECTOR_SIZE] == b"A" * SECTOR_SIZE
    assert payload[side_size : side_size + SECTOR_SIZE] == b"B" * SECTOR_SIZE
    assert payload[side_size - SECTOR_SIZE : side_size] == b"\x00" * SECTOR_SIZE
    assert exporter.metadata()["padded_missing"] is True
