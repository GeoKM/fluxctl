from pathlib import Path

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
