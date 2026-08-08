from pathlib import Path

import pytest

from fluxctl.cli import _prepare_image
from fluxctl.filesystems import RawSectorImage
from fluxctl.filesystems.fat12 import FAT12

FAT12_FIXTURE = Path("tests/fixtures/3.5inch/IBM/IBM-Generic-DSDD-MFM-IBMPC-720K.img")
NON_FAT_FIXTURE = Path("tests/fixtures/3.5inch/Commodore/Commodore-1010-DSDD-MFM-Amiga-880K.adf")
EMPTY_8IN_FAT12_SCP = Path("tests/fixtures/8inch/IBM/IBM-Generic-DSDD-MFM-IBMPC-1200K-C.scp")
FAT12_DIR_FIXTURES = [
    Path("tests/fixtures/3.5inch/IBM/IBM-Generic-DSDD-MFM-IBMPCDIR-720K.img"),
    Path("tests/fixtures/3.5inch/IBM/IBM-Generic-DSHD-MFM-IBMPCDIR-1440K.img"),
    Path("tests/fixtures/3.5inch/IBM/IBM-Generic-DSED-MFM-IBMPCDIR-2880K.img"),
    Path("tests/fixtures/5.25inch/IBM/IBM-Generic-DSDD-MFM-IBMPCDIR-360K.img"),
    Path("tests/fixtures/5.25inch/IBM/IBM-Generic-DSHD-MFM-IBMPCDIR-1200K.img"),
]


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


def test_empty_8inch_fat12_scp_lists_no_files():
    fs = FAT12()
    image = _prepare_image(EMPTY_8IN_FAT12_SCP, "ibm_mfm_8inch_1200k", "mfm")
    assert fs.probe(image)

    assert fs.list_directory("/") == []


@pytest.mark.parametrize("fixture", FAT12_DIR_FIXTURES)
def test_fat12_directory_fixtures_support_subdirectory_drilldown(fixture: Path):
    fs = FAT12()
    image = _load_image(fixture)
    assert fs.probe(image)

    root_entries = {entry.name: entry for entry in fs.list_directory("/")}
    assert root_entries["TEST"].is_dir

    test_entries = fs.list_directory("/TEST")
    assert [entry.name for entry in test_entries] == ["AUTOEXEC.BAT"]
    assert test_entries[0].size == 265
    assert fs.extract_file("/TEST/AUTOEXEC.BAT").startswith(b"@ECHO OFF")
