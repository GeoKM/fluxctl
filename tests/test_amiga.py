from fluxctl.exporters.adf import ADFExporter, ADF_SIZE
from fluxctl.filesystems.amiga import AmigaOFS
from fluxctl.exporters.adf import ADFExporter, ADF_SIZE
from fluxctl.filesystems import RawSectorImage
from fluxctl.filesystems.amiga import AmigaOFS


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
