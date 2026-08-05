from pathlib import Path

from typer.testing import CliRunner

from fluxctl import cli
from fluxctl import studio_services as services
from fluxctl.cli import _prepare_image
from fluxctl.filesystem_detection import detect_filesystem
from fluxctl.layouts.loader import load_builtin_layouts


FIXTURE_CPM_SRC1 = Path("tests/fixtures/8inch/CPM/CPM-Generic-SSSD-FM-CPM22SRC1-256K.img")
FIXTURE_CPM_SRC2 = Path("tests/fixtures/8inch/CPM/CPM-Generic-SSSD-FM-CPM22SRC2-256K.img")
FIXTURE_OSBORNE_CPM22 = Path("tests/fixtures/5.25inch/CPM/Osbourne-CPM-SSDD-MFM-CPM22-200K.imd")
FIXTURE_OSBORNE_WSTR = Path("tests/fixtures/5.25inch/CPM/Osbourne-CPM-SSDD-MFM-WSTR-200K.imd")
FIXTURE_OSBORNE_CPM22_IMG = Path("tests/fixtures/5.25inch/CPM/Osbourne-CPM-SSDD-MFM-CPM22-200K.img")
FIXTURE_OSBORNE_WSTR_IMG = Path("tests/fixtures/5.25inch/CPM/Osbourne-CPM-SSDD-MFM-WSTR-200K.img")
FIXTURE_OSBORNE_CPM22_SCP = Path("tests/fixtures/5.25inch/CPM/Osbourne-CPM-SSDD-MFM-CPM22-200K.scp")
FIXTURE_OSBORNE_WSTR_SCP = Path("tests/fixtures/5.25inch/CPM/Osbourne-CPM-SSDD-MFM-WSTR-200K.scp")
FIXTURE_KAYPRO_CPM22 = Path("tests/fixtures/5.25inch/CPM/KayproII-CPM-SSDD-MFM-CPM22-200K.imd")
FIXTURE_KAYPRO_WSTR = Path("tests/fixtures/5.25inch/CPM/KayproII-CPM-SSDD-MFM-WSTR-200K.imd")
FIXTURE_KAYPRO_CPM22_IMG = Path("tests/fixtures/5.25inch/CPM/KayproII-CPM-SSDD-MFM-CPM22-200K.img")
FIXTURE_KAYPRO_WSTR_IMG = Path("tests/fixtures/5.25inch/CPM/KayproII-CPM-SSDD-MFM-WSTR-200K.img")
FIXTURE_KAYPRO_CPM22_SCP = Path("tests/fixtures/5.25inch/CPM/KayproII-CPM-SSDD-MFM-CPM22-200K.scp")
FIXTURE_KAYPRO_WSTR_SCP = Path("tests/fixtures/5.25inch/CPM/KayproII-CPM-SSDD-MFM-WSTR-200K.scp")
FIXTURE_TANDY_CPM22_DSK = Path("tests/fixtures/5.25inch/TANDY/Tandy-Model4-SSDD-MFM-CPM22-180K.dsk")
FIXTURE_TANDY_CPMPLUS_DSK = Path("tests/fixtures/5.25inch/TANDY/Tandy-Model4-SSDD-MFM-CPMPlus-156K.dsk")


def _mount_cpm_fixture(path: Path):
    load_builtin_layouts()
    image = _prepare_image(path, "generic_fm_8inch_cpm_256k", "fm")
    detection = detect_filesystem(image, path_name=path.name)
    assert detection.plugin is not None
    return detection.plugin


def _mount_osborne_fixture(path: Path):
    load_builtin_layouts()
    image = _prepare_image(path, "osborne_mfm_ssdd_200k", "mfm")
    detection = detect_filesystem(image, path_name=path.name)
    assert detection.primary == "cpm"
    assert detection.plugin is not None
    return detection.plugin


def _mount_kaypro_fixture(path: Path):
    load_builtin_layouts()
    image = _prepare_image(path, "kaypro_mfm_ssdd_40_200k", "mfm")
    detection = detect_filesystem(image, path_name=path.name)
    assert detection.primary == "cpm"
    assert detection.plugin is not None
    return detection.plugin


