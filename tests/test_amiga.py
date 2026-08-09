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


def _set_long(block: bytearray, index: int, value: int) -> None:
    block[index * 4 : index * 4 + 4] = value.to_bytes(4, "big", signed=value < 0)


def _set_amiga_checksum(block: bytearray) -> None:
    block[20:24] = b"\x00" * 4
    total = sum(int.from_bytes(block[offset : offset + 4], "big") for offset in range(0, 512, 4))
    block[20:24] = (-total & 0xFFFFFFFF).to_bytes(4, "big")


def _build_ffs_extension_chain_adf() -> RawSectorImage:
    sectors = [bytearray(512) for _ in range(886)]
    sectors[0][:4] = b"DOS\1"

    root = sectors[880]
    _set_long(root, 0, 2)
    _set_long(root, 6, 881)

    header = sectors[881]
    _set_long(header, 0, 2)
    _set_long(header, 1, 881)
    _set_long(header, 2, 1)
    _set_long(header, 77, 882)
    _set_long(header, 81, 1024)
    header[432] = 8
    header[433:441] = b"CHAINED!"
    _set_long(header, 124, 883)
    _set_long(header, 126, 884)
    _set_long(header, 127, -3)
    _set_amiga_checksum(header)

    sibling = sectors[883]
    _set_long(sibling, 0, 2)
    _set_long(sibling, 1, 883)
    sibling[432] = 5
    sibling[433:438] = b"OTHER"
    _set_long(sibling, 127, -3)
    _set_amiga_checksum(sibling)

    extension = sectors[884]
    _set_long(extension, 0, 16)
    _set_long(extension, 1, 884)
    _set_long(extension, 2, 1)
    _set_long(extension, 77, 885)
    _set_long(extension, 125, 881)
    _set_long(extension, 127, -3)
    _set_amiga_checksum(extension)

    sectors[882][:] = b"A" * 512
    sectors[885][:] = b"B" * 512
    return RawSectorImage(b"".join(bytes(sector) for sector in sectors))


def _build_ofs_chain_adf() -> RawSectorImage:
    sectors = [bytearray(512) for _ in range(883)]
    sectors[0][:4] = b"DOS\0"

    root = sectors[880]
    _set_long(root, 0, 2)
    _set_long(root, 6, 881)

    header = sectors[881]
    _set_long(header, 0, 2)
    _set_long(header, 1, 881)
    _set_long(header, 2, 1)
    _set_long(header, 4, 882)
    _set_long(header, 77, 882)
    _set_long(header, 81, 488)
    header[432] = 3
    header[433:436] = b"OFS"
    _set_long(header, 127, -3)
    _set_amiga_checksum(header)

    data = sectors[882]
    _set_long(data, 0, 8)
    _set_long(data, 1, 881)
    _set_long(data, 2, 1)
    _set_long(data, 3, 488)
    data[24:512] = b"Z" * 488
    _set_amiga_checksum(data)
    return RawSectorImage(b"".join(bytes(sector) for sector in sectors))


def test_amiga_probe_and_extract():
    image = _build_mock_adf()
    fs = AmigaOFS()
    assert fs.probe(image)
    entries = fs.list_directory("/")
    assert entries[0].name == "file.txt"
    content = fs.extract_file("file.txt")
    assert content.startswith(b"DATA")


def test_amiga_ffs_file_uses_real_header_extension_and_data_blocks():
    image = _build_ffs_extension_chain_adf()
    fs = AmigaOFS()

    assert fs.probe(image)
    assert {entry.name for entry in fs.list_directory("/")} == {"CHAINED!", "OTHER"}
    assert fs.extract_file("CHAINED!") == b"A" * 512 + b"B" * 512
    assert fs.file_sector_addresses("CHAINED!") == {
        (40, 0, 1),
        (40, 0, 2),
        (40, 0, 4),
        (40, 0, 5),
    }


def test_amiga_ofs_file_follows_data_chain_and_excludes_block_headers_from_content():
    image = _build_ofs_chain_adf()
    fs = AmigaOFS()

    assert fs.probe(image)
    assert fs.extract_file("OFS") == b"Z" * 488
    assert fs.file_sector_addresses("OFS") == {(40, 0, 1), (40, 0, 2)}


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
