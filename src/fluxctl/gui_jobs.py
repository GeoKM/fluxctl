"""Qt job primitives used by Fluxctl Studio."""
from __future__ import annotations

import threading
from typing import Callable

from PySide6.QtCore import QObject, QRunnable, Signal, Slot


class JobSignals(QObject):
    finished = Signal(object)
    failed = Signal(str)
    cancelled = Signal()
    progress = Signal(int)


class Job(QRunnable):
    """Run one application operation off the GUI thread."""

    def __init__(self, fn: Callable[[], object]):
        super().__init__()
        self.fn = fn
        self.signals = JobSignals()
        self.cancel_event = threading.Event()

    def cancel(self) -> None:
        self.cancel_event.set()

    @property
    def cancelled_requested(self) -> bool:
        return self.cancel_event.is_set()

    @Slot()
    def run(self) -> None:
        try:
            self.signals.progress.emit(0)
            result = self.fn()
            if self.cancelled_requested:
                self.signals.cancelled.emit()
                return
            self.signals.progress.emit(100)
            self.signals.finished.emit(result)
        except Exception as exc:  # pragma: no cover - GUI error transport.
            if self.cancelled_requested:
                self.signals.cancelled.emit()
                return
            self.signals.failed.emit(str(exc))
