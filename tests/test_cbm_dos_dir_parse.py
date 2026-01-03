from fluxctl.filesystems import TrackSectorImage
from fluxctl.filesystems.cbm_dos import CBMDOS
from fluxctl.models import LayoutDescriptor
from fluxctl.sector.models import Sector, TrackSectors


def _make_layout():
    return LayoutDescriptor(
        schema_version="layout.v1",
        layout_id="commodore_gcr_1541_170k",
        name="Commodore 1541",
        encoding="gcr",
        rpm_nominal=300,
        sides=1,
        tracks=35,
        sectors_per_track=21,
        sector_size=256,
        gap3_hint=None,
        id_rules={},
        crc={},
        address_marks={},
        track_sectors=[
            21, 21, 21, 21, 21, 21, 21, 21, 21, 21, 21, 21, 21, 21, 21, 21, 21,
            19, 19, 19, 19, 19, 19, 19,
            18, 18, 18, 18, 18, 18,
            17, 17, 17, 17, 17,
        ],
    )


def _build_directory_image():
    # BAM sector
    bam = bytearray(256)
    bam[0] = 18
    bam[1] = 1
    bam[0xA2:0xA4] = b"2A"

    # Directory sector with a single PRG entry
    dir_sector = bytearray(256)
    dir_sector[0] = 0
    dir_sector[1] = 0
    entry = bytearray(32)
    entry[0] = 0x82  # PRG, closed
    entry[1] = 1  # start track (1-based)
    entry[2] = 0  # start sector
    name = "HELLO"
    entry[3:3 + len(name)] = name.encode("ascii")
    entry[30:32] = (1).to_bytes(2, "little")
    dir_sector[2:34] = entry

    # Data sector for HELLO
    data = bytearray(256)
    payload = b"HELLO"
    data[0] = 0  # end of chain
    data[1] = len(payload)
    data[2:2 + len(payload)] = payload

    track_17 = TrackSectors(track=17, head=0, sectors=[
        Sector(cylinder=17, head=0, sector_id=0, size_code=1, data=bytes(bam), crc_ok=True, confidence=1.0),
        Sector(cylinder=17, head=0, sector_id=1, size_code=1, data=bytes(dir_sector), crc_ok=True, confidence=1.0),
    ])
    track_0 = TrackSectors(track=0, head=0, sectors=[
        Sector(cylinder=0, head=0, sector_id=0, size_code=1, data=bytes(data), crc_ok=True, confidence=1.0)
    ])
    image = TrackSectorImage([track_17, track_0], bytes_per_sector=256)
    image.layout = _make_layout()
    return image


def test_cbm_dos_lists_directory_and_extracts_file():
    image = _build_directory_image()
    fs = CBMDOS()
    assert fs.probe(image) is True
    entries = fs.list_directory("/")
    assert len(entries) == 1
    assert entries[0].name.startswith("HELLO")
    content = fs.extract_file("HELLO")
    assert content == b"HELLO"


def test_cbm_dos_accepts_empty_directory():
    layout = _make_layout()

    bam = bytearray(256)
    bam[0] = 18
    bam[1] = 1
    bam[0xA2:0xA4] = b"2A"

    dir_sector = bytearray(256)
    dir_sector[0] = 0
    dir_sector[1] = 0

    track_17 = TrackSectors(
        track=17,
        head=0,
        sectors=[
            Sector(cylinder=17, head=0, sector_id=0, size_code=1, data=bytes(bam), crc_ok=True, confidence=1.0),
            Sector(cylinder=17, head=0, sector_id=1, size_code=1, data=bytes(dir_sector), crc_ok=True, confidence=1.0),
        ],
    )
    image = TrackSectorImage([track_17], bytes_per_sector=256)
    image.layout = layout

    fs = CBMDOS()
    assert fs.probe(image) is True
    assert fs.list_directory("/") == []
