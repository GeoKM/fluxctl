"""Qt job primitives used by Fluxctl Studio."""
from __future__ import annotations

import inspect
import threading
from typing import Callable

from PySide6.QtCore import QObject, QRunnable, Signal, Slot


class JobSignals(QObject):
    finished = Signal(object)
    failed = Signal(str)
    cancelled = Signal()
    progress = Signal(int)
    progress_event = Signal(str, int, int)


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

    def _emit_cancelled(self) -> None:
        """Ignore cancellation notifications after Qt has torn down the signals."""
        try:
            self.signals.cancelled.emit()
        except RuntimeError:
            # A QRunnable can outlive its QObject during application shutdown.
            pass

    @Slot()
    def run(self) -> None:
        try:
            self.signals.progress.emit(0)
            from .application.progress import OperationContext

            context = OperationContext(
                self.cancel_event,
                lambda stage, current, total: self.signals.progress_event.emit(stage, current, total),
            )
            result = self.fn(context) if inspect.signature(self.fn).parameters else self.fn()
            if self.cancelled_requested:
                self._emit_cancelled()
                return
            self.signals.progress.emit(100)
            self.signals.finished.emit(result)
        except Exception as exc:  # pragma: no cover - GUI error transport.
            if self.cancelled_requested:
                self._emit_cancelled()
                return
            self.signals.failed.emit(str(exc))
