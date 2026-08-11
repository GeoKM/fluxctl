from __future__ import annotations

import importlib.util
import subprocess
import sys
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


def test_default_greaseweazle_checkout_is_sibling():
    installer = _load_installer()

    assert installer._default_greaseweazle_checkout() == installer.ROOT.parent / "greaseweazle"


def test_default_hxcfe_checkout_is_sibling():
    installer = _load_installer()

    assert installer._default_hxcfe_checkout() == installer.ROOT.parent / "HxCFloppyEmulator"


def test_hxcfe_candidate_paths_include_known_build_locations(monkeypatch, tmp_path):
    installer = _load_installer()
    checkout = tmp_path / "HxCFloppyEmulator"
    explicit = tmp_path / "custom-hxcfe"

    monkeypatch.setattr(installer, "_find_sibling", lambda name: checkout if name == "HxCFloppyEmulator" else None)
    monkeypatch.setattr(installer.shutil, "which", lambda name: None)

    candidates = installer._hxcfe_candidate_paths(explicit)

    assert candidates == [
        explicit,
        checkout / "HxCFloppyEmulator_cmdline" / "build" / "hxcfe",
        checkout / "HxCFloppyEmulator_cmdline" / "build" / "hxcfe.exe",
        checkout / "build" / "hxcfe",
        checkout / "build" / "hxcfe.exe",
    ]


def test_hxcfe_windows_executable_does_not_need_unix_execute_bit(monkeypatch, tmp_path):
    installer = _load_installer()
    hxcfe = tmp_path / "hxcfe.exe"
    hxcfe.write_bytes(b"MZ")
    monkeypatch.setattr(installer.os, "name", "nt")
    monkeypatch.setattr(installer.os, "access", lambda path, mode: False)

    assert installer._check_hxcfe(hxcfe) == f"HxCFE found: {hxcfe}"


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


def test_windows_rust_target_uses_python_platform_tag(monkeypatch, tmp_path):
    installer = _load_installer()
    python = tmp_path / "venv" / "Scripts" / "python.exe"
    monkeypatch.setattr(installer.os, "name", "nt")
    monkeypatch.setattr(
        installer,
        "_run_probe",
        lambda args, cwd=installer.ROOT: subprocess.CompletedProcess(
            args, 0, "win-amd64\n", ""
        ),
    )

    assert installer._windows_rust_target(python) == "x86_64-pc-windows-msvc"


def test_build_native_uses_python_rust_target(monkeypatch, tmp_path):
    installer = _load_installer()
    python = tmp_path / "venv" / "Scripts" / "python.exe"
    calls: list[list[str]] = []
    monkeypatch.setattr(
        installer.shutil,
        "which",
        lambda name: f"C:\\tools\\{name}.exe" if name in {"cargo", "rustup"} else None,
    )
    monkeypatch.setattr(
        installer, "_windows_rust_target", lambda python: "x86_64-pc-windows-msvc"
    )
    monkeypatch.setattr(
        installer,
        "_run_optional",
        lambda args, cwd=installer.ROOT: calls.append([str(arg) for arg in args]) or True,
    )

    installer._build_native(python)

    assert calls[0][-3:] == ["target", "add", "x86_64-pc-windows-msvc"]
    assert calls[1][-2:] == ["--target", "x86_64-pc-windows-msvc"]


def test_yes_does_not_override_explicit_no_gui(monkeypatch, tmp_path):
    installer = _load_installer()
    venv_path = tmp_path / ".venv"
    python = installer._python_in_venv(venv_path)
    python.parent.mkdir(parents=True)
    python.touch()
    install_commands: list[list[str]] = []
    monkeypatch.setattr(
        sys,
        "argv",
        ["install_fluxctl.py", "--yes", "--no-gui", "--venv", str(venv_path)],
    )
    monkeypatch.setattr(installer, "_ensure_pip", lambda python: None)
    monkeypatch.setattr(
        installer,
        "_run",
        lambda args, cwd=installer.ROOT: install_commands.append(
            [str(arg) for arg in args]
        ),
    )
    monkeypatch.setattr(installer, "_check_hxcfe", lambda path: "optional")

    assert installer.main() == 0
    assert install_commands[1][-1] == "."


def test_greaseweazle_build_hint_mentions_python_headers(monkeypatch, capsys, tmp_path):
    installer = _load_installer()
    checkout = tmp_path / "greaseweazle"
    python = tmp_path / "venv" / "bin" / "python"

    monkeypatch.setattr(installer.os, "name", "posix")
    monkeypatch.setattr(installer.sys, "platform", "linux")
    installer._print_greaseweazle_build_hint(checkout, python)

    output = capsys.readouterr().out
    assert "python3-dev" in output
    assert "build-essential" in output
    assert str(checkout) in output


def test_greaseweazle_build_hint_mentions_windows_cpp_tools(monkeypatch, tmp_path):
    installer = _load_installer()
    checkout = tmp_path / "greaseweazle"
    python = tmp_path / "venv" / "Scripts" / "python.exe"

    monkeypatch.setattr(installer.os, "name", "nt")

    hint = installer._greaseweazle_build_hint(checkout, python)

    assert "Microsoft C++ Build Tools" in hint
    assert "Desktop development with C++" in hint
    assert str(checkout) in hint


def test_build_hxcfe_failure_prints_build_tool_hint(monkeypatch, capsys, tmp_path):
    installer = _load_installer()
    checkout = tmp_path / "HxCFloppyEmulator"
    build_dir = checkout / "build"
    build_dir.mkdir(parents=True)
    (build_dir / "Makefile").write_text("all:\n\tfalse\n", encoding="utf-8")
    seen: list[tuple[list[str], Path]] = []

    def fake_run_optional(args, *, cwd=installer.ROOT):
        seen.append(([str(arg) for arg in args], cwd))
        return False

    monkeypatch.setattr(installer.os, "name", "posix")
    monkeypatch.setattr(installer.sys, "platform", "linux")
    monkeypatch.setattr(installer, "_run_optional", fake_run_optional)

    installer._build_hxcfe_checkout(checkout)

    output = capsys.readouterr().out
    assert seen == [(["make", "HxCFloppyEmulator_cmdline"], build_dir)]
    assert "build-essential" in output
    assert f"make -C {build_dir} HxCFloppyEmulator_cmdline" in output


def test_hxcfe_build_hint_mentions_windows_msys2(monkeypatch, tmp_path):
    installer = _load_installer()
    checkout = tmp_path / "HxCFloppyEmulator"

    monkeypatch.setattr(installer.os, "name", "nt")

    hint = installer._hxcfe_build_hint(checkout)

    assert "MSYS2" in hint
    assert "mingw-w64-x86_64-toolchain" in hint
    assert "hxcfe.exe" in hint
