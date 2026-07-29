#!/usr/bin/env python3
"""Install fluxctl from a source checkout with optional integrations.

This helper is intentionally conservative: it creates or reuses a virtual
environment, installs fluxctl with selected extras, and checks optional external
tools. It does not modify system Python or install OS packages.
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import venv
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_VENV = ROOT / ".venv"


def _python_in_venv(venv_path: Path) -> Path:
    if os.name == "nt":
        return venv_path / "Scripts" / "python.exe"
    return venv_path / "bin" / "python"


def _script_in_venv(venv_path: Path, name: str) -> Path:
    suffix = ".exe" if os.name == "nt" else ""
    folder = "Scripts" if os.name == "nt" else "bin"
    return venv_path / folder / f"{name}{suffix}"


def _run(args: list[str], *, cwd: Path = ROOT) -> None:
    print("+", " ".join(str(arg) for arg in args))
    subprocess.run([str(arg) for arg in args], cwd=cwd, check=True)


def _run_probe(args: list[str], *, cwd: Path = ROOT) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(arg) for arg in args],
        cwd=cwd,
        text=True,
        capture_output=True,
        check=False,
    )


def _has_pip(python: Path) -> bool:
    return _run_probe([python, "-m", "pip", "--version"]).returncode == 0


def _ensure_pip(python: Path) -> None:
    if _has_pip(python):
        return
    print("pip is missing from this virtual environment; trying ensurepip...")
    result = _run_probe([python, "-m", "ensurepip", "--upgrade"])
    if result.returncode != 0:
        details = (result.stderr or result.stdout).strip()
        if details:
            print(details)
        raise SystemExit(
            "Unable to bootstrap pip in the virtual environment.\n"
            "On Debian/Ubuntu, install the venv support packages first:\n"
            "  sudo apt install python3-venv python3-pip\n"
            "Then rerun this installer. If .venv was created before those packages "
            "were installed, rerun with --recreate-venv."
        )
    if not _has_pip(python):
        raise SystemExit(
            "ensurepip completed but pip is still unavailable. "
            "Rerun with --recreate-venv, or remove .venv and try again."
        )


def _ask(prompt: str, default: bool) -> bool:
    default_text = "Y/n" if default else "y/N"
    answer = input(f"{prompt} [{default_text}] ").strip().lower()
    if not answer:
        return default
    return answer in {"y", "yes"}


def _interactive_default(value: bool | None, prompt: str, default: bool) -> bool:
    if value is not None:
        return value
    if sys.stdin.isatty():
        return _ask(prompt, default)
    return default


def _find_sibling(name: str) -> Path | None:
    candidate = ROOT.parent / name
    return candidate if candidate.exists() else None


def _is_installable_python_project(path: Path) -> bool:
    return (path / "pyproject.toml").exists() or (path / "setup.py").exists()


def _is_importable(python: Path, package: str) -> bool:
    return _run_probe([python, "-c", f"import {package}"]).returncode == 0


def _check_hxcfe(explicit_path: Path | None) -> str:
    candidates: list[Path] = []
    if explicit_path is not None:
        candidates.append(explicit_path)
    found = shutil.which("hxcfe")
    if found:
        candidates.append(Path(found))
    sibling = _find_sibling("HxCFloppyEmulator")
    if sibling is not None:
        candidates.extend(
            [
                sibling / "HxCFloppyEmulator_cmdline" / "build" / "hxcfe",
                sibling / "HxCFloppyEmulator_cmdline" / "build" / "hxcfe.exe",
            ]
        )
    for candidate in candidates:
        if candidate.exists() and os.access(candidate, os.X_OK):
            return f"HxCFE found: {candidate}"
    return (
        "HxCFE not found. It is optional. Install/build HxCFloppyEmulator, then "
        "either put hxcfe on PATH or pass --hxcfe /path/to/hxcfe to fluxctl commands."
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Install fluxctl from this checkout.")
    parser.add_argument("--venv", type=Path, default=DEFAULT_VENV, help="Virtual environment path.")
    parser.add_argument("--recreate-venv", action="store_true", help="Delete and recreate the selected virtual environment.")
    parser.add_argument("--yes", action="store_true", help="Use recommended defaults without prompting.")
    parser.add_argument("--no-gui", action="store_true", help="Skip PySide6 GUI dependencies.")
    parser.add_argument("--gui", action="store_true", help="Install GUI dependencies.")
    parser.add_argument("--greaseweazle", action="store_true", help="Install Greaseweazle support dependencies.")
    parser.add_argument("--no-greaseweazle", action="store_true", help="Skip Greaseweazle support dependencies.")
    parser.add_argument("--editable-greaseweazle", type=Path, help="Install a local Greaseweazle checkout editable.")
    parser.add_argument("--hxcfe", type=Path, help="Path to an existing hxcfe binary to check.")
    args = parser.parse_args()

    gui_choice: bool | None = True if args.gui else False if args.no_gui else None
    greaseweazle_choice: bool | None = (
        True if args.greaseweazle else False if args.no_greaseweazle else None
    )
    install_gui = True if args.yes else _interactive_default(gui_choice, "Install Fluxctl Studio GUI dependencies?", True)
    install_gw = (
        False
        if args.yes and greaseweazle_choice is None
        else _interactive_default(
            greaseweazle_choice,
            "Install optional Greaseweazle support dependencies and check for a local Greaseweazle checkout?",
            False,
        )
    )

    extras = []
    if install_gui:
        extras.append("gui")
    if install_gw:
        extras.append("greaseweazle")
    package = f".[{','.join(extras)}]" if extras else "."

    if args.recreate_venv and args.venv.exists():
        print(f"Removing virtual environment: {args.venv}")
        shutil.rmtree(args.venv)

    if not args.venv.exists():
        print(f"Creating virtual environment: {args.venv}")
        venv.EnvBuilder(with_pip=True).create(args.venv)

    python = _python_in_venv(args.venv)
    if not python.exists():
        raise SystemExit(
            f"Virtual environment Python was not found at {python}. "
            "Rerun with --recreate-venv, or remove the venv and try again."
        )
    _ensure_pip(python)
    _run([python, "-m", "pip", "install", "--upgrade", "pip"])
    _run([python, "-m", "pip", "install", "-e", package])

    if install_gw:
        gw_checkout = args.editable_greaseweazle or _find_sibling("greaseweazle") or _find_sibling("Greaseweazle")
        if gw_checkout is not None and _is_installable_python_project(gw_checkout):
            if args.yes or _ask(f"Install local Greaseweazle checkout editable from {gw_checkout}?", True):
                _run([python, "-m", "pip", "install", "-e", str(gw_checkout)])
            if _is_importable(python, "greaseweazle"):
                print("Greaseweazle Python package import check passed.")
            else:
                print(
                    "Greaseweazle support dependencies are installed, but the "
                    "Greaseweazle Python package is still not importable."
                )
        else:
            print(
                "Greaseweazle support dependencies are installed, but no local "
                "Greaseweazle checkout was found to install."
            )
            print("To enable the optional Greaseweazle fallback decoder:")
            print("  git clone https://github.com/keirf/Greaseweazle.git ../greaseweazle")
            print(f"  {python} -m pip install -e ../greaseweazle")

    print(_check_hxcfe(args.hxcfe))
    print()
    print("Installed commands:")
    print(f"  {_script_in_venv(args.venv, 'fluxctl')} doctor")
    if install_gui:
        print(f"  {_script_in_venv(args.venv, 'fluxctl-studio')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
