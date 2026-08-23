"""Thin page controllers for the Studio window.

The controllers own page routing and construction boundaries.  The window is
passed as a host so existing Qt signal handlers remain compatible while the
large legacy widget implementation is migrated incrementally.
"""
from __future__ import annotations


class _PageController:
    def __init__(self, host):
        self.host = host


class MainPageController(_PageController):
    def show(self):
        self.host.main_tabs.setCurrentIndex(self.host.disk_tab_index)


class FilesPageController(_PageController):
    def show(self):
        self.host.main_tabs.setCurrentIndex(self.host.files_tab_index)


class HexPageController(_PageController):
    def show(self, *, advanced=None):
        use_advanced = self.host.mode.currentIndex() == 1 if advanced is None else advanced
        self.host.hex_mode_stack.setCurrentIndex(1 if use_advanced else 0)
        self.host.main_tabs.setCurrentIndex(self.host.hex_tab_index)


class AdvancedPageController(_PageController):
    def show(self):
        if self.host.main_tabs.isTabEnabled(self.host.advanced_tab_index):
            self.host.main_tabs.setCurrentIndex(self.host.advanced_tab_index)


class JobsPageController(_PageController):
    def show(self):
        self.host.main_tabs.setCurrentIndex(self.host.jobs_tab_index)
