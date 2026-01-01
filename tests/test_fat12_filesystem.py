from pathlib import Path

from fluxctl.filesystems import RawSectorImage
from fluxctl.filesystems.fat12 import FAT12

FAT12_FIXTURE = Path("tests/fixtures/3.5inch/IBM/IBM-Generic-DSDD-MFM-IBMPC-720K.img")
NON_FAT_FIXTURE = Path("tests/fixtures/3.5inch/Commodore/Commodore-1010-DSDD-MFM-Amiga-880K.img")


def _load_image(path: Path) -> RawSectorImage:
    return RawSectorImage(path.read_bytes())


def test_probe_detects_fat12_disk():
    fs = FAT12()
    image = _load_image(FAT12_FIXTURE)
    assert fs.probe(image)


def test_probe_rejects_non_fat_disk():
    fs = FAT12()
    image = _load_image(NON_FAT_FIXTURE)
    assert not fs.probe(image)


def test_list_directory_returns_root_entries():
    fs = FAT12()
    image = _load_image(FAT12_FIXTURE)
    assert fs.probe(image)

    entries = fs.list_directory("/")
    names = {entry.name for entry in entries}

    assert "AUTOEXEC.BAT" in names
    assert "COMMAND.COM" in names
    assert len(entries) == 37


def test_extract_file_returns_expected_bytes():
    fs = FAT12()
    image = _load_image(FAT12_FIXTURE)
    assert fs.probe(image)

    content = fs.extract_file("/AUTOEXEC.BAT")

    assert content == b"@ECHO OFF\r\nCLS\r\nKEYB US\r\nSELECT MENU\r\n\x1a"
