from __future__ import annotations

import importlib.util
import subprocess
from pathlib import Path


def _load_installer():
    path = Path(__file__).resolve().parents[1] / "scripts" / "install_fluxctl.py"
    spec = importlib.util.spec_from_file_location("install_fluxctl", path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_ensure_pip_bootstraps_missing_pip(monkeypatch, tmp_path):
    installer = _load_installer()
    python = tmp_path / "venv" / "bin" / "python"
    calls: list[list[str]] = []

    def fake_probe(args, *, cwd=installer.ROOT):
        calls.append([str(arg) for arg in args])
        if args[2] == "pip" and len(calls) == 1:
            return subprocess.CompletedProcess(args, 1, "", "No module named pip")
        return subprocess.CompletedProcess(args, 0, "ok", "")

    monkeypatch.setattr(installer, "_run_probe", fake_probe)

    installer._ensure_pip(python)

    assert calls == [
        [str(python), "-m", "pip", "--version"],
        [str(python), "-m", "ensurepip", "--upgrade"],
        [str(python), "-m", "pip", "--version"],
    ]


def test_ensure_pip_reports_debian_hint_when_bootstrap_fails(monkeypatch, tmp_path):
    installer = _load_installer()
    python = tmp_path / "venv" / "bin" / "python"

    def fake_probe(args, *, cwd=installer.ROOT):
        if args[2] == "pip":
            return subprocess.CompletedProcess(args, 1, "", "No module named pip")
        return subprocess.CompletedProcess(args, 1, "", "No module named ensurepip")

    monkeypatch.setattr(installer, "_run_probe", fake_probe)

    try:
        installer._ensure_pip(python)
    except SystemExit as exc:
        message = str(exc)
    else:
        raise AssertionError("expected SystemExit")

    assert "sudo apt install python3-venv python3-pip" in message
    assert "--recreate-venv" in message
