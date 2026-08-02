from pathlib import Path

from typer.testing import CliRunner

from fluxctl import cli
from fluxctl import studio_services as services
from fluxctl.cli import _prepare_image
from fluxctl.filesystem_detection import detect_filesystem
from fluxctl.layouts.loader import load_builtin_layouts


FIXTURE_CPM_SRC1 = Path("tests/fixtures/8inch/CPM/CPM-Generic-SSDD-CPM22-SRC1.img")
FIXTURE_CPM_SRC2 = Path("tests/fixtures/8inch/CPM/CPM-Generic-SSDD-CPM22-SRC2.img")


def _mount_cpm_fixture(path: Path):
    load_builtin_layouts()
    image = _prepare_image(path, "dec_dec_rx02_rx02_250k", "dec_rx02")
    detection = detect_filesystem(image, path_name=path.name)
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
        "dec_dec_rx02_rx02_250k",
        "dec_rx02",
        ["/LOAD.LIN", "/LOAD.PLM"],
        tmp_path,
    )

    assert result.files == 2
    assert result.bytes == 11648
    assert (tmp_path / "LOAD.LIN").stat().st_size == 2048
    assert (tmp_path / "LOAD.PLM").stat().st_size == 9600
