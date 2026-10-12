#!/usr/bin/env python3
"""Build integrated standalone artifacts from prepared GW/HxC checkouts."""
from __future__ import annotations

import argparse
import platform
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _one(root: Path, filename: str) -> Path:
    candidates = [root / "build" / filename, root / "HxCFloppyEmulator_cmdline" / "build" / filename]
    match = next((path for path in candidates if path.is_file()), None)
    if match is None:
        raise SystemExit(f"Required HxCFE build output missing: {filename}")
    return match


def release_inputs(hxc_root: Path, gw_root: Path) -> list[str]:
    system = platform.system()
    hxc_name = "hxcfe.exe" if system == "Windows" else "hxcfe"
    suffix = ".dll" if system == "Windows" else ".dylib" if system == "Darwin" else ".so"
    native_name = "fluxctl_native.dll" if system == "Windows" else f"libfluxctl_native{suffix}"
    native = ROOT / "native" / "fluxctl_native" / "target" / "release" / native_name
    required = [
        _one(hxc_root, hxc_name),
        _one(hxc_root, f"libhxcfe{suffix}"),
        _one(hxc_root, f"libusbhxcfe{suffix}"),
        native,
        gw_root / "COPYING",
        hxc_root / "HxCFloppyEmulator_cmdline" / "COPYING_FULL",
    ]
    for path in required:
        if not path.is_file():
            raise SystemExit(f"Required release input missing: {path}")
    return [
        "--hxcfe-bin", str(required[0]),
        "--hxcfe-lib", str(required[1]),
        "--hxcfe-lib", str(required[2]),
        "--native-bin", str(required[3]),
        "--third-party-license", str(required[4]),
        "--third-party-license", str(required[5]),
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--hxc-root", type=Path, required=True)
    parser.add_argument("--gw-root", type=Path, required=True)
    args = parser.parse_args()
    command = [
        sys.executable, str(ROOT / "scripts" / "build_packages.py"),
        "--no-python", "--standalone-cli", "--standalone-gui", "--bundle-greaseweazle",
        *release_inputs(args.hxc_root, args.gw_root),
    ]
    subprocess.run(command, cwd=ROOT, check=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
