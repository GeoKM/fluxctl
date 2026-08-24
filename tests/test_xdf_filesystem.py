from pathlib import Path

from fluxctl.application.image_operations import prepare_image
from fluxctl.filesystem_detection import detect_filesystem
from fluxctl.filesystems.xdf import XDFImage


FIXTURE = Path("tests/fixtures/3.5inch/IBM/IBM-XDF-DSHD-MFM-OS2-1890K.scp")


def test_xdf_mounts_logical_fat12_and_lists_files() -> None:
    image = prepare_image(FIXTURE, "ibm_xdf_1890k", "mfm")
    detection = detect_filesystem(image)

    assert isinstance(image, XDFImage)
    assert detection.primary == "ibm_xdf_fat12"
    entries = {entry.name: entry for entry in detection.plugin.list_directory("/")}
    assert entries["UHPFS.DLL"].size == 181_968
    assert entries["UNPACK2.EXE"].size == 77_200


def test_xdf_extracts_file_and_keeps_physical_sector_mapping() -> None:
    image = prepare_image(FIXTURE, "ibm_xdf_1890k", "mfm")
    filesystem = detect_filesystem(image).plugin

    data = filesystem.extract_file("/UNPACK2.EXE")
    addresses = filesystem.file_sector_addresses("/UNPACK2.EXE")

    assert len(data) == 77_200
    assert data[:4] == bytes.fromhex("8b1880e3")
    assert addresses
    assert all(len(address) == 3 for address in addresses)


def test_xdf_accepts_logical_flat_img(tmp_path: Path) -> None:
    source = prepare_image(FIXTURE, "ibm_xdf_1890k", "mfm")
    logical_path = tmp_path / "xdf.img"
    logical_path.write_bytes(b"".join(source.iter_sectors()))

    image = prepare_image(logical_path, "ibm_xdf_1890k", "mfm")
    detection = detect_filesystem(image)

    assert detection.primary == "ibm_xdf_fat12"
    assert detection.plugin.extract_file("/UNPACK2.EXE")[:4] == bytes.fromhex("8b1880e3")
