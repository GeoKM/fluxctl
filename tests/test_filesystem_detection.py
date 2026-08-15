from pathlib import Path

import pytest

from fluxctl.cli import _prepare_image
from fluxctl.exceptions import FilesystemError
from fluxctl.filesystems import RawSectorImage
from fluxctl.filesystems.rt11 import RT11Filesystem, RT11InterchangeFilesystem
from fluxctl.filesystem_detection import detect_filesystem
from fluxctl.layouts.loader import load_builtin_layouts


FIXTURE_D64 = Path("tests/fixtures/5.25inch/Commodore/Commodore-1541-SSDD-GCR-C64-170K.d64")
FIXTURE_D64_CPM = Path("tests/fixtures/5.25inch/Commodore/Commodore-1541-SSDD-GCR-C64CPM-170K.d64")
FIXTURE_DISK_DISECTOR_D64 = Path("tests/fixtures/5.25inch/Commodore/0008-DISC001-Disk_Disector-v5.d64")
FIXTURE_DISK_DISECTOR_SCP = Path("tests/fixtures/5.25inch/Commodore/0008-DISC001-Disk_Disector-v5.scp")
FIXTURE_D71 = Path("tests/fixtures/5.25inch/Commodore/Commodore-1571-DSDD-GCR-C128-341K.d71")
FIXTURE_D71_CPM = Path("tests/fixtures/5.25inch/Commodore/Commodore-1571-DSDD-MFM-C128CPM-340K.d71")
FIXTURE_CPM_SRC1_IMG = Path("tests/fixtures/8inch/CPM/CPM-Generic-SSSD-FM-CPM22SRC1-256K.img")
FIXTURE_CPM_SRC2_IMG = Path("tests/fixtures/8inch/CPM/CPM-Generic-SSSD-FM-CPM22SRC2-256K.img")
FIXTURE_KAYPRO_CPM22_IMD = Path("tests/fixtures/5.25inch/CPM/KayproII-CPM-SSDD-MFM-CPM22-200K.imd")
FIXTURE_RX01_INTERCHANGE_SCP = Path("tests/fixtures/8inch/DEC/DEC-RX01-SSSD-FM-RT11_IDF-250K.scp")
FIXTURE_RX02_SSDD_SCP = Path("tests/fixtures/8inch/DEC/DEC-RX02-SSDD-Modified_MFM-RT11_FORTRAN-500K.scp")
FIXTURE_RX01_CPM_SCP = Path("tests/fixtures/8inch/CPM/CPM-RX01-SSSD-FM-STATPAK311-256K.scp")
FIXTURE_SEIKO_IMG = Path("tests/fixtures/8inch/Seiko/Seiko-8300-DSDD-FM+MFM-CPM22-1000K.img")
FIXTURE_SEIKO_SCP = Path("tests/fixtures/8inch/Seiko/Seiko-8300-DSDD-FM+MFM-CPM22-1000K.scp")


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
        image = _prepare_image(fixture, "generic_fm_8inch_cpm_256k", "fm")
        detection = detect_filesystem(image, path_name="not-from-name.img")

        assert detection.primary == "cpm"
        assert detection.plugin is not None
        names = [entry.name for entry in detection.plugin.list_directory("/")]
        assert expected_name in names


def test_detects_kaypro_cpm_before_rt11_false_positive() -> None:
    load_builtin_layouts()
    image = _prepare_image(FIXTURE_KAYPRO_CPM22_IMD, "kaypro_mfm_ssdd_40_200k", "mfm")

    detection = detect_filesystem(image, path_name="not-rt11.imd")

    assert detection.primary == "cpm"
    assert detection.plugin is not None
    names = [entry.name for entry in detection.plugin.list_directory("/")]
    assert "STAT.COM" in names


