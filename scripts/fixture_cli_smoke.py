#!/usr/bin/env python3
"""Run CLI smoke tests against SCP fixtures with encoding-aware timeouts."""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

from fluxctl.fixtures import discover_fixtures


def _run(cmd: list[str], timeout: int) -> tuple[int, str]:
    try:
        completed = subprocess.run(cmd, text=True, capture_output=True, timeout=timeout)
        output = (completed.stdout or "") + (completed.stderr or "")
        return completed.returncode, output.strip()
    except subprocess.TimeoutExpired:
        return 124, "timeout"


def main() -> int:
    parser = argparse.ArgumentParser(description="Run qc + visualize against SCP fixtures.")
    parser.add_argument("--fixtures-dir", default="tests/fixtures", help="Root directory of fixtures.")
    parser.add_argument("--default-timeout", type=int, default=120, help="Timeout in seconds for non-GCR runs.")
    parser.add_argument("--gcr-timeout", type=int, default=1200, help="Timeout in seconds for GCR runs.")
    args = parser.parse_args()

    fixtures = [
        fixture
        for fixture in discover_fixtures(Path(args.fixtures_dir))
        if fixture.path.suffix.lower() == ".scp"
    ]
    if not fixtures:
        print("No SCP fixtures found.")
        return 1

    failures: list[tuple[str, str, str]] = []
    skipped = 0

    with TemporaryDirectory(prefix="fluxctl-cli-") as tmp_dir:
        tmp_dir = Path(tmp_dir)
        for fixture in fixtures:
            encoding = fixture.encoding.lower()
            if encoding not in {"mfm", "gcr"}:
                skipped += 1
                continue

            timeout = args.gcr_timeout if encoding == "gcr" else args.default_timeout
            qc_out = tmp_dir / f"{fixture.path.stem}.qc.json"
            map_out = tmp_dir / f"{fixture.path.stem}.map.txt"
            base_cmd = [sys.executable, "-m", "fluxctl.cli"]

            for label, cmd in (
                ("qc", base_cmd + ["qc", str(fixture.path), "--encoding", encoding, "--json-out", str(qc_out)]),
                (
                    "visualize",
                    base_cmd
                    + [
                        "visualize",
                        str(fixture.path),
                        "--encoding",
                        encoding,
                        "--format",
                        "ascii",
                        "--out",
                        str(map_out),
                    ],
                ),
            ):
                exit_code, output = _run(cmd, timeout)
                if exit_code != 0:
                    failures.append((fixture.path.name, label, output))

    print(
        "Fixture CLI smoke results: "
        f"total={len(fixtures)}, skipped={skipped}, failures={len(failures)}"
    )
    if failures:
        for name, label, output in failures:
            print(f"- {name} ({label}) -> {output}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
