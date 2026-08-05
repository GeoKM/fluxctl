from pathlib import Path

from typer.testing import CliRunner

from fluxctl import cli
from fluxctl.cli import _prepare_image
from fluxctl.filesystem_detection import detect_filesystem
from fluxctl.layouts.loader import load_builtin_layouts


FIXTURE_MODEL3_TRSDOS = Path("tests/fixtures/5.25inch/TANDY/Tandy-Model3-SSDD-MFM-TRSDOS13-180K.dsk")
FIXTURE_MODEL3_LDOS = Path("tests/fixtures/5.25inch/TANDY/Tandy-Model3-SSDD-MFM-LDOS531-180K.dsk")
FIXTURE_MODEL4_TRSDOS6 = Path("tests/fixtures/5.25inch/TANDY/Tandy-Model4-SSDD-MFM-TRSDOS6-180K.dsk")
FIXTURE_MODEL4_LDOS = Path("tests/fixtures/5.25inch/TANDY/Tandy-Model4-SSDD-MFM-LDOS631-180K.dsk")


def _mount_model3_trsdos():
    load_builtin_layouts()
    image = _prepare_image(FIXTURE_MODEL3_TRSDOS, "tandy_mfm_ssdd_180k", "mfm")
    detection = detect_filesystem(image, path_name=FIXTURE_MODEL3_TRSDOS.name)
    assert detection.primary == "trsdos_1_3"
    assert detection.plugin is not None
    return detection.plugin


def test_model3_trsdos_lists_directory_entries() -> None:
    filesystem = _mount_model3_trsdos()

    entries = {entry.name: entry for entry in filesystem.list_directory()}

    assert {"BASIC.CMD", "CONVERT.CMD", "XFERSYS.CMD", "HERZ50.BLD"} <= set(entries)
    assert entries["BASIC.CMD"].size == 5120
    assert entries["CONVERT.CMD"].size == 2560


def test_model3_trsdos_extracts_file_contents(tmp_path: Path) -> None:
    out = tmp_path / "BASIC.CMD"
    runner = CliRunner()

    result = runner.invoke(
        cli.app,
        [
            "extract",
            str(FIXTURE_MODEL3_TRSDOS),
            "--layout",
            "tandy_mfm_ssdd_180k",
            "--path",
            "BASIC.CMD",
            "--out",
            str(out),
        ],
    )

    assert result.exit_code == 0
    data = out.read_bytes()
    assert len(data) == 5120
    assert data[:4] == bytes.fromhex("01955241")


def test_ldos_and_trsdos6_fixtures_are_not_misdetected_as_trsdos13() -> None:
    load_builtin_layouts()
    fixtures = [
        (FIXTURE_MODEL3_LDOS, "tandy_mfm_ssdd_180k_s0"),
        (FIXTURE_MODEL4_TRSDOS6, "tandy_mfm_ssdd_180k_s0"),
        (FIXTURE_MODEL4_LDOS, "tandy_mfm_ssdd_180k_s0"),
    ]
    for fixture, layout_id in fixtures:
        image = _prepare_image(fixture, layout_id, "mfm")
        detection = detect_filesystem(image, path_name=fixture.name)

        assert detection.primary is None
