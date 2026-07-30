from pathlib import Path
import sysconfig

import fluxctl.native as native


def test_native_load_errors_report_failed_candidate(monkeypatch, tmp_path) -> None:
    candidate = tmp_path / "fluxctl_native.dll"
    candidate.write_bytes(b"not a real dll")

    def raise_os_error(path: str) -> None:
        raise OSError(f"{path}: not a valid Win32 application")

    monkeypatch.setattr(native, "_LIB", None)
    monkeypatch.setattr(native, "_LOAD_ATTEMPTED", False)
    monkeypatch.setattr(native, "_LOAD_ERRORS", [])
    monkeypatch.setattr(native, "_candidate_paths", lambda: [Path(candidate)])
    monkeypatch.setattr(native.ctypes, "CDLL", raise_os_error)

    assert native.is_native_available() is False
    errors = native.native_load_errors()

    assert len(errors) == 1
    assert str(candidate) in errors[0]
    assert "not a valid Win32 application" in errors[0]


def test_windows_process_architecture_uses_python_platform_tag(monkeypatch) -> None:
    monkeypatch.setattr(native.platform, "system", lambda: "Windows")
    monkeypatch.setattr(sysconfig, "get_platform", lambda: "win-amd64")

    assert native.windows_process_architecture() == "x86_64"
    assert native.windows_rust_target() == "x86_64-pc-windows-msvc"


def test_native_loader_reports_architecture_mismatch(monkeypatch, tmp_path) -> None:
    candidate = tmp_path / "fluxctl_native.dll"
    candidate.write_bytes(b"MZ")
    monkeypatch.setattr(native, "_LIB", None)
    monkeypatch.setattr(native, "_LOAD_ATTEMPTED", False)
    monkeypatch.setattr(native, "_LOAD_ERRORS", [])
    monkeypatch.setattr(native, "_candidate_paths", lambda: [candidate])
    monkeypatch.setattr(native, "windows_pe_architecture", lambda path: "arm64")
    monkeypatch.setattr(native, "windows_process_architecture", lambda: "x86_64")
    monkeypatch.setattr(native, "windows_rust_target", lambda: "x86_64-pc-windows-msvc")

    assert native.is_native_available() is False
    assert "architecture mismatch" in native.native_load_errors()[0]
