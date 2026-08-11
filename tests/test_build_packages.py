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
