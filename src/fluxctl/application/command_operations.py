"""Application operations for invoking external Fluxctl commands."""
from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass(frozen=True)
class CommandResult:
    """Structured result used by frontends when a command is run."""

    args: list[str]
    returncode: int
    stdout: str
    stderr: str


def run_fluxctl_command(args: list[str], cwd: Optional[Path] = None) -> CommandResult:
    """Run a Fluxctl command with the current interpreter.

    This remains a compatibility operation for advanced Studio actions. It is
    deliberately isolated so those actions can later be replaced by direct
    application operations without changing the Qt layer.
    """

    cmd = [sys.executable, "-m", "fluxctl.cli", *args]
    completed = subprocess.run(
        cmd,
        cwd=str(cwd) if cwd else None,
        text=True,
        capture_output=True,
        check=False,
    )
    return CommandResult(args=args, returncode=completed.returncode, stdout=completed.stdout, stderr=completed.stderr)

