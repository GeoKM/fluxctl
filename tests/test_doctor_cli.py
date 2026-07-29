import json

from typer.testing import CliRunner

from fluxctl.cli import app


runner = CliRunner()


def test_doctor_json_reports_core_capabilities() -> None:
    result = runner.invoke(app, ["doctor", "--json"])

    assert result.exit_code == 0, result.output
    report = json.loads(result.output)
    assert report["tool"] == "fluxctl"
    assert report["overall"] in {"ok", "fail"}
    checks = {check["name"]: check for check in report["checks"]}
    assert checks["python"]["status"] == "ok"
    assert "mfm" in checks["decoders"]["detail"]
    assert "raw" in checks["exporters"]["detail"]
    assert int(checks["layouts"]["detail"].split()[0]) > 0
    assert "native_candidates" in report


def test_doctor_greaseweazle_hint_mentions_actual_package_install() -> None:
    result = runner.invoke(app, ["doctor", "--json"])

    assert result.exit_code == 0, result.output
    report = json.loads(result.output)
    checks = {check["name"]: check for check in report["checks"]}
    suggestion = checks["greaseweazle"]["suggestion"]
    if checks["greaseweazle"]["status"] == "warn":
        assert "git clone" in suggestion
        assert ".[greaseweazle]" in suggestion
        assert "../greaseweazle" in suggestion


def test_doctor_rejects_missing_hxcfe_path() -> None:
    result = runner.invoke(app, ["doctor", "--json", "--hxcfe", "/definitely/not/hxcfe"])

    assert result.exit_code == 0, result.output
    report = json.loads(result.output)
    checks = {check["name"]: check for check in report["checks"]}
    assert checks["hxcfe"]["status"] == "fail"
    assert report["overall"] == "fail"


def test_doctor_hxcfe_hint_mentions_clone_and_build_when_missing() -> None:
    result = runner.invoke(app, ["doctor", "--json"])

    assert result.exit_code == 0, result.output
    report = json.loads(result.output)
    checks = {check["name"]: check for check in report["checks"]}
    suggestion = checks["hxcfe"]["suggestion"]
    if checks["hxcfe"]["status"] == "warn":
        assert "git clone" in suggestion
        assert "make -C" in suggestion
        assert "HxCFloppyEmulator" in suggestion


def test_top_level_help_guides_real_workflows() -> None:
    result = runner.invoke(app, ["--help"])

    assert result.exit_code == 0
    assert "Inspect, verify, recover, and convert floppy flux captures" in result.output
    assert "fluxctl doctor" in result.output
    assert "fluxctl convert disk.scp" in result.output
    assert "doctor" in result.output


def test_no_args_shows_top_level_help() -> None:
    result = runner.invoke(app, [])

    assert result.exit_code == 0
    assert "fluxctl doctor" in result.output


def test_doctor_help_has_examples() -> None:
    result = runner.invoke(app, ["doctor", "--help"])

    assert result.exit_code == 0
    assert "fluxctl doctor --json" in result.output
    assert "--hxcfe" in result.output
