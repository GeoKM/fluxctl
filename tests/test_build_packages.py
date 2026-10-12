from __future__ import annotations

import importlib.util
import os
from pathlib import Path


def _load_builder():
    path = Path(__file__).resolve().parents[1] / "scripts" / "build_packages.py"
    spec = importlib.util.spec_from_file_location("build_packages", path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_pyinstaller_build_includes_source_package_and_layouts(monkeypatch):
    builder = _load_builder()
    calls: list[list[str]] = []
    entry_dir = builder.ROOT / "build" / "pyinstaller-entrypoints"
    entry_dir.mkdir(parents=True, exist_ok=True)
    stale_entrypoint = entry_dir / "fluxctl.py"
    stale_entrypoint.write_text("# stale generated entrypoint\n", encoding="utf-8")

    monkeypatch.setattr(builder, "_require_module", lambda module, hint: None)
    monkeypatch.setattr(builder, "_run", lambda args: calls.append(args))

    builder.build_pyinstaller("fluxctl", "fluxctl.cli:app", ["--console"])

    command = calls[0]
    assert not stale_entrypoint.exists()
    assert command[-1].endswith("fluxctl_entrypoint.py")
    assert command[command.index("--paths") + 1] == str(builder.ROOT / "src")
    assert command[command.index("--collect-submodules") + 1] == "fluxctl"
    data_arg = command[command.index("--add-data") + 1]
    source, destination = data_arg.split(os.pathsep, 1)
    assert source == str(builder.ROOT / "src" / "fluxctl" / "data" / "layouts")
    assert destination == "fluxctl/data/layouts"


def test_pyinstaller_build_stages_all_optional_runtime_tools(monkeypatch, tmp_path):
    builder = _load_builder()
    commands: list[list[str]] = []
    monkeypatch.setattr(builder, "_require_module", lambda module, hint: None)
    monkeypatch.setattr(builder, "_run", lambda args: commands.append(args))
    gw = tmp_path / "gw"
    hxcfe = tmp_path / "hxcfe"
    hxcfe_lib = tmp_path / "libhxcfe.dylib"
    native = tmp_path / "libfluxctl_native.dylib"
    worker = tmp_path / "fluxctl-diag-worker"

    builder.build_pyinstaller(
        "fluxctl", "fluxctl.cli:app", ["--console"],
        bundled_gw=gw, hxcfe_bin=hxcfe, hxcfe_libs=[hxcfe_lib], native_bin=native,
        diagnostic_worker=worker,
    )

    command = commands[0]
    assert command.count("--collect-all") == 1
    assert f"{gw}{os.pathsep}helpers/gw" in command
    assert f"{hxcfe}{os.pathsep}helpers" in command
    assert f"{hxcfe_lib}{os.pathsep}helpers" in command
    assert f"{native}{os.pathsep}native" in command
    assert f"{worker}{os.pathsep}helpers/diag-worker" in command