def test_detects_seiko_8300_catalog_for_flat_and_flux_images() -> None:
    load_builtin_layouts()
    for fixture in (FIXTURE_SEIKO_IMG, FIXTURE_SEIKO_SCP):
        image = _prepare_image(fixture, "luxor_mfm_1000_program_994k", "mfm")
        detection = detect_filesystem(image, path_name="unrelated-name.img")

        assert detection.primary == "seiko_8300_cpm"
        assert detection.plugin is not None
        names = [entry.name for entry in detection.plugin.list_directory("/")]
        assert "PATCH" in names
        patch = next(entry for entry in detection.plugin.list_directory("/") if entry.name == "PATCH")
        assert patch.size == 0
        assert patch.cluster_start == 0
        assert detection.plugin.metadata()["read_only"] is True
        assert detection.plugin.metadata()["catalog_fields_mapped"] is True
        assert detection.plugin.metadata()["catalog_offsets_monotonic"] is True
        assert detection.plugin.metadata()["allocation_mapping_status"] == "unproven"
        assert detection.plugin.metadata()["file_sizes_verified"] is False


def test_detects_rx01_rt11_interchange_labels_without_claiming_residual_data() -> None:
    load_builtin_layouts()
    image = _prepare_image(FIXTURE_RX01_INTERCHANGE_SCP, "dec_fm_rx01_250k", "fm")

    detection = detect_filesystem(image, path_name="interchange-disk.scp")

    assert detection.primary == "rt11_interchange"
    assert detection.plugin is not None
    assert [entry.name for entry in detection.plugin.list_directory("/")] == [
        "DATA",
        "DATA.RESIDUAL.RAW",
        "DATA.RESIDUAL.json",
    ]
    assert detection.plugin.metadata()["datasets"] == "1"
    with pytest.raises(FilesystemError, match="labelled empty"):
        detection.plugin.extract_file("/DATA")
    assert len(detection.plugin.extract_file("/DATA.RESIDUAL.RAW")) == 242_944


def test_extracts_nonempty_rt11_interchange_dataset_records() -> None:
    sectors = bytearray(77 * 26 * 128)
    sectors[6 * 128 : 7 * 128] = "VOL1RT11A".ljust(80).encode("cp037")
    sectors[7 * 128 : 8 * 128] = (
        "HDR1 TEST               080 01001 01002                                   01002 "
        .ljust(80)
        .encode("cp037")
    )
    sectors[26 * 128 : 27 * 128] = b"A" * 80 + b"\x00" * 48
    filesystem = RT11InterchangeFilesystem()

    assert filesystem.probe(RawSectorImage(bytes(sectors), bytes_per_sector=128))
    entries = filesystem.list_directory("/")
    assert [(entry.name, entry.size) for entry in entries] == [("TEST", 80)]
    assert filesystem.extract_file("/TEST") == b"A" * 80


def test_lists_and_extracts_normal_rx02_rt11_files() -> None:
    load_builtin_layouts()
    for fixture in (
        FIXTURE_RX02_SSDD_SCP,
        FIXTURE_RX02_SSDD_SCP.with_suffix(".img"),
    ):
        image = _prepare_image(fixture, "dec_dec_rx02_rx02_250k", "dec_rx02")
        filesystem = RT11Filesystem()

        assert filesystem.probe(image)
        entries = filesystem.list_directory("/")
        names = [entry.name for entry in entries]
        assert len(entries) == 17
        assert "EXONL.FOR" in names
        assert "CALEX.SAV" in names
        content = filesystem.extract_file("/EXONL.FOR")
        assert len(content) == 7 * 512
        assert b"SUBROUTINE" in content


def test_lists_and_extracts_rx01_cpm_from_scp() -> None:
    load_builtin_layouts()
    image = _prepare_image(FIXTURE_RX01_CPM_SCP, "dec_fm_rx01_250k", "fm")
    detection = detect_filesystem(image, path_name=FIXTURE_RX01_CPM_SCP.name)

    assert detection.primary == "cpm"
    assert detection.plugin is not None
    assert "MANNWHIT.BAS" in {entry.name for entry in detection.plugin.list_directory("/")}
    content = detection.plugin.extract_file("/MANNWHIT.BAS")
    assert len(content) == 5888
