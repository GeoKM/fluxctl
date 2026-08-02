from pathlib import Path

from fluxctl.cli import _prepare_image
from fluxctl.filesystem_detection import detect_filesystem
from fluxctl.layouts.loader import load_builtin_layouts


FIXTURE_D64 = Path("tests/fixtures/5.25inch/Commodore/Commodore-1541-SSDD-GCR-C64-170K.d64")
FIXTURE_D64_CPM = Path("tests/fixtures/5.25inch/Commodore/Commodore-1541-SSDD-GCR-C64CPM-170K.d64")
FIXTURE_DISK_DISECTOR_D64 = Path("tests/fixtures/5.25inch/Commodore/0008-DISC001-Disk_Disector-v5.d64")
FIXTURE_DISK_DISECTOR_SCP = Path("tests/fixtures/5.25inch/Commodore/0008-DISC001-Disk_Disector-v5.scp")
FIXTURE_D71 = Path("tests/fixtures/5.25inch/Commodore/Commodore-1571-DSDD-GCR-C128-341K.d71")
FIXTURE_D71_CPM = Path("tests/fixtures/5.25inch/Commodore/Commodore-1571-DSDD-MFM-C128CPM-340K.d71")
FIXTURE_CPM_SRC1_IMG = Path("tests/fixtures/8inch/CPM/CPM-Generic-SSDD-CPM22-SRC1.img")
FIXTURE_CPM_SRC2_IMG = Path("tests/fixtures/8inch/CPM/CPM-Generic-SSDD-CPM22-SRC2.img")


def test_detects_1541_cbm_dos_with_strong_probe() -> None:
    load_builtin_layouts()
    image = _prepare_image(FIXTURE_D64, "commodore_gcr_1541_170k", "gcr")

    detection = detect_filesystem(image, path_name="wrong-cpm-name.d64")

    assert detection.primary == "cbm_dos"
    assert detection.confidence > 0.9
    assert any(region.filesystem == "cbm_dos" for region in detection.regions)


def test_detects_1571_gcr_as_cbm_dos_family_with_regions() -> None:
    load_builtin_layouts()
    image = _prepare_image(FIXTURE_D71, "commodore_gcr_1571_341k", "gcr")

    detection = detect_filesystem(image, path_name="wrong-cpm-name.d71")

    assert detection.primary == "cbm_dos_1571"
    assert [region.filesystem for region in detection.regions] == [
        "cbm_dos_1541_compatible",
        "cbm_dos_1571_extended_side",
    ]


def test_detects_c64_cpm_from_directory_not_filename() -> None:
    load_builtin_layouts()
    image = _prepare_image(FIXTURE_D64_CPM, "commodore_gcr_1541_170k", "gcr")

    detection = detect_filesystem(image, path_name="definitely-not-cpm.d64")

    assert detection.primary == "c64_cpm_2_2"
    assert detection.regions[0].filesystem == "c64_cpm_2_2"
    assert detection.plugin is not None
    assert "PIP.COM" in [entry.name for entry in detection.plugin.list_directory("/")]


def test_detects_35_track_d64_as_cbm_dos_not_cpm() -> None:
    load_builtin_layouts()
    image = _prepare_image(FIXTURE_DISK_DISECTOR_D64, "commodore_gcr_1541_170k", "gcr")

    detection = detect_filesystem(image, path_name="disk-disector.d64")

    assert detection.primary == "cbm_dos"
    assert detection.plugin is not None
    assert len(getattr(image, "tracks", [])) == 35
    names = [entry.name for entry in detection.plugin.list_directory("/")]
    assert "DISK RESCUE" in names
    assert "PIP.COM" not in names


def test_reports_incomplete_cbm_dos_directory_chain_for_damaged_scp() -> None:
    load_builtin_layouts()
    image = _prepare_image(FIXTURE_DISK_DISECTOR_SCP, "commodore_gcr_1541_170k", "gcr")

    detection = detect_filesystem(image, path_name=FIXTURE_DISK_DISECTOR_SCP.name)

    assert detection.primary == "cbm_dos"
    assert detection.plugin is None
    assert "cbm_dos_bam_present=T18/S00" in detection.evidence
    assert "cbm_dos_directory_chain_missing=T18/S02" in detection.evidence


def test_detects_c128_cpm_from_directory_not_filename() -> None:
    load_builtin_layouts()
    image = _prepare_image(FIXTURE_D71_CPM, "commodore_gcr_1571_341k", "gcr")

    detection = detect_filesystem(image, path_name="plain-cbm-dos-name.d71")

    assert detection.primary == "c128_cpm_3_0"
    assert detection.regions[0].region == "disk"
    assert detection.plugin is not None
    names = [entry.name for entry in detection.plugin.list_directory("/")]
    assert "SETDEF.COM" in names


def test_detects_8inch_cpm_source_images_from_directory_records() -> None:
    load_builtin_layouts()
    expected_names = {
        FIXTURE_CPM_SRC1_IMG: "OS1BOOT.ASM",
        FIXTURE_CPM_SRC2_IMG: "SYSGEN.ASM",
    }

    for fixture, expected_name in expected_names.items():
        image = _prepare_image(fixture, "dec_dec_rx02_rx02_250k", "dec_rx02")
        detection = detect_filesystem(image, path_name="not-from-name.img")

        assert detection.primary == "cpm"
        assert detection.plugin is not None
        names = [entry.name for entry in detection.plugin.list_directory("/")]
        assert expected_name in names
