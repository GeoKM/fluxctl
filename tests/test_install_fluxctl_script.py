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


def test_installable_python_project_accepts_setup_py_or_pyproject(tmp_path):
    installer = _load_installer()

    setup_project = tmp_path / "setup-project"
    setup_project.mkdir()
    (setup_project / "setup.py").write_text("", encoding="utf-8")

    pyproject_project = tmp_path / "pyproject-project"
    pyproject_project.mkdir()
    (pyproject_project / "pyproject.toml").write_text("", encoding="utf-8")

    empty_project = tmp_path / "empty-project"
    empty_project.mkdir()

    assert installer._is_installable_python_project(setup_project)
    assert installer._is_installable_python_project(pyproject_project)
    assert not installer._is_installable_python_project(empty_project)


def test_is_importable_uses_target_python(monkeypatch, tmp_path):
    installer = _load_installer()
    python = tmp_path / "venv" / "bin" / "python"
    seen: list[list[str]] = []

    def fake_probe(args, *, cwd=installer.ROOT):
        seen.append([str(arg) for arg in args])
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(installer, "_run_probe", fake_probe)

    assert installer._is_importable(python, "greaseweazle")
    assert seen == [[str(python), "-c", "import greaseweazle"]]
