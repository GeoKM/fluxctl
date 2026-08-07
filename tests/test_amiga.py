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
