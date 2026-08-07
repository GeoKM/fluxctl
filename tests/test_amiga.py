from fluxctl.exporters.adf import ADFExporter, ADF_SIZE
from fluxctl.filesystems import RawSectorImage, TrackSectorImage
from fluxctl.filesystems.amiga import AmigaOFS
from fluxctl.sector.models import Sector, TrackSectors


def _build_mock_adf() -> RawSectorImage:
    sectors = [b"" for _ in range(3)]
    sectors[0] = b"file.txt:2:4\n".ljust(512, b"\x00")
    sectors[1] = b"".ljust(512, b"\x00")
    sectors[2] = b"DATA"
    filler = b"\x00" * (512 - len(sectors[2]))
    sectors[2] = sectors[2] + filler
    payload = b"".join(sectors)
    payload += b"\x00" * (ADF_SIZE - len(payload))
    # marker block at 880
    offset = 880 * 512
    payload = payload[:offset] + b"DOS" + payload[offset + 3 :]
    return RawSectorImage(payload)


def test_amiga_probe_and_extract():
    image = _build_mock_adf()
    fs = AmigaOFS()
    assert fs.probe(image)
    entries = fs.list_directory("/")
    assert entries[0].name == "file.txt"
    content = fs.extract_file("file.txt")
    assert content.startswith(b"DATA")


def test_amiga_kickstart_probe_exposes_virtual_rom_file():
    payload = b"KICK" + b"\x00" * 508 + b"ROMDATA".ljust(512, b"\x00")
    image = RawSectorImage(payload)
    fs = AmigaOFS()

    assert fs.probe(image)
    assert fs.metadata()["filesystem"] == "amiga_kickstart"
    entries = fs.list_directory("/")
    assert len(entries) == 1
    assert entries[0].name == "Kickstart.rom"
    assert entries[0].size == len(payload)
    assert fs.extract_file("Kickstart.rom") == payload


def test_amiga_kickstart_preserves_real_directory_when_present():
    sectors = [b"\x00" * 512 for _ in range(882)]
    sectors[0] = b"KICK".ljust(512, b"\x00")
    root = bytearray(512)
    root[3] = 2
    root[24:28] = (881).to_bytes(4, "big")
    root[432] = 9
    root[433:442] = b"KickDisk1"
    sectors[880] = bytes(root)
    entry = bytearray(512)
    entry[3] = 2
    entry[324:328] = (1).to_bytes(4, "big")
    entry[432] = 7
    entry[433:440] = b"VERSION"
    entry[508:512] = (-3).to_bytes(4, "big", signed=True)
    sectors[881] = bytes(entry)
    image = RawSectorImage(b"".join(sectors))
    fs = AmigaOFS()

    assert fs.probe(image)
    assert fs.metadata()["filesystem"] == "amiga_kickstart_dos"
    assert fs.metadata()["volume_label"] == "KickDisk1"
    entries = fs.list_directory("/")
    assert [entry.name for entry in entries] == ["Kickstart.rom", "VERSION"]


def test_adf_exporter_size():
    image = _build_mock_adf()
    exporter = ADFExporter()
    assert exporter.supports(image)
    payload = exporter.export(image)
    assert len(payload) == ADF_SIZE


def test_amiga_track_image_geometry_prevents_lba_shift_when_early_sector_missing():
    root = b"\x00\x00\x00\x02ROOT".ljust(512, b"\x00")
    image = TrackSectorImage(
        [
            TrackSectors(
                track=0,
                head=0,
                sectors=[Sector(0, 0, 0, 2, b"boot".ljust(512, b"\x00"), True, 1.0)],
            ),
            TrackSectors(
                track=40,
                head=0,
                sectors=[Sector(40, 0, 0, 2, root, True, 1.0)],
            ),
        ],
        bytes_per_sector=512,
    )

    image.set_geometry(11, 2, 0)

    assert image.read_sector(880) == root


def test_amiga_kickstart_track_image_uses_zero_based_sector_addresses():
    image = TrackSectorImage(
        [
            TrackSectors(
                track=0,
                head=0,
                sectors=[Sector(0, 0, 0, 2, b"KICK".ljust(512, b"\x00"), True, 1.0)],
            ),
            TrackSectors(
                track=0,
                head=1,
                sectors=[Sector(0, 1, 0, 2, b"ROM".ljust(512, b"\x00"), True, 1.0)],
            ),
        ],
        bytes_per_sector=512,
    )
    image.set_geometry(11, 2, 0)
    fs = AmigaOFS()

    assert fs.probe(image)
    assert fs.list_directory("/")[0].size == 512
    assert fs.file_sector_addresses("Kickstart.rom") == {(0, 0, 0)}
