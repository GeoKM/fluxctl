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
GREASEWEAZLE_REPO = "https://github.com/keirf/Greaseweazle.git"
HXCFE_REPO = "https://github.com/jfdelnero/HxCFloppyEmulator.git"


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


def _run_optional(args: list[str], *, cwd: Path = ROOT) -> bool:
    print("+", " ".join(str(arg) for arg in args))
    result = subprocess.run([str(arg) for arg in args], cwd=cwd, check=False)
    return result.returncode == 0


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


def _default_greaseweazle_checkout() -> Path:
    return ROOT.parent / "greaseweazle"


def _default_hxcfe_checkout() -> Path:
    return ROOT.parent / "HxCFloppyEmulator"


def _is_installable_python_project(path: Path) -> bool:
    return (path / "pyproject.toml").exists() or (path / "setup.py").exists()


def _is_importable(python: Path, package: str) -> bool:
    return _run_probe([python, "-c", f"import {package}"]).returncode == 0


def _hxcfe_candidate_paths(explicit_path: Path | None = None) -> list[Path]:
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
                sibling / "build" / "hxcfe",
                sibling / "build" / "hxcfe.exe",
            ]
        )
    return candidates


def _check_hxcfe(explicit_path: Path | None) -> str:
    candidates = _hxcfe_candidate_paths(explicit_path)
    for candidate in candidates:
        if candidate.exists() and os.access(candidate, os.X_OK):
            return f"HxCFE found: {candidate}"
    return (
        "HxCFE not found. It is optional. Clone and build HxCFloppyEmulator with:\n"
        f"  git clone {HXCFE_REPO} ../HxCFloppyEmulator\n"
        "  make -C ../HxCFloppyEmulator/build HxCFloppyEmulator_cmdline\n"
        "Then put hxcfe on PATH or pass --hxcfe /path/to/hxcfe to fluxctl commands."
    )


def _build_hxcfe_checkout(checkout: Path) -> None:
    build_commands = [
        (checkout / "build", ["make", "HxCFloppyEmulator_cmdline"]),
        (checkout / "HxCFloppyEmulator_cmdline" / "build", ["make"]),
    ]
    for build_dir, command in build_commands:
        if (build_dir / "Makefile").exists():
            if not _run_optional(command, cwd=build_dir):
                print(
                    "HxCFE build failed. On Debian/Ubuntu, install build tools first:\n"
                    "  sudo apt install build-essential\n"
                    f"Then rerun: make -C {checkout / 'build'} HxCFloppyEmulator_cmdline"
                )
            return
    print(f"Could not find an HxCFE Makefile under {checkout}. Build it manually and pass --hxcfe.")


def _print_greaseweazle_build_hint(checkout: Path, python: Path) -> None:
    print(
        "Greaseweazle package install failed. It builds a small C extension, "
        "so Python development headers are required."
    )
    print("On Debian/Ubuntu, install:")
    print("  sudo apt install build-essential python3-dev")
    print("Then retry:")
    print(f"  {python} -m pip install -e {checkout}")


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
    parser.add_argument("--clone-greaseweazle", action="store_true", help="Clone Greaseweazle into ../greaseweazle if no local checkout exists.")
    parser.add_argument("--hxcfe", type=Path, help="Path to an existing hxcfe binary to check.")
    parser.add_argument("--clone-hxcfe", action="store_true", help="Clone HxCFloppyEmulator into ../HxCFloppyEmulator if no local checkout exists.")
    parser.add_argument("--build-hxcfe", action="store_true", help="Run make in a discovered HxCFE build directory.")
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
        if gw_checkout is None:
            clone_target = _default_greaseweazle_checkout()
            clone_requested = args.clone_greaseweazle or (
                not args.yes
                and shutil.which("git") is not None
                and _ask(f"Clone Greaseweazle into {clone_target}?", False)
            )
            if clone_requested:
                if shutil.which("git") is None:
                    print("Cannot clone Greaseweazle because git is not available on PATH.")
                elif clone_target.exists():
                    print(f"Cannot clone Greaseweazle because {clone_target} already exists.")
                else:
                    _run(["git", "clone", GREASEWEAZLE_REPO, str(clone_target)])
                    gw_checkout = clone_target
        if gw_checkout is not None and _is_installable_python_project(gw_checkout):
            if args.yes or _ask(f"Install local Greaseweazle checkout editable from {gw_checkout}?", True):
                if not _run_optional([python, "-m", "pip", "install", "-e", str(gw_checkout)]):
                    _print_greaseweazle_build_hint(gw_checkout, python)
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
            print(f"  git clone {GREASEWEAZLE_REPO} ../greaseweazle")
            print(f"  {python} -m pip install -e ../greaseweazle")

    hxcfe_checkout = _find_sibling("HxCFloppyEmulator")
    if hxcfe_checkout is None:
        hxc_clone_target = _default_hxcfe_checkout()
        clone_hxcfe = args.clone_hxcfe or (
            not args.yes
            and shutil.which("git") is not None
            and _ask(f"Clone HxCFloppyEmulator into {hxc_clone_target}?", False)
        )
        if clone_hxcfe:
            if shutil.which("git") is None:
                print("Cannot clone HxCFloppyEmulator because git is not available on PATH.")
            elif hxc_clone_target.exists():
                print(f"Cannot clone HxCFloppyEmulator because {hxc_clone_target} already exists.")
            else:
                _run(["git", "clone", HXCFE_REPO, str(hxc_clone_target)])
                hxcfe_checkout = hxc_clone_target
    if args.build_hxcfe:
        if shutil.which("make") is None:
            print("Cannot build HxCFE because make is not available on PATH.")
        elif hxcfe_checkout is None:
            print("Cannot build HxCFE because no HxCFloppyEmulator checkout was found.")
        else:
            _build_hxcfe_checkout(hxcfe_checkout)

    print(_check_hxcfe(args.hxcfe))
    print()
    print("Installed commands:")
    print(f"  {_script_in_venv(args.venv, 'fluxctl')} doctor")
    if install_gui:
        print(f"  {_script_in_venv(args.venv, 'fluxctl-studio')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
