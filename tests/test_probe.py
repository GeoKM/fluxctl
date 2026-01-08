import json
from pathlib import Path

from typer.testing import CliRunner

from fluxctl import cli

FIXTURE = Path("tests/fixtures/5.25inch/Commodore/Commodore-1541-SSDD-GCR-C64-170K.scp")
FIXTURE_720K = Path("tests/fixtures/3.5inch/IBM/IBM-Generic-DSDD-MFM-IBMPC-720K.scp")
FIXTURE_1440K = Path("tests/fixtures/3.5inch/IBM/IBM-Generic-DSHD-MFM-IBMPC-1440K.scp")
FIXTURE_1200K = Path("tests/fixtures/5.25inch/IBM/IBM-Generic-DSHD-MFM-IBMPC-1200K.scp")
FIXTURE_CPM_340K = Path("tests/fixtures/5.25inch/Commodore/Commodore-1571-DSDD-GCR-C128CPM-340K.scp")
FIXTURE_CPM_170K = Path("tests/fixtures/5.25inch/Commodore/Commodore-1571-SSDD-GCR-C128CPM-170K.scp")
FIXTURE_8IN_500K = Path("tests/fixtures/8inch/DEC/DEC-RX02-DSDD-MFM-RT11-500K.scp")
FIXTURE_8IN_1200K = Path("tests/fixtures/8inch/IBM/IBM-Generic-DSDD-MFM-IBMPC-1200K.scp")
FIXTURE_8IN_FM_284K = Path("tests/fixtures/8inch/IBM/IBM-6580-SSDD-FM-DisplayWriter-284K.scp")
FIXTURE_AMIGA_880K = Path("tests/fixtures/3.5inch/Commodore/Commodore-1010-DSDD-MFM-Amiga-880K.scp")
FIXTURE_1541_CPM_170K = Path("tests/fixtures/5.25inch/Commodore/Commodore-1541-SSDD-GCR-C64CPM-170K.scp")
FIXTURE_D64 = Path("tests/fixtures/5.25inch/Commodore/Commodore-1541-SSDD-GCR-C64-170K.d64")
FIXTURE_IMG_720K = Path("tests/fixtures/3.5inch/IBM/IBM-Generic-DSDD-MFM-IBMPC-720K.img")
FIXTURE_ADF = Path("tests/fixtures/3.5inch/Commodore/Commodore-1010-DSDD-MFM-Amiga-880K.adf")


def test_probe_includes_gcr_candidates() -> None:
    runner = CliRunner()
    result = runner.invoke(cli.app, ["probe", str(FIXTURE)])
    assert result.exit_code == 0
    assert "commodore_gcr_1541_170k" in result.stdout
    assert "ibm_mfm_1440k" not in result.stdout


def test_probe_prefers_720k_layout() -> None:
    runner = CliRunner()
    result = runner.invoke(cli.app, ["probe", str(FIXTURE_720K)])
    assert result.exit_code == 0
    assert "ibm_mfm_720k" in result.stdout
    assert "ibm_mfm_360k" not in result.stdout


def test_probe_prefers_1440k_layout() -> None:
    runner = CliRunner()
    result = runner.invoke(cli.app, ["probe", str(FIXTURE_1440K)])
    assert result.exit_code == 0
    assert "ibm_mfm_1440k" in result.stdout


def test_probe_prefers_1200k_layout() -> None:
    runner = CliRunner()
    result = runner.invoke(cli.app, ["probe", str(FIXTURE_1200K)])
    assert result.exit_code == 0
    assert "ibm_mfm_1200k" in result.stdout


def test_probe_prefers_commodore_cpm_layout() -> None:
    runner = CliRunner()
    result = runner.invoke(cli.app, ["probe", str(FIXTURE_CPM_340K)])
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload[0]["encoding"] == "gcr"


def test_probe_uses_gcr_when_mfm_has_no_sectors() -> None:
    runner = CliRunner()
    result = runner.invoke(cli.app, ["probe", str(FIXTURE_CPM_170K)])
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload[0]["encoding"] == "gcr"


def test_probe_prefers_8inch_500k_layout() -> None:
    runner = CliRunner()
    result = runner.invoke(cli.app, ["probe", str(FIXTURE_8IN_500K)])
    assert result.exit_code == 0
    assert "generic_mfm_8inch_500k" in result.stdout


def test_probe_prefers_8inch_1200k_layout() -> None:
    runner = CliRunner()
    result = runner.invoke(cli.app, ["probe", str(FIXTURE_8IN_1200K)])
    assert result.exit_code == 0
    assert "ibm_mfm_8inch_1200k" in result.stdout


def test_probe_prefers_8inch_fm_284k_layout() -> None:
    runner = CliRunner()
    result = runner.invoke(cli.app, ["probe", str(FIXTURE_8IN_FM_284K)])
    assert result.exit_code == 0
    assert "ibm_fm_8inch_284k" in result.stdout


def test_probe_prefers_amiga_880k_over_hd() -> None:
    runner = CliRunner()
    result = runner.invoke(cli.app, ["probe", str(FIXTURE_AMIGA_880K)])
    assert result.exit_code == 0
    assert "amiga_mfm_880k" in result.stdout
    assert "amiga_mfm_amigados_hd_1760k" not in result.stdout


def test_probe_prefers_commodore_cpm_over_apple_gcr() -> None:
    runner = CliRunner()
    result = runner.invoke(cli.app, ["probe", str(FIXTURE_1541_CPM_170K)])
    assert result.exit_code == 0
    assert "commodore_gcr_1541_cpm_170k" in result.stdout
    assert "apple2_gcr_nofs_140_140k" not in result.stdout


def test_probe_supports_flat_d64_images() -> None:
    runner = CliRunner()
    result = runner.invoke(cli.app, ["probe", str(FIXTURE_D64)])
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload[0]["layout_id"] == "commodore_gcr_1541_170k"
    assert payload[0]["encoding"] == "gcr"
    assert payload[0]["filesystem"] == "cbm_dos"


def test_probe_supports_flat_img_images() -> None:
    runner = CliRunner()
    result = runner.invoke(cli.app, ["probe", str(FIXTURE_IMG_720K)])
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload[0]["layout_id"] == "ibm_mfm_720k"
    assert payload[0]["encoding"] == "mfm"


def test_probe_supports_flat_adf_images() -> None:
    runner = CliRunner()
    result = runner.invoke(cli.app, ["probe", str(FIXTURE_ADF)])
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload[0]["layout_id"] == "amiga_mfm_880k"
    assert payload[0]["filesystem"] is not None
