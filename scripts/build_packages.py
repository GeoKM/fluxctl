#!/usr/bin/env python3
"""Build distributable fluxctl artifacts.

The default build creates Python source and wheel distributions. Optional
PyInstaller builds create platform-specific standalone launchers and must be run
on each target OS.
"""
from __future__ import annotations

import argparse
import importlib.util
import os
import shutil
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _run(args: list[str]) -> None:
    print("+", " ".join(args))
    env = os.environ.copy()
    env.setdefault("PYINSTALLER_CONFIG_DIR", str(ROOT / "build" / "pyinstaller-cache"))
    subprocess.run(args, cwd=ROOT, check=True, env=env)


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


def build_pyinstaller(
    name: str,
    entry_point: str,
    extra_args: list[str],
    *,
    bundled_gw: Path | None = None,
    hxcfe_bin: Path | None = None,
    hxcfe_libs: list[Path] | None = None,
    native_bin: Path | None = None,
    licenses: list[Path] | None = None,
    diagnostic_worker: Path | None = None,
) -> None:
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
    bundled_args: list[str] = []
    if bundled_gw is not None:
        bundled_args.extend(["--collect-all", "greaseweazle"])
        bundled_args.extend(["--add-binary", f"{bundled_gw}{os.pathsep}helpers/gw"])
    if diagnostic_worker is not None:
        bundled_args.extend(["--add-binary", f"{diagnostic_worker}{os.pathsep}helpers/diag-worker"])
    if hxcfe_bin is not None:
        bundled_args.extend(["--add-binary", f"{hxcfe_bin}{os.pathsep}helpers"])
    for library in hxcfe_libs or []:
        bundled_args.extend(["--add-binary", f"{library}{os.pathsep}helpers"])
    if native_bin is not None:
        bundled_args.extend(["--add-binary", f"{native_bin}{os.pathsep}native"])
    bundled_args.extend(["--add-data", f"{ROOT / 'THIRD_PARTY_NOTICES.md'}{os.pathsep}licenses"])
    for license_file in licenses or []:
        bundled_args.extend(["--add-data", f"{license_file}{os.pathsep}licenses"])
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
            *bundled_args,
            *extra_args,
            str(entry_script),
        ]
    )


def build_greaseweazle_executable() -> Path:
    """Build the GW command as a folder bundle for release installers."""
    if importlib.util.find_spec("greaseweazle") is None:
        raise SystemExit("Greaseweazle must be installed in the build environment")
    _require_module("PyInstaller", f'{sys.executable} -m pip install -e ".[packaging]"')
    entry_dir = ROOT / "build" / "pyinstaller-entrypoints"
    entry_dir.mkdir(parents=True, exist_ok=True)
    entry_script = entry_dir / "gw_entrypoint.py"
    entry_script.write_text(
        "from greaseweazle.cli import main\n\nif __name__ == '__main__':\n    main()\n",
        encoding="utf-8",
    )
    bundle_dir = ROOT / "dist" / "gw"
    if bundle_dir.is_file():
        bundle_dir.unlink()
    elif bundle_dir.is_dir():
        shutil.rmtree(bundle_dir)
    _run([
        sys.executable, "-m", "PyInstaller", "--clean", "--noconfirm",
        "--onedir", "--console", "--name", "gw", "--collect-all", "greaseweazle",
        str(entry_script),
    ])
    return bundle_dir


def build_diagnostic_worker() -> Path:
    """Build Studio's interactive diagnostic process with a console subsystem."""
    _require_module("PyInstaller", f'{sys.executable} -m pip install -e ".[packaging]"')
    entry_dir = ROOT / "build" / "pyinstaller-entrypoints"
    entry_dir.mkdir(parents=True, exist_ok=True)
    entry_script = entry_dir / "diag_worker_entrypoint.py"
    entry_script.write_text(
        "from fluxctl.drive_diagnostics_worker import main\n\nif __name__ == '__main__':\n    raise SystemExit(main())\n",
        encoding="utf-8",
    )
    bundle_dir = ROOT / "dist" / "fluxctl-diag-worker"
    if bundle_dir.exists():
        shutil.rmtree(bundle_dir)
    _run([
        sys.executable, "-m", "PyInstaller", "--clean", "--noconfirm",
        "--onedir", "--console", "--name", "fluxctl-diag-worker",
        "--paths", str(ROOT / "src"), "--collect-all", "greaseweazle",
        str(entry_script),
    ])
    return bundle_dir


def main() -> int:
    parser = argparse.ArgumentParser(description="Build fluxctl release artifacts.")
    parser.add_argument("--no-python", action="store_true", help="Skip wheel/sdist build.")
    parser.add_argument("--standalone-cli", action="store_true", help="Build a platform-specific standalone CLI.")
    parser.add_argument("--standalone-gui", action="store_true", help="Build a platform-specific standalone GUI.")
    parser.add_argument("--bundle-greaseweazle", action="store_true", help="Bundle the installed Greaseweazle package and gw command.")
    parser.add_argument("--hxcfe-bin", type=Path, help="Bundle a built hxcfe executable.")
    parser.add_argument("--hxcfe-lib", type=Path, action="append", default=[], help="Bundle a required HxCFE shared library; repeat as needed.")
    parser.add_argument("--native-bin", type=Path, help="Bundle the optional Rust decoder library.")
    parser.add_argument("--third-party-license", type=Path, action="append", default=[], help="Bundle a third-party license file; repeat as needed.")
    args = parser.parse_args()

    supplied = [path for path in [args.hxcfe_bin, args.native_bin, *args.hxcfe_lib, *args.third_party_license] if path is not None]
    for path in supplied:
        if not path.is_file():
            parser.error(f"Bundle input does not exist: {path}")
    if args.hxcfe_lib and args.hxcfe_bin is None:
        parser.error("--hxcfe-lib requires --hxcfe-bin")

    if not args.no_python:
        build_python_distributions()
    gw = build_greaseweazle_executable() if args.bundle_greaseweazle else None
    worker = build_diagnostic_worker() if args.bundle_greaseweazle else None
    bundle_options = {
        "bundled_gw": gw,
        "hxcfe_bin": args.hxcfe_bin,
        "hxcfe_libs": args.hxcfe_lib,
        "native_bin": args.native_bin,
        "licenses": args.third_party_license,
        "diagnostic_worker": worker,
    }
    if args.standalone_cli:
        build_pyinstaller("fluxctl", "fluxctl.cli:app", ["--console"], **bundle_options)
    if args.standalone_gui:
        build_pyinstaller("fluxctl-studio", "fluxctl.gui:main", ["--windowed"], **bundle_options)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
