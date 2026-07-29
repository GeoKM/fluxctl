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
        else _interactive_default(greaseweazle_choice, "Install optional Greaseweazle support dependencies?", False)
    )

    extras = []
    if install_gui:
        extras.append("gui")
    if install_gw:
        extras.append("greaseweazle")
    package = f".[{','.join(extras)}]" if extras else "."

    if not args.venv.exists():
        print(f"Creating virtual environment: {args.venv}")
        venv.EnvBuilder(with_pip=True).create(args.venv)

    python = _python_in_venv(args.venv)
    _run([python, "-m", "pip", "install", "--upgrade", "pip"])
    _run([python, "-m", "pip", "install", "-e", package])

    if install_gw:
        gw_checkout = args.editable_greaseweazle or _find_sibling("greaseweazle") or _find_sibling("Greaseweazle")
        if gw_checkout is not None and (gw_checkout / "setup.py").exists():
            if args.yes or _ask(f"Install local Greaseweazle checkout editable from {gw_checkout}?", True):
                _run([python, "-m", "pip", "install", "-e", str(gw_checkout)])
        else:
            print("No sibling Greaseweazle checkout found. Fluxctl will still run without it.")

    print(_check_hxcfe(args.hxcfe))
    print()
    print("Installed commands:")
    print(f"  {_script_in_venv(args.venv, 'fluxctl')} doctor")
    if install_gui:
        print(f"  {_script_in_venv(args.venv, 'fluxctl-studio')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
