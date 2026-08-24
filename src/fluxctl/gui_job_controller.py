"""Controller for asynchronous Studio application operations."""
from __future__ import annotations

import time
from typing import Callable

from .gui_jobs import Job


class StudioJobController:
    """Own job lifecycle state while delegating presentation to the window."""

    def __init__(self, window) -> None:
        self.window = window

    def update_elapsed(self) -> None:
        window = self.window
        if window.current_job is None or window.current_job not in window.active_jobs:
            return
        elapsed = time.monotonic() - window._job_started_at
        label = window.job_status_label.text().split(" (")[0]
        window.job_status_label.setText(f"{label} ({elapsed:.1f}s)")

    def set_finished_state(self) -> None:
        window = self.window
        window._job_timer.stop()
        window.current_job = None
        window.job_cancel_button.setEnabled(False)
        window.job_progress.setVisible(False)
        window.job_status_label.setText("No active jobs")

    def cancel_current(self) -> None:
        window = self.window
        job = window.current_job
        if job is None or job not in window.active_jobs:
            return
        job.cancel()
        window.job_cancel_button.setEnabled(False)
        window.activity_label.setText("Cancellation requested; finishing the current operation...")
        window.job_status_label.setText("Cancellation requested")
        window._append_log("Cancellation requested for the active job.")

    def run(self, label: str, fn: Callable[[], object], done: Callable[[object], None], *, accepts_context: bool = False) -> None:
        window = self.window
        window._job_generation += 1
        generation = window._job_generation
        window.summary_labels["status"].setText("running")
        window.activity_label.setText(f"Running {label}...")
        window._append_log(f"$ {label}")
        job = Job(fn, accepts_context=accepts_context)
        window.active_jobs.add(job)
        window.current_job = job
        window._job_started_at = time.monotonic()
        window.job_status_label.setText(f"Running {label}")
        window.job_progress.setRange(0, 0)
        window.job_progress.setVisible(True)
        window.job_cancel_button.setEnabled(True)
        window._job_timer.start()
        job.signals.progress.connect(lambda value, current_job=job: self.show_progress(current_job, value))
        job.signals.progress_event.connect(
            lambda stage, current, total, current_job=job: self.show_progress_event(
                current_job, label, stage, current, total
            )
        )
        job.signals.finished.connect(
            lambda result, current_job=job: self.finish(current_job, generation, label, result, done)
        )
        job.signals.failed.connect(lambda message, current_job=job: self.fail(current_job, generation, label, message))
        job.signals.cancelled.connect(lambda current_job=job: self.cancelled(current_job, generation, label))
        window.thread_pool.start(job)

    def show_progress(self, job: Job, value: int) -> None:
        window = self.window
        if job is not window.current_job:
            return
        window.job_progress.setRange(0, 100)
        window.job_progress.setValue(value)

    def show_progress_event(self, job: Job, label: str, stage: str, current: int, total: int) -> None:
        window = self.window
        if job is not window.current_job:
            return
        if total > 0:
            window.job_progress.setRange(0, 100)
            window.job_progress.setValue(min(100, max(0, int(current * 100 / total))))
            detail = f"{current}/{total}"
        else:
            detail = str(current)
        window.activity_label.setText(f"Running {label}: {stage} {detail}")

    def finish(self, job: Job, generation: int, label: str, result: object, done: Callable[[object], None]) -> None:
        window = self.window
        window.active_jobs.discard(job)
        if job is window.current_job:
            self.set_finished_state()
        if generation != window._job_generation:
            window._append_log(f"Discarded stale result from {label}.")
            return
        window.activity_label.setText(f"Finished {label}.")
        if window.summary_labels["status"].text() == "running":
            window.summary_labels["status"].setText("ready")
        done(result)

    def fail(self, job: Job, generation: int, label: str, message: str) -> None:
        window = self.window
        window.active_jobs.discard(job)
        if job is window.current_job:
            self.set_finished_state()
        if generation != window._job_generation:
            window._append_log(f"Discarded stale error from {label}: {message}")
            return
        window.summary_labels["status"].setText("error")
        window.activity_label.setText(f"{label} failed: {message}")
        window._append_log(f"Error: {message}")

    def cancelled(self, job: Job, generation: int, label: str) -> None:
        window = self.window
        window.active_jobs.discard(job)
        if job is window.current_job:
            self.set_finished_state()
        if generation != window._job_generation:
            return
        window.summary_labels["status"].setText("ready")
        window.activity_label.setText(f"Cancelled {label}.")
        window._append_log(f"Cancelled {label}.")
