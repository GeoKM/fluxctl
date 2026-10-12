#!/usr/bin/env python3
"""Wrap tested standalone folders in one-download platform artifacts."""
from __future__ import annotations

import argparse
import os
import platform
import shutil
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "dist"


def _version() -> str:
    import tomllib

    return tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]


def _copytree(source: Path, destination: Path) -> None:
    if not source.is_dir():
        raise SystemExit(f"Missing standalone build: {source}")
    shutil.copytree(source, destination, symlinks=True, dirs_exist_ok=True)


def assemble_macos(version: str) -> Path:
    stage = DIST / "release" / "macos-stage"
    if stage.exists():
        shutil.rmtree(stage)
    stage.mkdir(parents=True)
    _copytree(DIST / "fluxctl-studio.app", stage / "Fluxctl Studio.app")
    _copytree(DIST / "fluxctl", stage / "Fluxctl CLI")
    shutil.copy2(ROOT / "LICENSE", stage / "LICENSE")
    shutil.copy2(ROOT / "THIRD_PARTY_NOTICES.md", stage / "THIRD_PARTY_NOTICES.md")
    output = DIST / "release" / f"Fluxctl-{version}-macos-{platform.machine().lower()}.dmg"
    subprocess.run(["hdiutil", "create", "-volname", f"Fluxctl {version}", "-srcfolder", str(stage), "-ov", str(output)], check=True)
    return output


def assemble_linux(version: str, appimagetool: Path) -> Path:
    stage = DIST / "release" / "Fluxctl.AppDir"
    if stage.exists():
        shutil.rmtree(stage)
    (stage / "usr" / "lib").mkdir(parents=True)
    (stage / "usr" / "bin").mkdir(parents=True)
    _copytree(DIST / "fluxctl", stage / "usr" / "lib" / "fluxctl")
    _copytree(DIST / "fluxctl-studio", stage / "usr" / "lib" / "fluxctl-studio")
    (stage / "AppRun").write_text(
        '#!/bin/sh\nHERE="$(dirname "$(readlink -f "$0")")"\n'
        'if [ "${1:-}" = "--cli" ]; then\n  shift\n  exec "$HERE/usr/lib/fluxctl/fluxctl" "$@"\nfi\n'
        'exec "$HERE/usr/lib/fluxctl-studio/fluxctl-studio" "$@"\n',
        encoding="utf-8",
    )
    (stage / "usr" / "bin" / "fluxctl").write_text('#!/bin/sh\nHERE="$(dirname "$(readlink -f "$0")")"\nexec "$HERE/../lib/fluxctl/fluxctl" "$@"\n', encoding="utf-8")
    for item in (stage / "AppRun", stage / "usr" / "bin" / "fluxctl"):
        item.chmod(0o755)
    (stage / "fluxctl.desktop").write_text("[Desktop Entry]\nType=Application\nName=Fluxctl Studio\nExec=AppRun\nIcon=fluxctl\nCategories=Utility;\nTerminal=false\n", encoding="utf-8")
    shutil.copy2(ROOT / "packaging" / "fluxctl.svg", stage / "fluxctl.svg")
    shutil.copy2(ROOT / "LICENSE", stage / "LICENSE")
    shutil.copy2(ROOT / "THIRD_PARTY_NOTICES.md", stage / "THIRD_PARTY_NOTICES.md")
    output = DIST / "release" / f"Fluxctl-{version}-linux-{platform.machine().lower()}.AppImage"
    env = os.environ.copy()
    env["ARCH"] = platform.machine().lower()
    subprocess.run([str(appimagetool.resolve()), str(stage), str(output)], check=True, env=env)
    return output


def assemble_windows(version: str, compiler: Path) -> Path:
    env = os.environ.copy()
    env["FLUXCTL_VERSION"] = version
    subprocess.run([str(compiler), str(ROOT / "packaging" / "fluxctl-windows.iss")], cwd=ROOT, check=True, env=env)
    output = DIST / "release" / f"Fluxctl-{version}-windows-x64-setup.exe"
    if not output.is_file():
        raise SystemExit(f"Installer compiler did not create {output}")
    return output


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--appimagetool", type=Path)
    parser.add_argument("--iscc", type=Path)
    args = parser.parse_args()
    system = platform.system()
    version = _version()
    if system == "Darwin":
        output = assemble_macos(version)
    elif system == "Linux":
        if args.appimagetool is None:
            parser.error("Linux packaging requires --appimagetool")
        output = assemble_linux(version, args.appimagetool)
    elif system == "Windows":
        if args.iscc is None:
            parser.error("Windows packaging requires --iscc")
        output = assemble_windows(version, args.iscc)
    else:
        parser.error(f"Unsupported release platform: {system}")
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
