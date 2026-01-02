from pathlib import Path

from typer.testing import CliRunner

from fluxctl import cli

FIXTURE = Path("tests/fixtures/5.25inch/Commodore/Commodore-1541-SSDD-GCR-C64-170K.scp")


def test_probe_includes_gcr_candidates() -> None:
    runner = CliRunner()
    result = runner.invoke(cli.app, ["probe", str(FIXTURE)])
    assert result.exit_code == 0
    assert '"gcr"' in result.stdout
