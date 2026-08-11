#!/usr/bin/env python3
"""Build distributable fluxctl artifacts.

The default build creates Python source and wheel distributions. Optional
PyInstaller builds create platform-specific standalone launchers and must be run
on each target OS.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _run(args: list[str]) -> None:
    print("+", " ".join(args))
    subprocess.run(args, cwd=ROOT, check=True)


def _require_module(module: str, install_hint: str) -> None:
    result = subprocess.run(
        [sys.executable, "-c", f"import {module}"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        raise SystemExit(f"Missing {module}. Install packaging tools with: {install_hint}")


def build_python_distributions() -> None:
    _require_module("build", f'{sys.executable} -m pip install -e ".[packaging]"')
    _run([sys.executable, "-m", "build"])


def build_pyinstaller(name: str, entry_point: str, extra_args: list[str]) -> None:
    _require_module(
        "PyInstaller",
        f'{sys.executable} -m pip install -e ".[packaging,gui]"',
    )
    entry_dir = ROOT / "build" / "pyinstaller-entrypoints"
    entry_dir.mkdir(parents=True, exist_ok=True)
    for stale_entrypoint in entry_dir.glob("*.py"):
        stale_entrypoint.unlink()
    # Do not name this file after the package. PyInstaller puts the entrypoint
    # directory on its analysis path, so `fluxctl.py` would shadow src/fluxctl.
    entry_script = entry_dir / f"{name.replace('-', '_')}_entrypoint.py"
    module, function = entry_point.split(":", 1)
    entry_script.write_text(
        f"from {module} import {function}\n\n"
        "if __name__ == '__main__':\n"
        f"    {function}()\n",
        encoding="utf-8",
    )
    _run(
        [
            sys.executable,
            "-m",
            "PyInstaller",
            "--clean",
            "--noconfirm",
            "--name",
            name,
            "--paths",
            str(ROOT / "src"),
            "--collect-submodules",
            "fluxctl",
            "--add-data",
            f"{ROOT / 'src' / 'fluxctl' / 'data' / 'layouts'}{os.pathsep}fluxctl/data/layouts",
            *extra_args,
            str(entry_script),
        ]
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Build fluxctl release artifacts.")
    parser.add_argument("--no-python", action="store_true", help="Skip wheel/sdist build.")
    parser.add_argument("--standalone-cli", action="store_true", help="Build a platform-specific standalone CLI.")
    parser.add_argument("--standalone-gui", action="store_true", help="Build a platform-specific standalone GUI.")
    args = parser.parse_args()

    if not args.no_python:
        build_python_distributions()
    if args.standalone_cli:
        build_pyinstaller("fluxctl", "fluxctl.cli:app", ["--console"])
    if args.standalone_gui:
        build_pyinstaller("fluxctl-studio", "fluxctl.gui:main", ["--windowed"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
