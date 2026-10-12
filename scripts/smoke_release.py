#!/usr/bin/env python3
"""Check standalone release artifacts outside the source checkout."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
EXE = ".exe" if os.name == "nt" else ""


def main() -> int:
    cli = ROOT / "dist" / "fluxctl" / f"fluxctl{EXE}"
    gui = ROOT / "dist" / "fluxctl-studio" / f"fluxctl-studio{EXE}"
    if not cli.is_file() or not gui.is_file():
        raise SystemExit("Both standalone CLI and Studio builds are required")
    env = os.environ.copy()
    for key in ("FLUXCTL_GW_PATH", "FLUXCTL_HXCFE_PATH", "FLUXCTL_NATIVE_PATH", "PYTHONPATH"):
        env.pop(key, None)
    if os.name != "nt":
        env["PATH"] = "/usr/bin:/bin"
    env["QT_QPA_PLATFORM"] = "offscreen"
    with tempfile.TemporaryDirectory(prefix="fluxctl-release-smoke-") as directory:
        cwd = Path(directory)
        report_process = subprocess.run(
            [str(cli), "doctor", "--json"], cwd=cwd, env=env,
            capture_output=True, text=True, timeout=45, check=True,
        )
        report = json.loads(report_process.stdout)
        checks = {check["name"]: check for check in report["checks"]}
        required = ("python", "fluxctl", "layouts", "decoders", "exporters", "filesystems", "native acceleration", "greaseweazle", "gw command", "gw diagnostics", "hxcfe")
        failures = {name: checks.get(name) for name in required if checks.get(name, {}).get("status") != "ok"}
        if failures:
            raise SystemExit(f"Release doctor checks failed: {json.dumps(failures, indent=2)}")
        for name in ("gw command", "hxcfe"):
            helper = Path(checks[name]["detail"])
            if not helper.resolve().is_relative_to(cli.parent.resolve()):
                raise SystemExit(f"{name} resolved outside the release bundle: {helper}")
        print(f"CLI doctor passed outside checkout: {report['version']}")
        worker_path = gui.parent / "_internal" / "helpers" / "diag-worker" / f"fluxctl-diag-worker{EXE}"
        if not worker_path.is_file():
            raise SystemExit(f"Bundled diagnostic worker missing: {worker_path}")
        worker = subprocess.run(
            [str(worker_path), "--help"], cwd=cwd, env=env,
            capture_output=True, text=True, timeout=30, check=False,
        )
        if worker.returncode != 0:
            raise SystemExit(f"Bundled diagnostic worker failed: {worker.stderr or worker.stdout}")
        print("Bundled diagnostic worker started")
        process = subprocess.Popen(
            [str(gui)], cwd=cwd, env=env,
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True,
        )
        try:
            time.sleep(8)
            if process.poll() is not None:
                detail = process.stderr.read() if process.stderr else ""
                raise SystemExit(f"Studio exited during smoke test ({process.returncode}): {detail}")
            print("Studio remained running for 8 seconds")
        finally:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
