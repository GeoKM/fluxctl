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
    bam[4 + (1 - 1) * 4 + 1] = 0b00000100  # Track 1 sector 2 free.

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
    entry[28:30] = (1).to_bytes(2, "little")
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
    assert entries[0].size == 5
    content = fs.extract_file("HELLO")
    assert content == b"HELLO"


def test_cbm_dos_reports_bam_block_states():
    image = _build_directory_image()
    fs = CBMDOS()
    assert fs.probe(image) is True

    states = {(track, head, sector): state for track, head, sector, state in fs.bam_blocks()}

    assert states[(1, 0, 0)] == "bam_file"
    assert states[(1, 0, 1)] == "bam_used"
    assert states[(1, 0, 2)] == "bam_free"
    assert states[(18, 0, 0)] == "bam_system"
    assert states[(18, 0, 1)] == "bam_system"


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


def test_cbm_dos_reads_rel_files_through_side_sector_index():
    layout = _make_layout()
    bam = bytearray(256)
    bam[0] = 18
    bam[1] = 1
    bam[0xA2:0xA4] = b"2A"

    directory = bytearray(256)
    directory[0] = 0
    directory[1] = 0
    entry = bytearray(32)
    entry[0] = 0x84
    entry[1:3] = bytes((1, 0))
    entry[3:6] = b"REL"
    entry[19:21] = bytes((2, 0))
    entry[21] = 5
    entry[28:30] = (1).to_bytes(2, "little")
    directory[2:34] = entry

    side = bytearray(256)
    side[2] = 0
    side[3] = 5
    side[16:18] = bytes((1, 0))

    data = bytearray(256)
    data[0:2] = b"\x00\x05"
    data[2:7] = b"HELLO"

    def track(track_number: int, sectors: list[tuple[int, bytes]]) -> TrackSectors:
        return TrackSectors(
            track=track_number,
            head=0,
            sectors=[
                Sector(cylinder=track_number, head=0, sector_id=sector_id, size_code=1,
                       data=payload, crc_ok=True, confidence=1.0)
                for sector_id, payload in sectors
            ],
        )

    image = TrackSectorImage(
        [
            track(17, [(0, bytes(bam)), (1, bytes(directory))]),
            track(1, [(0, bytes(side))]),
            track(0, [(0, bytes(data))]),
        ],
        bytes_per_sector=256,
    )
    image.layout = layout

    fs = CBMDOS()
    assert fs.probe(image) is True
    extracted = fs.extract_file("REL")
    assert len(extracted) == 254
    assert extracted.startswith(b"HELLO")
    assert fs.file_sector_addresses("REL") == {(0, 0, 0), (1, 0, 0)}
