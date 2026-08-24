"""Cooperative cancellation and progress reporting for application operations."""
from __future__ import annotations

from dataclasses import dataclass
from threading import Event
from typing import Callable, Optional


class OperationCancelled(Exception):
    """Raised at a safe boundary when the caller requests cancellation."""


ProgressCallback = Callable[[str, int, int], None]


@dataclass
class OperationContext:
    cancel_event: Event
    progress_callback: Optional[ProgressCallback] = None

    def checkpoint(self, stage: str, current: int = 0, total: int = 0) -> None:
        if self.cancel_event.is_set():
            raise OperationCancelled("Operation cancelled")
        if self.progress_callback is not None:
            self.progress_callback(stage, current, total)


__all__ = ["OperationCancelled", "OperationContext"]
