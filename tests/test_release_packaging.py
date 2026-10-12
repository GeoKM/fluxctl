from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


def _load_script(name: str):
    path = Path(__file__).resolve().parents[1] / "scripts" / name
    spec = importlib.util.spec_from_file_location(name.removesuffix(".py"), path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_release_inputs_require_all_hxc_and_native_libraries(monkeypatch, tmp_path) -> None:
    release = _load_script("build_release.py")
    monkeypatch.setattr(release, "ROOT", tmp_path)
    monkeypatch.setattr(release.platform, "system", lambda: "Darwin")
    hxc = tmp_path / "hxc"
    gw = tmp_path / "gw"
    for file in (
        hxc / "build" / "hxcfe",
        hxc / "build" / "libhxcfe.dylib",
        hxc / "build" / "libusbhxcfe.dylib",
        hxc / "HxCFloppyEmulator_cmdline" / "COPYING_FULL",
        gw / "COPYING",
        tmp_path / "native" / "fluxctl_native" / "target" / "release" / "libfluxctl_native.dylib",
    ):
        file.parent.mkdir(parents=True, exist_ok=True)
        file.touch()

    arguments = release.release_inputs(hxc, gw)
    assert arguments.count("--hxcfe-lib") == 2
    assert arguments.count("--third-party-license") == 2
    assert "--native-bin" in arguments

    (hxc / "build" / "libusbhxcfe.dylib").unlink()
    with pytest.raises(SystemExit, match="libusbhxcfe.dylib"):
        release.release_inputs(hxc, gw)


def test_linux_appimage_exposes_studio_and_cli(monkeypatch, tmp_path) -> None:
    release = _load_script("assemble_release.py")
    root = tmp_path / "repo"
    dist = root / "dist"
    for name in ("fluxctl", "fluxctl-studio"):
        (dist / name).mkdir(parents=True)
        (dist / name / name).write_text("binary", encoding="utf-8")
    (root / "packaging").mkdir()
    (root / "packaging" / "fluxctl.svg").write_text("<svg/>", encoding="utf-8")
    (root / "LICENSE").touch()
    (root / "THIRD_PARTY_NOTICES.md").touch()
    monkeypatch.setattr(release, "ROOT", root)
    monkeypatch.setattr(release, "DIST", dist)
    monkeypatch.setattr(release.platform, "machine", lambda: "x86_64")
    calls = []
    monkeypatch.setattr(release.subprocess, "run", lambda args, **kwargs: calls.append(args))

    output = release.assemble_linux("0.3.8", tmp_path / "appimagetool")

    appdir = dist / "release" / "Fluxctl.AppDir"
    assert "--cli" in (appdir / "AppRun").read_text(encoding="utf-8")
    assert (appdir / "usr" / "lib" / "fluxctl" / "fluxctl").exists()
    assert (appdir / "usr" / "lib" / "fluxctl-studio" / "fluxctl-studio").exists()
    assert calls[0][-1] == str(output)