def _mount_tandy_fixture(path: Path, layout_id: str):
    load_builtin_layouts()
    image = _prepare_image(path, layout_id, "mfm")
    detection = detect_filesystem(image, path_name=path.name)
    assert detection.primary == "cpm"
    assert detection.plugin is not None
    return detection.plugin


def test_cpm_26_sector_extracts_file_contents_from_source_disk_1() -> None:
    filesystem = _mount_cpm_fixture(FIXTURE_CPM_SRC1)

    data = filesystem.extract_file("/OS1BOOT.ASM")

    assert len(data) == 2688
    assert data.startswith(b"\ttitle\t'mds cold start loader at 3000h'")
    assert b"MDS-800 Cold Start Loader for CP/M 2.0" in data


def test_cpm_26_sector_extracts_file_contents_from_source_disk_2() -> None:
    filesystem = _mount_cpm_fixture(FIXTURE_CPM_SRC2)

    data = filesystem.extract_file("/SYSGEN.ASM")

    assert len(data) == 9472
    assert b"SYSGEN" in data[:2048].upper()
    assert data.rstrip(b"\x1a").upper().endswith(b"\r\n\tEND\r\n")


def test_cpm_26_sector_cli_extract_writes_file(tmp_path: Path) -> None:
    out = tmp_path / "PIP.LIN"
    runner = CliRunner()

    result = runner.invoke(
        cli.app,
        [
            "extract",
            str(FIXTURE_CPM_SRC1),
            "--path",
            "/PIP.LIN",
            "--out",
            str(out),
        ],
    )

    assert result.exit_code == 0
    assert out.stat().st_size == 10240
    assert out.read_bytes().startswith(b"0000 PIP#\r\n")


def test_cpm_26_sector_studio_exports_multiple_selected_files(tmp_path: Path) -> None:
    result = services.export_filesystem_entries(
        FIXTURE_CPM_SRC2,
        "generic_fm_8inch_cpm_256k",
        "fm",
        ["/LOAD.LIN", "/LOAD.PLM"],
        tmp_path,
    )

    assert result.files == 2
    assert result.bytes == 11648
    assert (tmp_path / "LOAD.LIN").stat().st_size == 2048
    assert (tmp_path / "LOAD.PLM").stat().st_size == 9600


def test_cpm_26_sector_file_allocation_overlay_uses_dpb_skew() -> None:
    filesystem = _mount_cpm_fixture(FIXTURE_CPM_SRC1)

    addresses = filesystem.file_sector_addresses("/PIP.LIN")

    assert len(addresses) == 80
    assert (52, 0, 2) in addresses
    assert (52, 0, 25) in addresses
    assert (55, 0, 25) in addresses


def test_osborne_cpm_extracts_file_contents() -> None:
    for fixture in (FIXTURE_OSBORNE_WSTR, FIXTURE_OSBORNE_WSTR_IMG, FIXTURE_OSBORNE_WSTR_SCP):
        filesystem = _mount_osborne_fixture(fixture)

        data = filesystem.extract_file("/SAMPLE.TXT")

        assert len(data) == 2432
        assert b"WordStar" in data


def test_osborne_cpm_studio_exports_files(tmp_path: Path) -> None:
    for fixture in (FIXTURE_OSBORNE_CPM22, FIXTURE_OSBORNE_CPM22_IMG):
        target = tmp_path / fixture.suffix.lstrip(".")
        target.mkdir()
        result = services.export_filesystem_entries(
            fixture,
            "osborne_mfm_ssdd_200k",
            "mfm",
            ["/STAT.COM", "/SYSGEN.COM"],
            target,
        )

        assert result.files == 2
        assert result.bytes == 6912
        assert (target / "STAT.COM").stat().st_size == 5376
        assert (target / "SYSGEN.COM").stat().st_size == 1536


def test_osborne_cpm_file_allocation_overlay_uses_1_based_sector_ids() -> None:
    filesystem = _mount_osborne_fixture(FIXTURE_OSBORNE_WSTR_IMG)

    assert filesystem.file_sector_addresses("/SAMPLE.TXT") == {
        (19, 0, 2),
        (19, 0, 3),
        (19, 0, 4),
    }


def test_kaypro_cpm_extracts_file_contents() -> None:
    for fixture in (FIXTURE_KAYPRO_WSTR, FIXTURE_KAYPRO_WSTR_IMG, FIXTURE_KAYPRO_WSTR_SCP):
        filesystem = _mount_kaypro_fixture(fixture)

        data = filesystem.extract_file("/WS.COM")

        assert len(data) == 17664
        assert data.rstrip(b"\x1a")


