from pathlib import Path

from typer.testing import CliRunner

from fluxctl.cli import app


runner = CliRunner()
FIXTURE = Path("tests/fixtures/3.5inch/IBM/IBM-Generic-DSDD-MFM-IBMPC-720K.scp")


def test_dump_outputs_existing_sector_hex() -> None:
    result = runner.invoke(
        app,
        [
            "dump",
            str(FIXTURE),
            "--layout",
            "ibm_mfm_720k",
            "--track",
            "0",
            "--side",
            "0",
            "--sector",
            "1",
        ],
    )

    assert result.exit_code == 0, result.output
    assert result.output.strip()


def test_dump_errors_when_sector_is_missing() -> None:
    result = runner.invoke(
        app,
        [
            "dump",
            str(FIXTURE),
            "--layout",
            "ibm_mfm_720k",
            "--track",
            "0",
            "--side",
            "0",
            "--sector",
            "999",
        ],
    )

    assert result.exit_code == 1
    assert "Sector 0:0:999 not found" in result.output


def test_dump_errors_when_track_side_is_missing() -> None:
    result = runner.invoke(
        app,
        [
            "dump",
            str(FIXTURE),
            "--layout",
            "ibm_mfm_720k",
            "--track",
            "0",
            "--side",
            "99",
            "--sector",
            "1",
        ],
    )

    assert result.exit_code == 1
    assert "Track 0 side 99 not found" in result.output
