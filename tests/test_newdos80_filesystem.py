from pathlib import Path

from typer.testing import CliRunner

from fluxctl import cli
from fluxctl.cli import _prepare_image
from fluxctl.filesystem_detection import detect_filesystem
from fluxctl.layouts.loader import load_builtin_layouts


FIXTURE_NEWDOS80 = Path("tests/fixtures/5.25inch/TANDY/Tandy-Model3-SSDD-MFM-NEWDOS80-180K.dmk")


def _mount_newdos80():
    load_builtin_layouts()
    image = _prepare_image(FIXTURE_NEWDOS80, "tandy_mfm_ssdd_180k_s0", "mfm")
    detection = detect_filesystem(image)
    assert detection.primary == "newdos80"
    assert detection.plugin is not None
    return detection.plugin


def test_newdos80_lists_directory_entries() -> None:
    filesystem = _mount_newdos80()

    entries = {entry.name: entry for entry in filesystem.list_directory()}

    assert {"BOOT.SYS", "DIR.SYS", "BASIC.CMD", "SUPERZAP.CMD"} <= set(entries)
    assert entries["BASIC.CMD"].size == 4608
    assert entries["SUPERZAP.CMD"].size == 7680


def test_newdos80_extracts_file_contents(tmp_path: Path) -> None:
    out = tmp_path / "BASIC.CMD"
    runner = CliRunner()

    result = runner.invoke(
        cli.app,
        [
            "extract",
            str(FIXTURE_NEWDOS80),
            "--layout",
            "tandy_mfm_ssdd_180k_s0",
            "--path",
            "BASIC.CMD",
            "--out",
            str(out),
        ],
    )

    assert result.exit_code == 0
    data = out.read_bytes()
    assert len(data) == 4608
    assert data[:4] == bytes.fromhex("010f0057")


def test_newdos80_reports_file_sector_addresses_for_map_overlay() -> None:
    filesystem = _mount_newdos80()

    addresses = filesystem.file_sector_addresses("/BASIC.CMD")

    assert len(addresses) == 20
    assert (1, 0, 17) in addresses
    assert (2, 0, 0) in addresses
    assert (3, 0, 0) in addresses
