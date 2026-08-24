import json
from pathlib import Path

from typer.testing import CliRunner

import fluxctl.cli as cli
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


def test_doctor_native_hint_mentions_rust_install(monkeypatch) -> None:
    monkeypatch.setattr(cli, "is_native_available", lambda: False)
    monkeypatch.setattr(cli, "native_load_errors", lambda: [])
    monkeypatch.delenv("FLUXCTL_DISABLE_NATIVE", raising=False)

    report = cli._doctor_report()
    checks = {check["name"]: check for check in report["checks"]}
    suggestion = checks["native acceleration"]["suggestion"]

    assert checks["native acceleration"]["status"] == "warn"
    assert "rustup.rs" in suggestion
    assert "cargo build" in suggestion


def test_doctor_native_warning_includes_load_error(monkeypatch) -> None:
    monkeypatch.setattr(cli, "is_native_available", lambda: False)
    monkeypatch.setattr(
        cli,
        "native_load_errors",
        lambda: [r"C:\repo\native\fluxctl_native.dll: not a valid Win32 application"],
    )
    monkeypatch.delenv("FLUXCTL_DISABLE_NATIVE", raising=False)

    report = cli._doctor_report()
    checks = {check["name"]: check for check in report["checks"]}

    assert checks["native acceleration"]["status"] == "warn"
    assert "not a valid Win32 application" in checks["native acceleration"]["detail"]


def test_doctor_prefers_actionable_native_error_over_missing_debug_dll(monkeypatch) -> None:
    monkeypatch.setattr(cli, "is_native_available", lambda: False)
    monkeypatch.setattr(
        cli,
        "native_load_errors",
        lambda: ["release.dll: architecture mismatch", "debug.dll: not found"],
    )
    monkeypatch.delenv("FLUXCTL_DISABLE_NATIVE", raising=False)

    report = cli._doctor_report()
    check = {item["name"]: item for item in report["checks"]}["native acceleration"]

    assert "architecture mismatch" in check["detail"]
    assert "debug.dll" not in check["detail"]


def test_doctor_native_hint_is_windows_friendly(monkeypatch) -> None:
    monkeypatch.setattr(cli.os, "name", "nt")
    monkeypatch.setattr(cli, "windows_process_architecture", lambda: "arm64")
    monkeypatch.setattr(cli, "windows_rust_target", lambda: "aarch64-pc-windows-msvc")

    suggestion = cli._native_build_suggestion()

    assert "Native Tools" in suggestion
    assert "ARM64" in suggestion
    assert "rustup.rs" in suggestion
    assert "link.exe" in suggestion
    assert "Microsoft C++ Build Tools" in suggestion
    assert "platform.machine" in suggestion
    assert "--target" in suggestion
    assert "cargo build" in suggestion


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
        assert "HxCFloppyEmulator_cmdline" in suggestion
        assert "HxCFloppyEmulator" in suggestion


def test_doctor_finds_hxcfe_in_sibling_checkout(monkeypatch, tmp_path) -> None:
    checkout = tmp_path / "fluxctl"
    checkout.mkdir()
    hxcfe = tmp_path / "HxCFloppyEmulator" / "build" / "hxcfe"
    hxcfe.parent.mkdir(parents=True)
    hxcfe.write_text("#!/bin/sh\n", encoding="utf-8")
    hxcfe.chmod(0o755)

    monkeypatch.setattr(cli, "__file__", str(checkout / "src" / "fluxctl" / "cli.py"))
    monkeypatch.setattr(cli.shutil, "which", lambda name: None)
    monkeypatch.chdir(checkout)

    report = cli._doctor_report()
    checks = {check["name"]: check for check in report["checks"]}

    assert checks["hxcfe"]["status"] == "ok"
    assert checks["hxcfe"]["detail"] == str(hxcfe)


def test_top_level_help_guides_real_workflows() -> None:
    result = runner.invoke(app, ["--help"])

    assert result.exit_code == 0
    assert "Inspect, verify, recover, and convert floppy flux captures" in result.output
    assert "fluxctl doctor" in result.output
    assert "fluxctl convert disk.scp" in result.output
    assert "Human-readable workflows:" in result.output
    assert "Machine-readable reports:" in result.output
    assert "--json-out qc.json" in result.output
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