def test_kaypro_cpm_studio_exports_files(tmp_path: Path) -> None:
    for fixture in (FIXTURE_KAYPRO_CPM22, FIXTURE_KAYPRO_CPM22_IMG):
        target = tmp_path / fixture.suffix.lstrip(".")
        target.mkdir()
        result = services.export_filesystem_entries(
            fixture,
            "kaypro_mfm_ssdd_40_200k",
            "mfm",
            ["/STAT.COM", "/SYSGEN.COM"],
            target,
        )

        assert result.files == 2
        assert result.bytes == 6272
        assert (target / "STAT.COM").stat().st_size == 5248
        assert (target / "SYSGEN.COM").stat().st_size == 1024


def test_kaypro_cpm_file_allocation_overlay_uses_0_based_sector_ids() -> None:
    filesystem = _mount_kaypro_fixture(FIXTURE_KAYPRO_WSTR_IMG)

    addresses = filesystem.file_sector_addresses("/WS.COM")

    assert len(addresses) == 36
    assert (1, 0, 8) in addresses
    assert (2, 0, 0) in addresses
    assert (5, 0, 3) in addresses


def test_tandy_cpm_dsk_lists_files() -> None:
    filesystem = _mount_tandy_fixture(FIXTURE_TANDY_CPM22_DSK, "tandy_mfm_ssdd_180k")

    names = {entry.name for entry in filesystem.list_directory()}

    assert {"ASM.COM", "PIP.COM", "STAT.COM"} <= names


def test_tandy_cpm_dsk_extracts_file_contents() -> None:
    filesystem = _mount_tandy_fixture(FIXTURE_TANDY_CPM22_DSK, "tandy_mfm_ssdd_180k")

    data = filesystem.extract_file("/PIP.COM")

    assert len(data) == 4096
    assert data.startswith(bytes.fromhex("2a0100013300097e"))


def test_tandy_cpm_file_allocation_overlay_uses_even_odd_skew() -> None:
    filesystem = _mount_tandy_fixture(FIXTURE_TANDY_CPM22_DSK, "tandy_mfm_ssdd_180k")

    addresses = filesystem.file_sector_addresses("/PIP.COM")

    assert len(addresses) == 16
    assert (16, 0, 8) in addresses
    assert (16, 0, 18) in addresses
    assert (17, 0, 1) in addresses


def test_tandy_cpm_plus_dmk_lists_files() -> None:
    filesystem = _mount_tandy_fixture(FIXTURE_TANDY_CPMPLUS_DSK, "tandy_mfm_cpmplus_156k")

    names = {entry.name for entry in filesystem.list_directory()}

    assert {"CPM3.SYS", "PIP.COM", "SETDEF.COM"} <= names


def test_tandy_cpm_plus_dmk_extracts_file_contents() -> None:
    filesystem = _mount_tandy_fixture(FIXTURE_TANDY_CPMPLUS_DSK, "tandy_mfm_cpmplus_156k")

    data = filesystem.extract_file("/PIP.COM")

    assert len(data) == 8704
    assert data.startswith(bytes.fromhex("31b722cde8050e00cd0500"))
    assert b"CP/M Version 3.0" in data[:128]


def test_tandy_cpm_plus_file_allocation_overlay_uses_mixed_track_geometry() -> None:
    filesystem = _mount_tandy_fixture(FIXTURE_TANDY_CPMPLUS_DSK, "tandy_mfm_cpmplus_156k")

    addresses = filesystem.file_sector_addresses("/PIP.COM")

    assert len(addresses) == 18
    assert (19, 0, 3) in addresses
    assert (19, 0, 8) in addresses
    assert (20, 0, 1) in addresses


def test_studio_file_allocation_view_supports_modelled_cpm() -> None:
    allocation = services.file_allocation_for_image(
        FIXTURE_KAYPRO_WSTR_IMG,
        "kaypro_mfm_ssdd_40_200k",
        "mfm",
        "/WS.COM",
    )

    assert allocation.path == "/WS.COM"
    assert len(allocation.sectors) == 36
    assert (2, 0, 0) in allocation.sectors
