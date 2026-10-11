"""Pipe-safe keyboard adapter for the fork's interactive diagnostic engine.

Run in a child process so the Greaseweazle hardware loop never blocks Qt.
"""
from __future__ import annotations

import queue
import sys
import threading

from .drive_diagnostics import VALID_KEYS


class PipeKeyboard:
    """Present line-delimited GUI commands as Greaseweazle keystrokes."""

    def __init__(self) -> None:
        self._keys: queue.Queue[str] = queue.Queue()

    def __enter__(self) -> PipeKeyboard:
        threading.Thread(target=self._read_input, daemon=True).start()
        return self

    def __exit__(self, *_exc: object) -> None:
        pass

    def _read_input(self) -> None:
        for line in sys.stdin:
            key = line.strip()
            if key in VALID_KEYS:
                self._keys.put(key)
        self._keys.put("q")

    def kbhit(self) -> bool:
        return not self._keys.empty()

    def read_key(self) -> str | None:
        try:
            return self._keys.get_nowait()
        except queue.Empty:
            return None


def main() -> int:
    try:
        from greaseweazle.tools import diag
    except ImportError:
        print("Adrian Black's Greaseweazle diagnostic fork is not installed in this Python environment.", file=sys.stderr)
        return 2
    diag.keyboard.Keyboard = PipeKeyboard
    try:
        diag.main(["gw", "diag", *sys.argv[1:]])
    except (KeyboardInterrupt, SystemExit) as exc:
        if isinstance(exc, SystemExit):
            return int(exc.code) if isinstance(exc.code, int) else 1
        return 130
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
