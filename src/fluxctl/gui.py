"""Fluxctl Studio desktop application."""
from __future__ import annotations

import json
import math
import sys
import threading
import time
from pathlib import Path
from typing import Callable, Optional

from . import studio_services as services
from .application.compare_operations import compare_images
from .application.conversion_operations import convert_image, roundtrip_image
from .application.diagnostic_operations import summarize_image
from .application.diagnostic_operations import doctor_report, provenance_json
from .application.hardware_operations import greaseweazle_formats, greaseweazle_status, read_disk_with_greaseweazle
from .application.image_creation_operations import blank_image_presets, create_blank_image
from .application.filesystem_operations import (
    create_directory_with_copy,
    delete_filesystem_entry_with_copy,
    export_filesystem_entries,
    export_filesystem_entry,
    extract_file_to_path,
    file_allocation_for_image,
    file_hex_dump,
    import_directory_with_copy,
    import_file_with_copy,
    list_files,
    list_files_with_info,
    replace_file_bytes_with_copy,
    replace_file_with_copy,
    replace_flat_sector_bytes_with_copy,
    safe_export_name,
    sector_hex_dump,
    sector_list,
)
from .application.report_operations import (
    build_disk_map_for_image,
    build_qc_for_image,
    export_disk_map_svg,
    export_qc_json,
)


try:  # pragma: no cover - exercised only when GUI dependencies are installed.
    from PySide6.QtCore import QObject, QRunnable, QSettings, QStandardPaths, Qt, QThreadPool, Signal, Slot, QTimer
    from PySide6.QtGui import QAction, QColor, QFont, QPainter, QPen
    from PySide6.QtWidgets import (
        QApplication,
        QAbstractItemView,
        QComboBox,
        QDialog,
        QDialogButtonBox,
        QFileDialog,
        QFormLayout,
        QFrame,
        QGridLayout,
        QHBoxLayout,
        QHeaderView,
        QLabel,
        QLineEdit,
        QInputDialog,
        QMainWindow,
        QMessageBox,
        QPushButton,
        QProgressBar,
        QSpinBox,
        QStackedWidget,
        QTableWidget,
        QTableWidgetItem,
        QTabWidget,
        QTextEdit,
        QToolTip,
        QToolBar,
        QVBoxLayout,
        QWidget,
    )
except ImportError as exc:  # pragma: no cover - import guard.
    raise SystemExit(
        "Fluxctl Studio requires PySide6. Install it with `python -m pip install -e .[gui]`."
    ) from exc


class JobSignals(QObject):
    finished = Signal(object)
    failed = Signal(str)
    cancelled = Signal()
    progress = Signal(int)


class Job(QRunnable):
    def __init__(self, fn: Callable[[], object]):
        super().__init__()
        self.fn = fn
        self.signals = JobSignals()
        self.cancel_event = threading.Event()
        self.started_at = time.monotonic()

    def cancel(self) -> None:
        """Request cooperative cancellation; the result will be discarded."""

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
            try:
                self.signals.progress.emit(100)
                self.signals.finished.emit(result)
            except RuntimeError:
                pass
        except Exception as exc:  # pragma: no cover - GUI error transport.
            try:
                if self.cancelled_requested:
                    self.signals.cancelled.emit()
                    return
                self.signals.failed.emit(str(exc))
            except RuntimeError:
                pass


class HexDumpEditor(QTextEdit):
    """Editable Advanced-mode dump that synchronises columns on Enter."""

    syncRequested = Signal()

    def keyPressEvent(self, event) -> None:  # type: ignore[override]
        if event.key() in {Qt.Key_Return, Qt.Key_Enter} and not event.modifiers() & Qt.ShiftModifier:
            self.syncRequested.emit()
            event.accept()
            return
        super().keyPressEvent(event)


class DiskMapWidget(QWidget):
    sectorClicked = Signal(int, int, int)
    STATE_COLORS = {
        "good": QColor("#35d07f"),
        "weak": QColor("#f2c94c"),
        "bad": QColor("#e05a47"),
        "unused": QColor("#4f5b6f"),
        "bam_file": QColor("#35d07f"),
        "bam_system": QColor("#4aa3ff"),
        "bam_used": QColor("#f2c94c"),
        "bam_free": QColor("#4f5b6f"),
    }
    HIGHLIGHT_COLOR = QColor("#ff8a3d")
    LEGEND_LABELS = {
        "good": "Good",
        "weak": "Weak",
        "bad": "Bad",
        "unused": "Unused/free",
        "bam_file": "File",
        "bam_system": "System",
        "bam_used": "Allocated",
        "bam_free": "Free",
    }

    def __init__(self) -> None:
        super().__init__()
        self.disk_map = None
        self._head_layouts: list[dict[str, object]] = []
        self.setMouseTracking(True)
        self.setMinimumHeight(520)

    def set_disk_map(self, disk_map) -> None:
        self.disk_map = disk_map
        self.update()

    def legend_items(self) -> list[tuple[str, str]]:
        if self.disk_map and getattr(self.disk_map, "render_style", "radial") == "grid":
            items = [(state, self.LEGEND_LABELS[state]) for state in ("bam_file", "bam_system", "bam_used", "bam_free")]
        else:
            items = [(state, self.LEGEND_LABELS[state]) for state in ("good", "weak", "bad", "unused")]
        if self.disk_map and getattr(self.disk_map, "highlighted_sectors", None):
            items.append(("selected_file", "Selected file"))
        return items

    @staticmethod
    def head_groups(disk_map) -> list[tuple[int, list[tuple[int, tuple[int, int], list[str]]]]]:
        """Group map rows by physical head while preserving track order."""

        if not disk_map or not disk_map.tracks:
            return []
        track_ids = disk_map.track_ids or [(idx, 0) for idx, _ in enumerate(disk_map.tracks)]
        grouped: dict[int, list[tuple[int, tuple[int, int], list[str]]]] = {}
        for row_index, (track_id, sectors) in enumerate(zip(track_ids, disk_map.tracks)):
            track, head = track_id
            grouped.setdefault(head, []).append((row_index, (track, head), sectors))
        return [
            (head, sorted(rows, key=lambda item: item[1][0]))
            for head, rows in sorted(grouped.items(), key=lambda item: item[0])
        ]

    def sector_detail_text(self, row_index: int, sector_index: int) -> str:
        if not self.disk_map:
            return ""
        track_ids = self.disk_map.track_ids or [(idx, 0) for idx, _ in enumerate(self.disk_map.tracks)]
        track, head = track_ids[row_index]
        detail = None
        if self.disk_map.sector_details and row_index < len(self.disk_map.sector_details):
            details = self.disk_map.sector_details[row_index]
            if sector_index < len(details):
                detail = details[sector_index]
        state = self.disk_map.tracks[row_index][sector_index]
        if detail is None:
            return f"Track {track} Head {head}\nSector position {sector_index + 1}\nState: {state}"
        crc = "n/a" if detail.state == "unused" or detail.state.startswith("bam_") else ("ok" if detail.crc_ok else "bad")
        state_label = self.LEGEND_LABELS.get(detail.state, detail.state)
        data = "yes" if detail.has_data else "no"
        deleted = "yes" if detail.deleted else "no"
        return (
            f"Track {track}  Head {head}\n"
            f"Sector ID {detail.sector_id}  Position {sector_index + 1}\n"
            f"State: {state_label}  CRC: {crc}\n"
            f"Confidence: {detail.confidence:.2f}\n"
            f"Size: {detail.size} bytes  Data: {data}  Deleted: {deleted}"
        )

    def paintEvent(self, _event) -> None:  # pragma: no cover - visual rendering.
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.fillRect(self.rect(), QColor("#0c1018"))
        self._head_layouts = []
        if not self.disk_map or not self.disk_map.tracks:
            painter.setPen(QPen(QColor("#788296"), 1))
            painter.drawText(self.rect(), Qt.AlignCenter, "Open an image to render the disk map")
            return

        head_groups = self.head_groups(self.disk_map)
        if not head_groups:
            return
        if getattr(self.disk_map, "render_style", "radial") == "grid":
            self._paint_grid_map(painter, width=self.width(), height=self.height(), head_groups=head_groups)
            return

        width = self.width()
        height = self.height()
        colors = self.STATE_COLORS
        columns = len(head_groups)
        gap = 28
        label_height = 34
        legend_height = 34
        column_width = max((width - gap * (columns + 1)) / columns, 1)
        usable_height = max(height - label_height - legend_height - 20, 1)

        for column, (head, rows) in enumerate(head_groups):
            left = gap + column * (column_width + gap)
            cx = left + column_width / 2
            cy = label_height + usable_height / 2
            max_radius = min(column_width, usable_height) * 0.49
            track_count = max(len(rows), 1)
            ring_width = max(max_radius / track_count, 2.0)
            self._head_layouts.append(
                {
                    "head": head,
                    "cx": cx,
                    "cy": cy,
                    "max_radius": max_radius,
                    "ring_width": ring_width,
                    "rows": rows,
                }
            )

            painter.setPen(QPen(QColor("#dce7f7"), 1))
            painter.setFont(QFont("Arial", 12, QFont.Bold))
            painter.drawText(int(left), 8, int(column_width), 24, Qt.AlignCenter, f"Head {head}")
            for track_idx, (_row_index, _track_id, sectors) in enumerate(rows):
                radius = max_radius - (track_idx * ring_width)
                if radius <= 4:
                    break
                sector_count = max(len(sectors), 1)
                for sector_idx, state in enumerate(sectors):
                    painter.setBrush(colors.get(state, QColor("#6b7280")))
                    pen = QPen(self.HIGHLIGHT_COLOR, 3.0) if self._sector_is_highlighted(_row_index, sector_idx) else QPen(QColor("#101823"), 1.35)
                    painter.setPen(pen)
                    start = int((90 - (360 * sector_idx / sector_count)) * 16)
                    span = int(-(360 / sector_count) * 16)
                    rect_size = radius * 2
                    painter.drawPie(
                        int(cx - radius),
                        int(cy - radius),
                        int(rect_size),
                        int(rect_size),
                        start,
                        span,
                    )
                painter.setBrush(QColor("#0c1018"))
                painter.setPen(QPen(QColor("#1f2b3a"), 1.2))
                inner_radius = max(radius - ring_width + 1, 0)
                painter.drawEllipse(
                    int(cx - inner_radius),
                    int(cy - inner_radius),
                    int(inner_radius * 2),
                    int(inner_radius * 2),
                )
        self._draw_legend(painter, width, height)

    def _paint_grid_map(
        self,
        painter: QPainter,
        *,
        width: int,
        height: int,
        head_groups: list[tuple[int, list[tuple[int, tuple[int, int], list[str]]]]],
    ) -> None:  # pragma: no cover - visual rendering.
        self._head_layouts = []
        if not any(rows for _head, rows in head_groups):
            return
        outer_gap = 18
        column_gap = 28
        top = 44
        legend_height = 36
        row_label_width = 40
        panes = self.grid_panes(head_groups, self.disk_map)
        columns = max(len(panes), 1)
        column_width = max((width - outer_gap * 2 - column_gap * (columns - 1)) / columns, 1)
        grid_height = max(height - top - legend_height - 12, 1)

        painter.setFont(QFont("Arial", 10))
        painter.setPen(QPen(QColor("#dce7f7"), 1))
        for column, (head, title, rows) in enumerate(panes):
            if not rows:
                continue
            left = outer_gap + column * (column_width + column_gap)
            grid_width = max(column_width - row_label_width, 1)
            max_cols = max(len(sectors) for _row_index, _track_id, sectors in rows)
            cell = max(4.0, min(grid_width / max(max_cols, 1), grid_height / max(len(rows), 1)))
            cell = min(cell, 18.0)
            painter.setPen(QPen(QColor("#dce7f7"), 1))
            painter.setFont(QFont("Arial", 12, QFont.Bold))
            painter.drawText(int(left), 8, int(column_width), 24, Qt.AlignCenter, title)
            painter.setFont(QFont("Arial", 10))
            for display_row, (row_index, (track, _head), sectors) in enumerate(rows):
                y = top + display_row * cell
                painter.setPen(QPen(QColor("#dce7f7"), 1))
                painter.drawText(
                    int(left),
                    int(y),
                    row_label_width - 4,
                    int(cell),
                    Qt.AlignRight | Qt.AlignVCenter,
                    self.track_label(track, self.disk_map),
                )
                for sector_index, state in enumerate(sectors):
                    x = left + row_label_width + sector_index * cell
                    painter.setBrush(self.STATE_COLORS.get(state, QColor("#6b7280")))
                    pen = QPen(self.HIGHLIGHT_COLOR, 2.2) if self._sector_is_highlighted(row_index, sector_index) else QPen(QColor("#111b28"), 1.25)
                    painter.setPen(pen)
                    painter.drawRect(int(x), int(y), max(int(cell - 1), 1), max(int(cell - 1), 1))

            self._head_layouts.append(
                {
                    "head": head,
                    "grid": True,
                    "left": left + row_label_width,
                    "top": top,
                    "cell": cell,
                    "rows": rows,
                }
            )
        self._draw_legend(painter, width, height)

    @classmethod
    def grid_panes(
        cls,
        head_groups: list[tuple[int, list[tuple[int, tuple[int, int], list[str]]]]],
        disk_map: DiskMap | None,
    ) -> list[tuple[int, str, list[tuple[int, tuple[int, int], list[str]]]]]:
        panes: list[tuple[int, str, list[tuple[int, tuple[int, int], list[str]]]]] = []
        for head, rows in head_groups:
            if not rows:
                continue
            chunk_size = 40 if len(rows) > 45 else len(rows)
            chunks = [rows[index : index + chunk_size] for index in range(0, len(rows), chunk_size)]
            for chunk in chunks:
                title = f"Head {head}"
                if len(chunks) > 1:
                    start_track = chunk[0][1][0]
                    end_track = chunk[-1][1][0]
                    title = f"{title} {cls.track_label(start_track, disk_map)}-{cls.track_label(end_track, disk_map)}"
                panes.append((head, title, chunk))
        return panes

    @staticmethod
    def track_label(track: int, disk_map: DiskMap | None) -> str:
        if disk_map is not None and getattr(disk_map, "address_style", "physical") == "cbm_logical":
            return f"T{track:02d}"
        return f"T{track + 1:02d}"

    def _sector_is_highlighted(self, row_index: int, sector_index: int) -> bool:
        if not self.disk_map or not getattr(self.disk_map, "highlighted_sectors", None):
            return False
        track_ids = self.disk_map.track_ids or [(idx, 0) for idx, _ in enumerate(self.disk_map.tracks)]
        if row_index >= len(track_ids) or not self.disk_map.sector_details or row_index >= len(self.disk_map.sector_details):
            return False
        details = self.disk_map.sector_details[row_index]
        if sector_index >= len(details):
            return False
        track, head = track_ids[row_index]
        return (track, head, details[sector_index].sector_id) in self.disk_map.highlighted_sectors

    def _draw_legend(self, painter: QPainter, width: int, height: int) -> None:  # pragma: no cover - visual rendering.
        painter.setFont(QFont("Arial", 11))
        painter.setPen(QPen(QColor("#dce7f7"), 1))
        y = max(height - 28, 8)
        x = 18
        for state, label in self.legend_items():
            if state == "selected_file":
                painter.setBrush(Qt.NoBrush)
                painter.setPen(QPen(self.HIGHLIGHT_COLOR, 2.2))
                painter.drawRoundedRect(x, y + 4, 14, 14, 3, 3)
            else:
                painter.setBrush(self.STATE_COLORS[state])
                painter.setPen(Qt.NoPen)
                painter.drawRoundedRect(x, y + 4, 14, 14, 3, 3)
            painter.setPen(QPen(QColor("#dce7f7"), 1))
            painter.drawText(x + 20, y, max(width - x - 20, 1), 24, Qt.AlignLeft | Qt.AlignVCenter, label)
            x += 138 if state == "selected_file" else (116 if state != "unused" else 150)

    def mouseMoveEvent(self, event) -> None:  # pragma: no cover - GUI interaction.
        hit = self._hit_test(event.position().x(), event.position().y())
        if hit is None:
            QToolTip.hideText()
            return
        row_index, sector_index = hit
        QToolTip.showText(event.globalPosition().toPoint(), self.sector_detail_text(row_index, sector_index), self)

    def leaveEvent(self, _event) -> None:  # pragma: no cover - GUI interaction.
        QToolTip.hideText()

    def mousePressEvent(self, event) -> None:  # pragma: no cover - GUI interaction.
        if event.button() != Qt.LeftButton:
            return
        address = self.sector_address_at(event.position().x(), event.position().y())
        if address is not None:
            track, head, sector_id = address
            self.sectorClicked.emit(track, head, sector_id)

    def sector_address_at(self, x: float, y: float) -> Optional[tuple[int, int, int]]:
        hit = self._hit_test(x, y)
        if hit is None or not self.disk_map:
            return None
        row_index, sector_index = hit
        track_ids = self.disk_map.track_ids or [(idx, 0) for idx, _ in enumerate(self.disk_map.tracks)]
        if row_index >= len(track_ids):
            return None
        track, head = track_ids[row_index]
        if not self.disk_map.sector_details or row_index >= len(self.disk_map.sector_details):
            return None
        details = self.disk_map.sector_details[row_index]
        if sector_index >= len(details):
            return None
        detail = details[sector_index]
        if detail.state.startswith("bam_") or not detail.has_data:
            return None
        return track, head, detail.sector_id

    def _hit_test(self, x: float, y: float) -> Optional[tuple[int, int]]:
        for layout in self._head_layouts:
            if layout.get("grid"):
                cell = float(layout["cell"])
                col = int((x - float(layout["left"])) // cell)
                row = int((y - float(layout["top"])) // cell)
                rows = layout["rows"]
                if row < 0 or row >= len(rows):
                    continue
                row_index, _track_id, sectors = rows[row]
                if col < 0 or col >= len(sectors):
                    continue
                return row_index, col
            dx = x - float(layout["cx"])
            dy = y - float(layout["cy"])
            distance = math.hypot(dx, dy)
            max_radius = float(layout["max_radius"])
            ring_width = float(layout["ring_width"])
            if distance > max_radius or distance <= max(max_radius - ring_width * len(layout["rows"]), 0):
                continue
            track_idx = int((max_radius - distance) // ring_width)
            rows = layout["rows"]
            if track_idx < 0 or track_idx >= len(rows):
                continue
            row_index, _track_id, sectors = rows[track_idx]
            if not sectors:
                continue
            angle = (90 - math.degrees(math.atan2(dy, dx))) % 360
            sector_index = int(angle / (360 / len(sectors)))
            sector_index = max(0, min(sector_index, len(sectors) - 1))
            return int(row_index), sector_index
        return None


class FluxctlStudio(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Fluxctl Studio")
        self.resize(1440, 900)
        self.settings = QSettings("GeoKM", "FluxctlStudio")
        self.thread_pool = QThreadPool.globalInstance()
        self.active_jobs: set[Job] = set()
        self.current_job: Optional[Job] = None
        self._job_generation = 0
        self._job_started_at = 0.0
        self._job_timer = QTimer(self)
        self._job_timer.setInterval(250)
        self._job_timer.timeout.connect(self._update_job_elapsed)
        self.current_path: Optional[Path] = None
        self.current_summary = None
        self.file_browser_path = "/"
        self.advanced_file_browser_path = "/"
        self._loading_advanced_file_paths = False
        self.layout_options = services.load_layout_options()
        self.blank_image_presets = blank_image_presets()
        self.greaseweazle_status = greaseweazle_status()
        self.greaseweazle_formats = greaseweazle_formats()
        self._advanced_hex_dump: Optional[services.HexDumpView] = None
        self._build_ui()
        self._restore_settings()
        self._apply_style()
        self._update_hardware_controls()
        self._update_filesystem_write_actions()
        self._update_advanced_context()
        self.run_doctor()

    def _build_ui(self) -> None:
        open_action = QAction("Open", self)
        open_action.triggered.connect(self.open_image)
        self.toolbar = QToolBar("Main")
        self.toolbar.addAction(open_action)
        self.addToolBar(self.toolbar)

        root = QWidget()
        root_layout = QVBoxLayout(root)
        root_layout.setContentsMargins(0, 0, 0, 0)

        self.sidebar = QFrame()
        self.sidebar.setObjectName("sidebar")
        self.sidebar.setMinimumWidth(340)
        self.sidebar.setMaximumWidth(420)
        sidebar_layout = QVBoxLayout(self.sidebar)
        self.title = QLabel("Fluxctl Studio")
        self.title.setObjectName("title")
        self.file_label = QLabel("No image loaded")
        self.file_label.setWordWrap(True)
        self.summary_grid = QGridLayout()
        self.summary_grid.setColumnStretch(0, 0)
        self.summary_grid.setColumnStretch(1, 1)
        self.summary_labels = {}
        for row, label in enumerate(["Layout", "Encoding", "Filesystem", "Confidence", "Size", "Status"]):
            key = label.lower()
            field = QLabel(label)
            field.setObjectName("metricName")
            self.summary_grid.addWidget(field, row, 0)
            value = QLabel("-")
            value.setObjectName("metric")
            value.setWordWrap(True)
            self.summary_labels[key] = value
            self.summary_grid.addWidget(value, row, 1)
        self.mode = QComboBox()
        self.mode.addItems(["Simple Mode", "Advanced Mode"])
        self.mode.currentIndexChanged.connect(self._switch_mode)
        self.map_view = QComboBox()
        self.map_view.addItem("Filesystem Logical Map", "logical")
        self.map_view.addItem("Whole Physical Disk Map", "physical")
        self.map_view.addItem("CBM DOS BAM Block Map", "bam")
        self.map_view.currentIndexChanged.connect(lambda _index: self.run_map() if self.current_path else None)
        self.doctor_button = QPushButton("Run Doctor")
        self.doctor_button.clicked.connect(self.run_doctor)
        self.open_button = QPushButton("Open Image")
        self.open_button.clicked.connect(self.open_image)
        self.blank_image_combo = QComboBox()
        for preset in self.blank_image_presets:
            self.blank_image_combo.addItem(preset.label, preset.preset_id)
            self.blank_image_combo.setItemData(self.blank_image_combo.count() - 1, preset.description, Qt.ToolTipRole)
        self.blank_image_combo.currentIndexChanged.connect(self._update_blank_image_tooltip)
        self.create_blank_button = QPushButton("Create Blank...")
        self.create_blank_button.clicked.connect(self.create_blank_image_dialog)
        hardware_section = self._build_hardware_section()
        sidebar_layout.addWidget(self.title)
        sidebar_layout.addWidget(self.file_label)
        sidebar_layout.addLayout(self.summary_grid)
        sidebar_layout.addWidget(self.mode)
        sidebar_layout.addWidget(self.map_view)
        sidebar_layout.addWidget(self.open_button)
        sidebar_layout.addWidget(self.blank_image_combo)
        sidebar_layout.addWidget(self.create_blank_button)
        sidebar_layout.addWidget(hardware_section)
        sidebar_layout.addWidget(self.doctor_button)
        sidebar_layout.addStretch(1)
        self._update_blank_image_tooltip()

        self.simple = self._build_simple_mode()
        self.advanced = self._build_advanced_mode()

        self.main_page = QWidget()
        main_page_layout = QHBoxLayout(self.main_page)
        main_page_layout.setContentsMargins(0, 0, 0, 0)
        main_page_layout.addWidget(self.sidebar, 0)
        main_page_layout.addWidget(self.simple, 1)

        self.hex_mode_stack = QStackedWidget()
        self.hex_mode_stack.addWidget(self.hex_panel)
        self.hex_mode_stack.addWidget(self.advanced_hex_panel)
        self.hex_page = QWidget()
        hex_page_layout = QVBoxLayout(self.hex_page)
        hex_page_layout.setContentsMargins(8, 8, 8, 8)
        hex_page_layout.addWidget(self.hex_mode_stack)

        self.jobs_page = QWidget()
        jobs_layout = QVBoxLayout(self.jobs_page)
        jobs_layout.setContentsMargins(8, 8, 8, 8)
        job_controls = QHBoxLayout()
        self.job_status_label = QLabel("No active jobs")
        self.job_progress = QProgressBar()
        self.job_progress.setRange(0, 0)
        self.job_progress.setVisible(False)
        self.job_cancel_button = QPushButton("Cancel Job")
        self.job_cancel_button.setEnabled(False)
        self.job_cancel_button.clicked.connect(self.cancel_current_job)
        job_controls.addWidget(self.job_status_label)
        job_controls.addWidget(self.job_progress, 1)
        job_controls.addWidget(self.job_cancel_button)
        jobs_layout.addLayout(job_controls)
        jobs_layout.addWidget(self.log)

        self.main_tabs = QTabWidget()
        self.disk_tab_index = self.main_tabs.addTab(self.main_page, "Disk && Imaging")
        self.files_tab_index = self.main_tabs.addTab(self.file_panel, "Files && Directories")
        self.hex_tab_index = self.main_tabs.addTab(self.hex_page, "HEX && ASCII")
        self.advanced_tab_index = self.main_tabs.addTab(self.advanced, "Advanced")
        self.jobs_tab_index = self.main_tabs.addTab(self.jobs_page, "Jobs && Logs")
        self.main_tabs.setTabToolTip(self.advanced_tab_index, "Available when Advanced Mode is selected.")

        root_layout.addWidget(self.activity_label)
        root_layout.addWidget(self.main_tabs, 1)
        self.setCentralWidget(root)
        self._switch_mode(self.mode.currentIndex())

    def _build_hardware_section(self) -> QWidget:
        section = QFrame()
        section.setObjectName("sidebarSection")
        layout = QVBoxLayout(section)
        layout.setContentsMargins(0, 8, 0, 0)

        title = QLabel("Hardware")
        title.setObjectName("sectionTitle")
        self.greaseweazle_status_label = QLabel("Greaseweazle: checking")
        self.greaseweazle_status_label.setWordWrap(True)
        self.greaseweazle_drive_combo = QComboBox()
        for drive in ["A", "B", "0", "1", "2", "3"]:
            self.greaseweazle_drive_combo.addItem(f"Drive {drive}", drive)
        self.greaseweazle_image_combo = QComboBox()
        self.greaseweazle_image_combo.addItem("Raw flux SCP (.scp)", "scp")
        self.greaseweazle_format_combo = QComboBox()
        self.greaseweazle_format_combo.addItem("Auto / no format", "")
        for gw_format in self.greaseweazle_formats:
            self.greaseweazle_format_combo.addItem(gw_format.label, gw_format.format_id)
        self.greaseweazle_format_combo.setEditable(True)
        self.greaseweazle_tracks_input = QLineEdit()
        self.greaseweazle_tracks_input.setPlaceholderText("Track override, e.g. c=0-79:h=0-1")
        self.greaseweazle_revs_input = QLineEdit()
        self.greaseweazle_revs_input.setPlaceholderText("Revs, default")
        self.greaseweazle_read_button = QPushButton("Read Disk...")
        self.greaseweazle_read_button.clicked.connect(self.read_disk_with_greaseweazle_dialog)
        self.greaseweazle_write_button = QPushButton("Write Disk...")
        self.greaseweazle_write_button.setEnabled(False)
        self.greaseweazle_write_button.setToolTip(
            "Write-to-disk is not enabled yet. It needs destructive confirmation and read-back verification."
        )

        layout.addWidget(title)
        layout.addWidget(self.greaseweazle_status_label)
        layout.addWidget(self.greaseweazle_drive_combo)
        layout.addWidget(self.greaseweazle_image_combo)
        layout.addWidget(self.greaseweazle_format_combo)
        layout.addWidget(self.greaseweazle_tracks_input)
        layout.addWidget(self.greaseweazle_revs_input)
        layout.addWidget(self.greaseweazle_read_button)
        layout.addWidget(self.greaseweazle_write_button)
        return section

    def _build_simple_mode(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        self.activity_label = QLabel("Ready")
        self.activity_label.setObjectName("activity")
        self.activity_label.setWordWrap(True)

        actions = QHBoxLayout()
        for text, handler in [
            ("Probe", self.run_probe),
            ("QC Report", self.run_qc),
            ("Render Map", self.run_map),
            ("List Files", self.run_list_files),
            ("Convert...", self.convert_dialog),
            ("Round Trip...", self.roundtrip_dialog),
        ]:
            button = QPushButton(text)
            button.clicked.connect(handler)
            actions.addWidget(button)
        self.map_widget = DiskMapWidget()
        self.map_widget.sectorClicked.connect(self.load_sector_hex_from_map)
        self.file_path_label = QLabel("/")
        self.file_path_label.setObjectName("filePath")
        self.file_path_label.setWordWrap(True)
        self.file_volume_label = QLabel("")
        self.file_volume_label.setObjectName("filePath")
        self.file_volume_label.setWordWrap(True)
        self.file_up_button = QPushButton("Up")
        self.file_up_button.clicked.connect(self.open_parent_directory)
        self.file_root_button = QPushButton("Root")
        self.file_root_button.clicked.connect(self.open_root_directory)
        self.file_hex_button = QPushButton("View File Hex")
        self.file_hex_button.clicked.connect(self.view_selected_file_hex)
        self.file_export_button = QPushButton("Export Selected...")
        self.file_export_button.clicked.connect(self.export_selected_file_entry)
        self.file_replace_button = QPushButton("Replace With Copy...")
        self.file_replace_button.clicked.connect(self.replace_selected_file_with_copy)
        self.file_delete_button = QPushButton("Delete From Copy...")
        self.file_delete_button.clicked.connect(self.delete_selected_entry_with_copy)
        self.file_import_button = QPushButton("Import File...")
        self.file_import_button.clicked.connect(self.import_file_into_copy)
        self.directory_import_button = QPushButton("Import Directory...")
        self.directory_import_button.clicked.connect(self.import_directory_into_copy)
        self.directory_create_button = QPushButton("New Directory...")
        self.directory_create_button.clicked.connect(self.create_directory_in_copy)
        file_nav = QHBoxLayout()
        file_nav.addWidget(QLabel("Directory"))
        file_nav.addWidget(self.file_path_label, 1)
        file_nav.addWidget(self.file_up_button)
        file_nav.addWidget(self.file_root_button)
        file_nav.addWidget(self.file_hex_button)
        file_nav.addWidget(self.file_export_button)
        file_nav.addWidget(self.file_replace_button)
        file_nav.addWidget(self.file_delete_button)
        file_nav.addWidget(self.file_import_button)
        file_nav.addWidget(self.directory_import_button)
        file_nav.addWidget(self.directory_create_button)
        self.files_table = QTableWidget(0, 3)
        self.files_table.setMinimumHeight(340)
        self.files_table.setHorizontalHeaderLabels(["Name", "Kind", "Size"])
        self.files_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.files_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.files_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.files_table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.files_table.itemDoubleClicked.connect(self.open_selected_file_entry)
        self.files_table.itemSelectionChanged.connect(self.highlight_selected_file_on_map)
        self.file_panel = QWidget()
        file_panel_layout = QVBoxLayout(self.file_panel)
        file_panel_layout.setContentsMargins(0, 0, 0, 0)
        file_panel_layout.addLayout(file_nav)
        file_panel_layout.addWidget(self.file_volume_label)
        file_panel_layout.addWidget(self.files_table)
        self.log = QTextEdit()
        self.log.setReadOnly(True)
        self.log.setMinimumHeight(340)
        self.hex_title_label = QLabel("No hex data loaded")
        self.hex_title_label.setObjectName("filePath")
        self.hex_track_input = QSpinBox()
        self.hex_track_input.setObjectName("hexChsInput")
        self.hex_track_input.setRange(0, 999)
        self.hex_track_input.setValue(0)
        self.hex_track_input.setToolTip("Track number")
        self.hex_head_input = QSpinBox()
        self.hex_head_input.setObjectName("hexChsInput")
        self.hex_head_input.setRange(0, 1)
        self.hex_head_input.setValue(0)
        self.hex_head_input.setToolTip("Head number")
        self.hex_sector_input = QSpinBox()
        self.hex_sector_input.setObjectName("hexChsInput")
        self.hex_sector_input.setRange(0, 99)
        self.hex_sector_input.setValue(1)
        self.hex_sector_input.setToolTip("Sector ID")
        for input_widget in (
            self.hex_track_input,
            self.hex_head_input,
            self.hex_sector_input,
        ):
            input_widget.valueChanged.connect(self._auto_view_sector_hex)
        self.hex_sector_button = QPushButton("View Sector Hex")
        self.hex_sector_button.setObjectName("hexSectorButton")
        self.hex_sector_button.clicked.connect(self.view_sector_hex)
        hex_controls = QHBoxLayout()
        for label_text, input_widget in (
            ("Track", self.hex_track_input),
            ("Head", self.hex_head_input),
            ("Sector", self.hex_sector_input),
        ):
            label = QLabel(label_text)
            label.setObjectName("hexChsLabel")
            hex_controls.addWidget(label)
            hex_controls.addWidget(input_widget)
        hex_controls.addWidget(self.hex_sector_button)
        self.hex_text = QTextEdit()
        self.hex_text.setReadOnly(True)
        self.hex_text.setFont(QFont("Menlo"))
        self.hex_panel = QWidget()
        hex_panel_layout = QVBoxLayout(self.hex_panel)
        hex_panel_layout.setContentsMargins(0, 0, 0, 0)
        hex_panel_layout.addWidget(self.hex_title_label)
        hex_panel_layout.addLayout(hex_controls)
        hex_panel_layout.addWidget(self.hex_text)

        layout.addLayout(actions)
        self.map_canvas_panel = QWidget()
        map_canvas_layout = QVBoxLayout(self.map_canvas_panel)
        map_canvas_layout.setContentsMargins(0, 0, 0, 0)
        map_canvas_layout.addWidget(self.map_widget, 1)
        layout.addWidget(self.map_canvas_panel, 1)
        return page

    def _build_advanced_mode(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        controls = QGridLayout()
        self.layout_combo = QComboBox()
        self.layout_combo.addItem("Auto / none", "")
        for layout_option in self.layout_options:
            self.layout_combo.addItem(
                f"{layout_option['layout_id']}  ({layout_option['encoding']}, {layout_option['tracks']}T/{layout_option['sides']}H)",
                layout_option["layout_id"],
            )
        self.encoding_combo = QComboBox()
        self.encoding_combo.addItems(["mfm", "fm", "gcr", "auto"])
        self.export_combo = QComboBox()
        self.export_combo.addItems(["raw", "imd", "adf", "d64", "d71", "d81", "g64"])
        self.dump_mode_combo = QComboBox()
        self.dump_mode_combo.addItem("Sector", "sector")
        self.dump_mode_combo.addItem("File", "file")
        self.track_input = QLineEdit("0")
        self.head_input = QLineEdit("0")
        self.sector_input = QLineEdit("1")
        self.file_path_input = QComboBox()
        self.file_path_input.setEditable(True)
        self.file_path_input.activated.connect(self._advanced_file_path_activated)
        self.patch_payload_input = QLineEdit("")
        controls.addWidget(QLabel("Layout"), 0, 0)
        controls.addWidget(self.layout_combo, 0, 1, 1, 3)
        controls.addWidget(QLabel("Encoding"), 1, 0)
        controls.addWidget(self.encoding_combo, 1, 1)
        controls.addWidget(QLabel("Exporter"), 1, 2)
        controls.addWidget(self.export_combo, 1, 3)
        controls.addWidget(QLabel("Dump Mode"), 2, 0)
        controls.addWidget(self.dump_mode_combo, 2, 1, 1, 3)
        controls.addWidget(QLabel("Track / Head / Sector"), 3, 0)
        controls.addWidget(self.track_input, 3, 1)
        controls.addWidget(self.head_input, 3, 2)
        controls.addWidget(self.sector_input, 3, 3)
        controls.addWidget(QLabel("File Path"), 4, 0)
        controls.addWidget(self.file_path_input, 4, 1, 1, 3)
        controls.addWidget(QLabel("Patch Hex"), 5, 0)
        controls.addWidget(self.patch_payload_input, 5, 1, 1, 3)

        buttons = QHBoxLayout()
        self.advanced_image_buttons: list[QPushButton] = []
        for text, handler in [
            ("Info", self.run_info),
            ("Sectors", self.run_sectors),
            ("Dump", self.run_dump),
            ("QC JSON...", self.qc_export_dialog),
            ("SVG Map...", self.svg_export_dialog),
            ("Extract...", self.extract_dialog),
            ("Patch...", self.patch_dialog),
            ("Compare...", self.compare_dialog),
            ("Convert...", self.convert_dialog),
            ("Round Trip...", self.roundtrip_dialog),
            ("Open Provenance...", self.open_provenance),
        ]:
            button = QPushButton(text)
            button.clicked.connect(handler)
            buttons.addWidget(button)
            if text != "Open Provenance...":
                self.advanced_image_buttons.append(button)

        self.advanced_output = QTextEdit()
        self.advanced_output.setReadOnly(True)
        self.advanced_hex_title_label = QLabel("No hex data loaded")
        self.advanced_hex_title_label.setObjectName("filePath")
        self.advanced_hex_text = HexDumpEditor()
        self.advanced_hex_text.setReadOnly(False)
        self.advanced_hex_text.setFont(QFont("Menlo"))
        self.advanced_hex_text.syncRequested.connect(self.synchronize_advanced_hex_columns)
        self.advanced_hex_save_button = QPushButton("Save Edited Copy...")
        self.advanced_hex_save_button.clicked.connect(self.save_advanced_hex_edit)
        self.advanced_hex_revert_button = QPushButton("Revert")
        self.advanced_hex_revert_button.clicked.connect(self.revert_advanced_hex_edit)
        advanced_hex_actions = QHBoxLayout()
        advanced_hex_actions.addWidget(self.advanced_hex_save_button)
        advanced_hex_actions.addWidget(self.advanced_hex_revert_button)
        advanced_hex_actions.addStretch(1)
        self.advanced_hex_panel = QWidget()
        advanced_hex_layout = QVBoxLayout(self.advanced_hex_panel)
        advanced_hex_layout.setContentsMargins(0, 0, 0, 0)
        advanced_hex_layout.addWidget(self.advanced_hex_title_label)
        advanced_hex_layout.addLayout(advanced_hex_actions)
        advanced_hex_layout.addWidget(self.advanced_hex_text)
        self.advanced_detail_stack = QStackedWidget()
        self.advanced_detail_stack.addWidget(self.advanced_output)
        layout.addLayout(controls)
        layout.addLayout(buttons)
        layout.addWidget(self.advanced_detail_stack, 1)
        return page

    def _apply_style(self) -> None:
        self.setStyleSheet(
            """
            QMainWindow, QWidget { background: #111722; color: #e7edf7; font-size: 13px; }
            #sidebar { background: #090d14; min-width: 340px; max-width: 420px; border-right: 1px solid #263241; }
            #sidebarSection { border-top: 1px solid #263241; margin-top: 8px; }
            #title { font-size: 24px; font-weight: 700; margin-bottom: 10px; }
            #sectionTitle { font-size: 16px; font-weight: 700; color: #dce7f7; }
            QLabel#metricName { color: #c9d4e5; font-weight: 600; padding: 3px 6px 3px 0; }
            QLabel#metric { color: #9ee6b8; font-weight: 600; }
            QLabel#activity { background: #172233; border: 1px solid #2f4158; border-radius: 6px; padding: 8px; color: #dce7f7; }
            QLabel#filePath { color: #9ee6b8; font-weight: 600; padding: 4px 8px; }
            QPushButton { background: #243348; border: 1px solid #40536c; border-radius: 6px; padding: 8px 10px; }
            QPushButton:hover { background: #2f435f; }
            QSpinBox#hexChsInput {
                min-height: 52px;
                min-width: 92px;
                font-size: 18px;
                font-weight: 600;
                padding: 4px 8px;
            }
            QSpinBox#hexChsInput::up-button,
            QSpinBox#hexChsInput::down-button {
                width: 30px;
                height: 26px;
            }
            QLabel#hexChsLabel {
                font-size: 16px;
                font-weight: 600;
                padding-left: 6px;
            }
            QPushButton#hexSectorButton {
                min-height: 52px;
                font-size: 16px;
                padding-left: 16px;
                padding-right: 16px;
            }
            QPushButton:disabled {
                background: #151b25;
                border: 1px solid #253142;
                color: #697386;
            }
            QComboBox, QLineEdit, QTextEdit, QTableWidget {
                background: #0c1018; border: 1px solid #2d3a4b; border-radius: 6px; padding: 6px;
            }
            QComboBox:disabled, QLineEdit:disabled, QTextEdit:disabled, QTableWidget:disabled {
                background: #0b0f16;
                border: 1px solid #202a38;
                color: #657080;
            }
            QToolTip {
                background: #172233;
                color: #e7edf7;
                border: 1px solid #40536c;
                padding: 6px;
            }
            QHeaderView::section { background: #1b2636; color: #dce7f7; padding: 6px; border: 0; }
            QTabBar::tab { background: #1b2636; padding: 8px 14px; border-top-left-radius: 6px; border-top-right-radius: 6px; }
            QTabBar::tab:selected { background: #31455f; }
            QTabBar::tab:disabled { background: #111722; color: #697386; }
            """
        )

    def _switch_mode(self, index: int) -> None:
        advanced = index == 1
        self.hex_mode_stack.setCurrentIndex(1 if advanced else 0)
        if not advanced and self.main_tabs.currentIndex() == self.advanced_tab_index:
            self.main_tabs.setCurrentIndex(self.disk_tab_index)
        self.main_tabs.setTabEnabled(self.advanced_tab_index, advanced)

    def _show_main_tab(self) -> None:
        self.main_tabs.setCurrentIndex(self.disk_tab_index)

    def _show_files_tab(self) -> None:
        self.main_tabs.setCurrentIndex(self.files_tab_index)

    def _show_hex_tab(self, *, advanced: Optional[bool] = None) -> None:
        use_advanced = self.mode.currentIndex() == 1 if advanced is None else advanced
        self.hex_mode_stack.setCurrentIndex(1 if use_advanced else 0)
        self.main_tabs.setCurrentIndex(self.hex_tab_index)

    def _show_advanced_tab(self) -> None:
        if self.main_tabs.isTabEnabled(self.advanced_tab_index):
            self.main_tabs.setCurrentIndex(self.advanced_tab_index)

    def _show_jobs_tab(self) -> None:
        self.main_tabs.setCurrentIndex(self.jobs_tab_index)

    def _selected_layout(self) -> str:
        return str(self.layout_combo.currentData() or (self.current_summary.layout_id if self.current_summary else ""))

    def _selected_encoding(self) -> str:
        if self.current_summary and self.current_summary.encoding:
            return self.current_summary.encoding
        return self.encoding_combo.currentText()

    def _selected_map_view(self) -> str:
        return str(self.map_view.currentData() or "logical")

    def _uses_cbm_logical_addressing(self) -> bool:
        if self.current_summary is None:
            return False
        filesystem = self.current_summary.filesystem or ""
        if filesystem in {"cbm_dos", "cbm_dos_1571"}:
            return True
        return (self.current_summary.layout_id or "") in {
            "commodore_gcr_1541_170k",
            "commodore_gcr_1571_341k",
        }

    def _display_to_internal_chs(self, track: int, head: int, sector: int) -> tuple[int, int, int]:
        if not self._uses_cbm_logical_addressing():
            return track, head, sector
        if track < 1:
            raise ValueError("CBM DOS track numbers start at 1")
        if track >= 36:
            return track - 36, 1, sector
        return track - 1, head, sector

    def _internal_to_display_chs(self, track: int, head: int, sector: int) -> tuple[int, int, int]:
        if not self._uses_cbm_logical_addressing():
            return track, head, sector
        if head == 1:
            return track + 36, head, sector
        return track + 1, head, sector

    def _current_map_uses_cbm_logical_addressing(self) -> bool:
        disk_map = self.map_widget.disk_map
        return bool(disk_map and getattr(disk_map, "address_style", "") == "cbm_logical")

    def _sector_hex_dump_for_display(self, layout: Optional[str], encoding: str, track: int, head: int, sector: int):
        internal_track, internal_head, internal_sector = self._display_to_internal_chs(track, head, sector)
        assert self.current_path is not None
        dump = sector_hex_dump(
            self.current_path,
            layout,
            encoding,
            internal_track,
            internal_head,
            internal_sector,
        )
        if not self._uses_cbm_logical_addressing():
            return dump
        title = f"Sector CBM T{track} H{head} S{sector}"
        return services.HexDumpView(
            title=title,
            size=dump.size,
            text=dump.text,
            data=dump.data,
            source_kind=dump.source_kind,
            track=dump.track,
            head=dump.head,
            sector=dump.sector,
            file_path=dump.file_path,
        )

    def _update_advanced_hex_edit_actions(self) -> None:
        enabled = (
            self.current_path is not None
            and self._advanced_hex_dump is not None
            and self._advanced_hex_dump.source_kind in {"sector", "file"}
        )
        if hasattr(self, "advanced_hex_save_button"):
            self.advanced_hex_save_button.setEnabled(enabled)
            self.advanced_hex_save_button.setToolTip(
                "Save edited Advanced hex bytes into a new image copy."
                if enabled
                else "Load a sector or file dump before saving edited hex."
            )
        if hasattr(self, "advanced_hex_revert_button"):
            self.advanced_hex_revert_button.setEnabled(enabled)
            self.advanced_hex_revert_button.setToolTip(
                "Restore the loaded hex dump text." if enabled else "Load a sector or file dump before reverting."
            )

    def _append_log(self, text: str) -> None:
        self.log.append(text)

    def _restore_settings(self) -> None:
        """Restore non-destructive UI preferences from the platform settings store."""

        geometry = self.settings.value("window/geometry")
        state = self.settings.value("window/state")
        if geometry:
            self.restoreGeometry(geometry)
        if state:
            self.restoreState(state)
        self._restore_combo(self.mode, "mode", 0)
        self._restore_combo(self.map_view, "map_view", 0)
        self._restore_combo(self.layout_combo, "layout", 0)
        self._restore_combo(self.encoding_combo, "encoding", 0)
        self._restore_combo(self.export_combo, "export", 0)
        self._restore_combo(self.dump_mode_combo, "dump_mode", 0)
        self._restore_combo(self.greaseweazle_drive_combo, "greaseweazle/drive", 0)
        self._restore_combo(self.greaseweazle_format_combo, "greaseweazle/format", 0)
        self.greaseweazle_tracks_input.setText(str(self.settings.value("greaseweazle/tracks", "")))
        self.greaseweazle_revs_input.setText(str(self.settings.value("greaseweazle/revs", "")))
        self.file_browser_path = str(self.settings.value("files/directory", "/")) or "/"
        self.advanced_file_browser_path = str(self.settings.value("advanced/directory", "/")) or "/"
        self._set_file_browser_path(self.file_browser_path)

    def _restore_combo(self, combo: QComboBox, key: str, default_index: int) -> None:
        value = self.settings.value(key)
        if value is None:
            combo.setCurrentIndex(default_index)
            return
        index = combo.findData(value)
        if index < 0:
            index = combo.findText(str(value))
        combo.setCurrentIndex(index if index >= 0 else default_index)

    def _save_settings(self) -> None:
        self.settings.setValue("window/geometry", self.saveGeometry())
        self.settings.setValue("window/state", self.saveState())
        for combo, key in (
            (self.mode, "mode"),
            (self.map_view, "map_view"),
            (self.layout_combo, "layout"),
            (self.encoding_combo, "encoding"),
            (self.export_combo, "export"),
            (self.dump_mode_combo, "dump_mode"),
            (self.greaseweazle_drive_combo, "greaseweazle/drive"),
            (self.greaseweazle_format_combo, "greaseweazle/format"),
        ):
            self.settings.setValue(key, combo.currentData() or combo.currentText())
        self.settings.setValue("greaseweazle/tracks", self.greaseweazle_tracks_input.text())
        self.settings.setValue("greaseweazle/revs", self.greaseweazle_revs_input.text())
        self.settings.setValue("files/directory", self.file_browser_path)
        self.settings.setValue("advanced/directory", self.advanced_file_browser_path)
        self.settings.sync()

    def closeEvent(self, event) -> None:  # type: ignore[override]
        self._save_settings()
        for job in tuple(self.active_jobs):
            job.cancel()
        super().closeEvent(event)

    def _update_job_elapsed(self) -> None:
        if self.current_job is None or self.current_job not in self.active_jobs:
            return
        elapsed = time.monotonic() - self._job_started_at
        label = self.job_status_label.text().split(" (")[0]
        self.job_status_label.setText(f"{label} ({elapsed:.1f}s)")

    def _set_job_finished_state(self) -> None:
        self._job_timer.stop()
        self.current_job = None
        self.job_cancel_button.setEnabled(False)
        self.job_progress.setVisible(False)
        self.job_status_label.setText("No active jobs")

    def cancel_current_job(self) -> None:
        job = self.current_job
        if job is None or job not in self.active_jobs:
            return
        job.cancel()
        self.job_cancel_button.setEnabled(False)
        self.activity_label.setText("Cancellation requested; finishing the current operation...")
        self.job_status_label.setText("Cancellation requested")
        self._append_log("Cancellation requested for the active job.")

    def _run_job(self, label: str, fn: Callable[[], object], done: Callable[[object], None]) -> None:
        self._job_generation += 1
        generation = self._job_generation
        self.summary_labels["status"].setText("running")
        self.activity_label.setText(f"Running {label}...")
        self._append_log(f"$ {label}")
        job = Job(fn)
        self.active_jobs.add(job)
        self.current_job = job
        self._job_started_at = time.monotonic()
        self.job_status_label.setText(f"Running {label}")
        self.job_progress.setRange(0, 0)
        self.job_progress.setVisible(True)
        self.job_cancel_button.setEnabled(True)
        self._job_timer.start()
        job.signals.progress.connect(lambda value, current_job=job: self._show_job_progress(current_job, value))
        job.signals.finished.connect(
            lambda result, current_job=job: self._finish_job(current_job, generation, label, result, done)
        )
        job.signals.failed.connect(lambda message, current_job=job: self._fail_job(current_job, generation, label, message))
        job.signals.cancelled.connect(lambda current_job=job: self._cancelled_job(current_job, generation, label))
        self.thread_pool.start(job)

    def _show_job_progress(self, job: Job, value: int) -> None:
        if job is not self.current_job:
            return
        self.job_progress.setRange(0, 100)
        self.job_progress.setValue(value)

    def _finish_job(self, job: Job, generation: int, label: str, result: object, done: Callable[[object], None]) -> None:
        self.active_jobs.discard(job)
        if job is self.current_job:
            self._set_job_finished_state()
        if generation != self._job_generation:
            self._append_log(f"Discarded stale result from {label}.")
            return
        self.activity_label.setText(f"Finished {label}.")
        if self.summary_labels["status"].text() == "running":
            self.summary_labels["status"].setText("ready")
        done(result)

    def _fail_job(self, job: Job, generation: int, label: str, message: str) -> None:
        self.active_jobs.discard(job)
        if job is self.current_job:
            self._set_job_finished_state()
        if generation != self._job_generation:
            self._append_log(f"Discarded stale error from {label}: {message}")
            return
        self.summary_labels["status"].setText("error")
        self.activity_label.setText(f"{label} failed: {message}")
        self._append_log(f"Error: {message}")

    def _cancelled_job(self, job: Job, generation: int, label: str) -> None:
        self.active_jobs.discard(job)
        if job is self.current_job:
            self._set_job_finished_state()
        if generation != self._job_generation:
            return
        self.summary_labels["status"].setText("ready")
        self.activity_label.setText(f"Cancelled {label}.")
        self._append_log(f"Cancelled {label}.")

    def open_image(self) -> None:
        filename, _ = QFileDialog.getOpenFileName(
            self,
            "Open disk image",
            "",
            "Disk images (*.scp *.woz *.po *.do *.nib *.img *.imd *.dsk *.dmk *.d64 *.d71 *.d81 *.adf);;All files (*)",
        )
        if filename:
            self.current_path = Path(filename)
            self.file_label.setText(str(self.current_path))
            self._clear_image_results()
            self._show_main_tab()
            self.run_probe()

    def _selected_blank_preset(self):
        preset_id = str(self.blank_image_combo.currentData() or "")
        return next((preset for preset in self.blank_image_presets if preset.preset_id == preset_id), None)

    def _update_blank_image_tooltip(self) -> None:
        preset = self._selected_blank_preset()
        tooltip = preset.description if preset else "Choose a supported blank disk image preset."
        self.blank_image_combo.setToolTip(tooltip)
        self.create_blank_button.setToolTip(tooltip)

    def create_blank_image_dialog(self) -> None:
        preset = self._selected_blank_preset()
        if preset is None:
            self._warn("Choose a blank disk image preset first.")
            return
        default_name = self._default_blank_image_output_path(preset.preset_id, preset.suffix)
        filter_text = f"{preset.label} (*{preset.suffix});;All files (*)"
        output_name, _ = QFileDialog.getSaveFileName(
            self,
            "Create blank disk image",
            str(default_name),
            filter_text,
        )
        if not output_name:
            return
        output = Path(output_name)
        if output.suffix == "":
            output = output.with_suffix(preset.suffix)
        if not output.is_absolute():
            output = self._blank_image_default_directory() / output
        overwrite = output.exists()
        if overwrite and not self._confirm_overwrite_output(output):
            return
        self._run_job(
            f"create blank {preset.label}",
            lambda: create_blank_image(preset.preset_id, output, overwrite=overwrite),
            self._show_blank_image_result,
        )

    def _confirm_overwrite_output(self, output: Path) -> bool:
        question = (
            "The selected output file already exists.\n\n"
            f"{output}\n\n"
            "Replace it with the new blank disk image?"
        )
        answer = QMessageBox.question(
            self,
            "Replace existing image?",
            question,
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        return answer == QMessageBox.Yes

    def _blank_image_default_directory(self) -> Path:
        documents = QStandardPaths.writableLocation(QStandardPaths.DocumentsLocation)
        if documents:
            return Path(documents)
        return Path.home()

    def _default_blank_image_output_path(self, preset_id: str, suffix: str) -> Path:
        return self._blank_image_default_directory() / f"blank-{preset_id}{suffix}"

    def _hardware_default_directory(self) -> Path:
        documents = QStandardPaths.writableLocation(QStandardPaths.DocumentsLocation)
        base = Path(documents) if documents else Path.home()
        return base / "Fluxctl Captures"

    def _update_hardware_controls(self) -> None:
        status = self.greaseweazle_status
        label = "Greaseweazle: available" if status.available else "Greaseweazle: missing"
        if status.available and status.executable:
            label += f"\n{status.executable}"
        self.greaseweazle_status_label.setText(label)
        tooltip = status.detail if status.available else f"{status.detail}\n{status.suggestion}".strip()
        for widget in [
            self.greaseweazle_status_label,
            self.greaseweazle_drive_combo,
            self.greaseweazle_image_combo,
            self.greaseweazle_format_combo,
            self.greaseweazle_tracks_input,
            self.greaseweazle_revs_input,
            self.greaseweazle_read_button,
        ]:
            widget.setToolTip(tooltip)
            if widget is not self.greaseweazle_status_label:
                widget.setEnabled(status.available)
        self.greaseweazle_write_button.setEnabled(False)
        self.greaseweazle_write_button.setToolTip(
            "Write-to-disk is not enabled yet. It needs destructive confirmation and read-back verification."
        )

    def _refresh_greaseweazle_format_combo(self) -> None:
        current_text = self.greaseweazle_format_combo.currentText().strip()
        current_data = self.greaseweazle_format_combo.currentData()
        current_format = str(current_data or current_text).strip()
        self.greaseweazle_format_combo.blockSignals(True)
        self.greaseweazle_format_combo.clear()
        self.greaseweazle_format_combo.addItem("Auto / no format", "")
        for gw_format in self.greaseweazle_formats:
            self.greaseweazle_format_combo.addItem(gw_format.label, gw_format.format_id)
        if current_format:
            index = self.greaseweazle_format_combo.findData(current_format)
            if index >= 0:
                self.greaseweazle_format_combo.setCurrentIndex(index)
            else:
                self.greaseweazle_format_combo.setEditText(current_format)
        self.greaseweazle_format_combo.blockSignals(False)

    def _selected_greaseweazle_revs(self) -> Optional[int]:
        text = self.greaseweazle_revs_input.text().strip()
        if not text:
            return None
        try:
            revs = int(text)
        except ValueError as exc:
            raise ValueError("Greaseweazle revolutions must be a whole number") from exc
        if revs < 1:
            raise ValueError("Greaseweazle revolutions must be 1 or greater")
        return revs

    def read_disk_with_greaseweazle_dialog(self) -> None:
        if not self.greaseweazle_status.available:
            self._warn(self.greaseweazle_status.detail)
            return
        default_name = self._hardware_default_directory() / "capture.scp"
        output_name, _ = QFileDialog.getSaveFileName(
            self,
            "Read disk to SCP",
            str(default_name),
            "SuperCard Pro flux (*.scp);;All files (*)",
        )
        if not output_name:
            return
        output = Path(output_name)
        if output.suffix == "":
            output = output.with_suffix(".scp")
        if not output.is_absolute():
            output = self._hardware_default_directory() / output
        overwrite = output.exists()
        if overwrite and not self._confirm_overwrite_output(output):
            return
        try:
            revs = self._selected_greaseweazle_revs()
        except ValueError as exc:
            self._warn(str(exc))
            return
        drive = str(self.greaseweazle_drive_combo.currentData() or "A")
        gw_format = self.greaseweazle_format_combo.currentText().strip()
        selected_data = self.greaseweazle_format_combo.currentData()
        selected_index = self.greaseweazle_format_combo.currentIndex()
        if (
            selected_data
            and selected_index >= 0
            and gw_format == self.greaseweazle_format_combo.itemText(selected_index)
        ):
            gw_format = str(selected_data).strip()
        elif gw_format == "Auto / no format":
            gw_format = ""
        tracks = self.greaseweazle_tracks_input.text().strip()
        self._run_job(
            f"greaseweazle read {drive}",
            lambda: read_disk_with_greaseweazle(
                output,
                drive=drive,
                gw_format=gw_format,
                tracks=tracks,
                revs=revs,
                overwrite=overwrite,
            ),
            self._show_greaseweazle_read_result,
        )

    def _show_greaseweazle_read_result(self, result: object) -> None:
        output = Path(result.path)
        self.current_path = output
        self.file_label.setText(str(output))
        self._clear_image_results()
        self.activity_label.setText(f"Read disk to {output}.")
        self._append_log(f"$ {result.command_display}")
        if result.stdout:
            self._append_log(result.stdout.strip())
        if result.stderr:
            self._append_log(result.stderr.strip())
        self._show_jobs_tab()
        self.run_probe()

    def _show_blank_image_result(self, result: object) -> None:
        self.current_path = Path(result.path)
        self.file_label.setText(str(self.current_path))
        self._clear_image_results()
        self.activity_label.setText(
            f"Created {result.label} ({result.size:,} bytes) at {result.path}."
        )
        self._append_log(
            f"Created blank image {result.path} using {result.preset_id} "
            f"({result.layout_id}, {result.filesystem}, {result.size:,} bytes)"
        )
        self._show_main_tab()
        self.run_probe()

    def _clear_image_results(self) -> None:
        self.current_summary = None
        self._set_file_browser_path("/")
        self.file_volume_label.setText("")
        self.files_table.setRowCount(0)
        self.hex_title_label.setText("No hex data loaded")
        self.hex_text.clear()
        self.map_widget.set_disk_map(None)
        self.summary_labels["layout"].setText("-")
        self.summary_labels["encoding"].setText("-")
        self.summary_labels["filesystem"].setText("-")
        self.summary_labels["confidence"].setText("-")
        self.summary_labels["size"].setText("-")
        self.activity_label.setText("Ready")
        self._update_filesystem_write_actions()
        self._update_advanced_context()

    def run_doctor(self) -> None:
        self._run_job("doctor", doctor_report, self._show_doctor)

    def _show_doctor(self, report: object) -> None:
        self.summary_labels["status"].setText(str(report.get("overall", "unknown")) if isinstance(report, dict) else "unknown")
        summary = self._doctor_summary_text(report) if isinstance(report, dict) else str(report)
        self.log.append(json.dumps(report, indent=2))
        self.greaseweazle_status = greaseweazle_status()
        self.greaseweazle_formats = greaseweazle_formats()
        self._refresh_greaseweazle_format_combo()
        self._update_hardware_controls()
        if self.current_path is None:
            self.advanced_detail_stack.setCurrentWidget(self.advanced_output)
            self.advanced_output.setPlainText(summary)

    def run_probe(self) -> None:
        if not self._require_image():
            return
        assert self.current_path is not None
        self._run_job("probe", lambda: summarize_image(self.current_path), self._show_summary)

    def _show_summary(self, summary: object) -> None:
        self.current_summary = summary
        self.summary_labels["layout"].setText(summary.layout_id or "unknown")
        self.summary_labels["encoding"].setText(summary.encoding or "unknown")
        self.summary_labels["filesystem"].setText(summary.filesystem or "unknown")
        self.summary_labels["confidence"].setText(f"{summary.confidence:.2f}")
        self.summary_labels["size"].setText(f"{summary.size:,} bytes")
        self.summary_labels["status"].setText("ready")
        diagnostic = next(
            (
                evidence
                for evidence in summary.evidence
                if evidence.startswith("cbm_dos_directory_chain_missing=")
            ),
            None,
        )
        activity = f"Probe found {summary.layout_id or 'unknown layout'} with {summary.confidence:.2f} confidence."
        if diagnostic:
            activity += f" CBM DOS directory chain is incomplete at {diagnostic.split('=', 1)[1]}."
        self.activity_label.setText(activity)
        self._append_log(json.dumps(summary.__dict__, indent=2))
        self._update_filesystem_write_actions()
        self._update_advanced_context()
        self.run_map()

    def _doctor_summary_text(self, report: dict) -> str:
        lines = [
            f"Fluxctl Doctor: {report.get('overall', 'unknown')}",
            f"Version: {report.get('version', 'unknown')}",
            "",
            "Checks:",
        ]
        for check in report.get("checks", []):
            status = str(check.get("status", "unknown")).upper()
            name = check.get("name", "check")
            detail = check.get("detail", "")
            lines.append(f"- {status}: {name} - {detail}")
            suggestion = check.get("suggestion")
            if suggestion:
                lines.append(f"  Suggestion: {suggestion}")
        return "\n".join(lines)

    def _advanced_fields(self) -> list[QWidget]:
        return [
            self.layout_combo,
            self.encoding_combo,
            self.export_combo,
            self.dump_mode_combo,
            self.track_input,
            self.head_input,
            self.sector_input,
            self.file_path_input,
            self.patch_payload_input,
        ]

    def _update_advanced_context(self) -> None:
        has_image = self.current_path is not None and self.current_summary is not None
        for field in self._advanced_fields():
            field.setEnabled(has_image)
        for button in self.advanced_image_buttons:
            button.setEnabled(has_image)
            button.setToolTip("" if has_image else "Open and probe a disk image before using this action.")
        self._update_advanced_hex_edit_actions()

        if not has_image:
            self.layout_combo.setCurrentIndex(-1)
            self.encoding_combo.setCurrentIndex(-1)
            self.export_combo.setCurrentIndex(-1)
            self.dump_mode_combo.setCurrentIndex(-1)
            self.track_input.clear()
            self.head_input.clear()
            self.sector_input.clear()
            self.advanced_file_browser_path = "/"
            self.file_path_input.clear()
            self.patch_payload_input.clear()
            self._advanced_hex_dump = None
            self.advanced_hex_title_label.setText("No hex data loaded")
            self.advanced_hex_text.clear()
            self._update_advanced_hex_edit_actions()
            return

        assert self.current_summary is not None
        self._select_combo_data(self.layout_combo, self.current_summary.layout_id)
        self._select_combo_text(self.encoding_combo, self.current_summary.encoding)
        self._select_combo_text(
            self.export_combo,
            self._default_exporter_for_image(
                self.current_summary.kind,
                self.current_summary.layout_id,
                self.current_summary.encoding,
            ),
        )
        self._select_combo_data(self.dump_mode_combo, "sector")
        self.track_input.setText("0")
        self.head_input.setText("0")
        self.sector_input.setText("1")
        self._load_advanced_file_path_options("/")
        self.patch_payload_input.clear()
        self._advanced_hex_dump = None
        self.advanced_hex_title_label.setText("No hex data loaded")
        self.advanced_hex_text.clear()
        self._update_advanced_hex_edit_actions()
        self.advanced_detail_stack.setCurrentWidget(self.advanced_output)
        self.advanced_output.setPlainText(self._image_summary_text(self.current_summary))

    def _advanced_file_path_text(self) -> str:
        return self.file_path_input.currentText().strip()

    def _advanced_file_path_is_selected_directory(self) -> bool:
        current_text = self._advanced_file_path_text()
        for index in range(self.file_path_input.count()):
            data = self.file_path_input.itemData(index)
            if not isinstance(data, dict):
                continue
            path = str(data.get("path") or "")
            if path == current_text:
                return bool(data.get("is_dir"))
        return False

    def _load_advanced_file_path_options(self, directory: str, selected_path: str = "") -> None:
        self._loading_advanced_file_paths = True
        try:
            self.advanced_file_browser_path = self._normalise_filesystem_path(directory)
            self.file_path_input.clear()
            self.file_path_input.addItem(f"Current directory: {self.advanced_file_browser_path}", {"path": self.advanced_file_browser_path, "is_dir": True})
            self.file_path_input.addItem("Root /", {"path": "/", "is_dir": True})
            if self.advanced_file_browser_path != "/":
                self.file_path_input.addItem("Up ..", {"path": self._filesystem_parent_path(self.advanced_file_browser_path), "is_dir": True})
            if self.current_path is not None:
                layout = self._selected_layout() or None
                encoding = self._selected_encoding()
                entries = list_files(self.current_path, layout, encoding, self.advanced_file_browser_path)
                for entry in sorted(entries, key=lambda item: (not item.is_dir, item.name.upper())):
                    label = f"{entry.name}/" if entry.is_dir else entry.name
                    self.file_path_input.addItem(label, {"path": entry.path, "is_dir": entry.is_dir})
            self.file_path_input.setEditText(selected_path or self.advanced_file_browser_path)
            self.file_path_input.setToolTip(
                "Type an image filesystem path, choose a file, or choose a directory to browse into it."
            )
        finally:
            self._loading_advanced_file_paths = False

    def _advanced_file_path_activated(self, index: int) -> None:
        if self._loading_advanced_file_paths:
            return
        data = self.file_path_input.itemData(index)
        if not isinstance(data, dict):
            return
        path = str(data.get("path") or "")
        if not path:
            return
        if bool(data.get("is_dir")):
            self._load_advanced_file_path_options(path, path)
            return
        self.file_path_input.setEditText(path)

    def _normalise_filesystem_path(self, path: str) -> str:
        parts = [part for part in path.strip().strip("/").split("/") if part]
        return "/" + "/".join(parts) if parts else "/"

    def _filesystem_parent_path(self, path: str) -> str:
        parts = [part for part in path.strip("/").split("/") if part]
        if len(parts) <= 1:
            return "/"
        return "/" + "/".join(parts[:-1])

    def _select_combo_data(self, combo: QComboBox, value: str) -> None:
        index = combo.findData(value)
        combo.setCurrentIndex(index if index >= 0 else 0)

    def _select_combo_text(self, combo: QComboBox, value: str) -> None:
        index = combo.findText(value)
        combo.setCurrentIndex(index if index >= 0 else 0)

    def _default_exporter_for_image(self, kind: str, layout_id: str = "", encoding: str = "") -> str:
        if layout_id.startswith("apple2_") or kind in {"woz", "po", "do", "nib"}:
            return "do" if kind == "do" else "po"
        if kind in {"img", "raw"}:
            return "raw"
        if kind in {"imd", "adf", "d64", "d71", "g64"}:
            return kind
        if kind == "d81":
            return "raw"
        if kind == "scp":
            if layout_id == "commodore_mfm_1581_800k":
                return "d81"
            if layout_id == "commodore_gcr_1571_341k":
                return "d71"
            if layout_id == "commodore_gcr_1541_170k":
                return "d64"
            if encoding == "gcr":
                return "g64"
            if layout_id == "amiga_mfm_880k":
                return "adf"
            return "raw"
        return "raw"

    def _exporter_choices_for_image(self, kind: str, layout_id: str = "", encoding: str = "") -> list[tuple[str, str]]:
        if layout_id.startswith("apple2_") or kind in {"woz", "po", "do", "nib"}:
            return [
                ("po", "Apple ProDOS-order sector image (.po)"),
                ("do", "Apple DOS-order sector image (.do)"),
            ]
        choices = [
            ("raw", "Raw sector image (.img)"),
            ("imd", "ImageDisk (.imd)"),
        ]
        if layout_id == "amiga_mfm_880k" or kind == "adf":
            choices.append(("adf", "Amiga Disk File (.adf)"))
        if layout_id.startswith("commodore_gcr_1541") or kind == "d64":
            choices.append(("d64", "Commodore 1541 image (.d64)"))
        if layout_id == "commodore_gcr_1571_341k" or kind == "d71":
            choices.append(("d71", "Commodore 1571 image (.d71)"))
        if layout_id == "commodore_mfm_1581_800k" or kind == "d81":
            choices.append(("d81", "Commodore 1581 image (.d81)"))
        if encoding == "gcr" or kind == "g64":
            choices.append(("g64", "Commodore GCR track image (.g64)"))
        if kind in {"adf", "d64", "d71", "d81", "g64"} and all(choice[0] != kind for choice in choices):
            choices.append((kind, f"Same container (.{kind})"))
        return choices

    def _default_roundtrip_back_exporter_for_image(self, kind: str) -> str:
        if kind in {"woz", "po", "do", "nib"}:
            return "do" if kind == "do" else "po"
        if kind in {"img", "raw", "scp", "imd"}:
            return "raw"
        if kind in {"adf", "d64", "d71", "g64"}:
            return kind
        if kind == "d81":
            return "raw"
        return "raw"

    def _image_summary_text(self, summary: object) -> str:
        evidence = "\n".join(f"- {item}" for item in summary.evidence[:12])
        if len(summary.evidence) > 12:
            evidence += f"\n- ... {len(summary.evidence) - 12} more evidence item(s)"
        return "\n".join(
            [
                "Loaded Image",
                f"Path: {summary.path}",
                f"Size: {summary.size:,} bytes",
                f"Kind: {summary.kind or 'image'}",
                f"Layout: {summary.layout_id or 'unknown'}",
                f"Encoding: {summary.encoding or 'unknown'}",
                f"Filesystem: {summary.filesystem or 'unknown'}",
                f"Confidence: {summary.confidence:.2f}",
                "",
                "Evidence:",
                evidence or "- none",
            ]
        )

    def _filesystem_write_buttons(self) -> list[QPushButton]:
        return [
            self.file_replace_button,
            self.file_delete_button,
            self.file_import_button,
            self.directory_import_button,
            self.directory_create_button,
        ]

    def _filesystem_write_action_support(self) -> dict[QPushButton, tuple[bool, str]]:
        closed_reason = "Open and probe a disk image before using write actions."
        default = {button: (False, closed_reason) for button in self._filesystem_write_buttons()}
        if self.current_path is None or self.current_summary is None:
            return default
        suffix = self.current_path.suffix.lower()
        filesystem = self.current_summary.filesystem or "unknown"
        if suffix == ".img" and filesystem == "fat12":
            reason = "Available for FAT12 flat .img images. Operations write a new image copy."
            return {button: (True, reason) for button in self._filesystem_write_buttons()}
        if suffix == ".img" and filesystem == "cpm":
            reason = (
                "Available for modelled CP/M flat .img images. File import and delete write a new image copy. "
                "Replace and directory actions are not implemented yet."
            )
            return {
                self.file_replace_button: (False, reason),
                self.file_delete_button: (True, reason),
                self.file_import_button: (True, reason),
                self.directory_import_button: (False, reason),
                self.directory_create_button: (False, reason),
            }
        if suffix in {".d64", ".d71"} and filesystem in {"cbm_dos", "cbm_dos_1571"}:
            unsupported = (
                "This CBM DOS image supports root-level file import, replace, and delete in a new image copy. "
                "Directory import and directory creation are not implemented yet."
            )
            return {
                self.file_replace_button: (True, unsupported),
                self.file_delete_button: (True, unsupported),
                self.file_import_button: (
                    True,
                    "Available for CBM DOS .d64/.d71 root-level PRG import by default. .SEQ/.USR suffixes set the corresponding type; REL import requires side-sector support. The operation writes a new image copy.",
                ),
                self.directory_import_button: (False, unsupported),
                self.directory_create_button: (False, unsupported),
            }
        if suffix == ".d81" and filesystem == "cbm_dos_1581":
            reason = "Available for CBM DOS 1581 .d81 images. Operations write a new image copy."
            return {
                self.file_replace_button: (True, reason),
                self.file_delete_button: (True, reason),
                self.file_import_button: (
                    True,
                    "Available for CBM DOS 1581 .d81 PRG import by default. .SEQ/.USR suffixes set the corresponding type; REL import requires side-sector support. The operation writes a new image copy.",
                ),
                self.directory_import_button: (True, reason),
                self.directory_create_button: (True, reason),
            }
        if suffix not in {".img", ".d64", ".d71", ".d81"}:
            reason = "Write actions currently support FAT12 .img, modelled CP/M .img, and CBM DOS .d64/.d71/.d81 images only."
        else:
            filesystem = self.current_summary.filesystem or "unknown"
            reason = f"Write actions are not available for filesystem {filesystem} in this container yet."
        return {button: (False, reason) for button in self._filesystem_write_buttons()}

    def _update_filesystem_write_actions(self) -> None:
        support = self._filesystem_write_action_support()
        for button, (supported, reason) in support.items():
            button.setEnabled(supported)
            button.setToolTip(reason)

    def run_qc(self) -> None:
        if not self._require_image():
            return
        assert self.current_path is not None
        layout = self._selected_layout() or None
        encoding = self._selected_encoding()
        self._run_job("qc", lambda: build_qc_for_image(self.current_path, layout, encoding), self._show_qc)

    def _show_qc(self, report: object) -> None:
        self.summary_labels["status"].setText(report.status)
        self.summary_labels["confidence"].setText(f"{report.overall_confidence:.2f}")
        details = [
            f"{report.total_good_sectors}/{report.total_sectors} good",
            f"{report.total_weak_sectors} weak",
            f"{report.total_missing_sectors} missing",
            f"{report.total_bad_sectors} bad",
        ]
        if report.missing_tracks:
            details.append(f"{report.missing_tracks} missing track/head rows")
        self.activity_label.setText(f"QC {report.status}: " + ", ".join(details) + ".")
        self._append_log(report.to_json())

    def run_map(self) -> None:
        if not self._require_image():
            return
        assert self.current_path is not None
        layout = self._selected_layout() or None
        encoding = self._selected_encoding()
        map_view = self._selected_map_view()
        self._run_job(
            "map",
            lambda: build_disk_map_for_image(self.current_path, layout, encoding, map_view),
            self._show_map,
        )

    def _show_map(self, disk_map: object) -> None:
        self.map_widget.set_disk_map(disk_map)
        head_count = len(DiskMapWidget.head_groups(disk_map))
        self.activity_label.setText(
            f"Rendered {self.map_view.currentText().lower()} with {disk_map.total_tracks} track/head rows across {head_count} head(s), "
            f"{disk_map.max_sectors_per_track} sectors per track."
        )
        self._append_log(f"Rendered {disk_map.total_tracks} tracks with {disk_map.max_sectors_per_track} sectors/track")

    def run_list_files(self) -> None:
        if not self._require_image():
            return
        assert self.current_path is not None
        layout = self._selected_layout() or None
        encoding = self._selected_encoding()
        directory = self.file_browser_path
        self._run_job(
            f"extract --list {directory}",
            lambda: list_files_with_info(self.current_path, layout, encoding, directory),
            self._show_files,
        )

    def _show_files(self, entries: object) -> None:
        self._show_files_tab()
        volume_text = ""
        if isinstance(entries, services.FileListView):
            volume_text = entries.volume_text
            entries = entries.entries
        self.file_volume_label.setText(volume_text)
        if not entries:
            self.files_table.setRowCount(1)
            self.files_table.setItem(0, 0, QTableWidgetItem(f"No cataloged filesystem entries found in {self.file_browser_path}"))
            self.files_table.setItem(0, 1, QTableWidgetItem("-"))
            self.files_table.setItem(0, 2, QTableWidgetItem("-"))
            self._clear_file_map_highlight()
            self.activity_label.setText(f"The detected filesystem has no cataloged entries in {self.file_browser_path}.")
            self._append_log(f"Listed 0 filesystem entries in {self.file_browser_path}")
            return
        self.files_table.setRowCount(len(entries))
        for row, entry in enumerate(entries):
            name_item = QTableWidgetItem(entry.name)
            name_item.setData(Qt.UserRole, entry.path)
            name_item.setData(Qt.UserRole + 1, entry.is_dir)
            self.files_table.setItem(row, 0, name_item)
            self.files_table.setItem(row, 1, QTableWidgetItem(entry.file_type or entry.kind))
            self.files_table.setItem(row, 2, QTableWidgetItem(str(entry.size)))
        self.activity_label.setText(f"Listed {len(entries)} filesystem entries in {self.file_browser_path}.")
        self._append_log(f"Listed {len(entries)} filesystem entries in {self.file_browser_path}")
        self._clear_file_map_highlight()

    def _set_file_browser_path(self, path: str) -> None:
        parts = [part for part in path.strip("/").split("/") if part]
        self.file_browser_path = "/" + "/".join(parts) if parts else "/"
        self.file_path_label.setText(self.file_browser_path)
        self.file_up_button.setEnabled(self.file_browser_path != "/")

    def open_root_directory(self) -> None:
        self._set_file_browser_path("/")
        self.run_list_files()

    def open_parent_directory(self) -> None:
        if self.file_browser_path == "/":
            return
        parent_parts = self.file_browser_path.strip("/").split("/")[:-1]
        self._set_file_browser_path("/" + "/".join(parent_parts) if parent_parts else "/")
        self.run_list_files()

    def open_selected_file_entry(self, item: QTableWidgetItem) -> None:
        name_item = self.files_table.item(item.row(), 0)
        if name_item is None:
            return
        if not bool(name_item.data(Qt.UserRole + 1)):
            return
        entry_path = str(name_item.data(Qt.UserRole) or "")
        if not entry_path:
            return
        self._set_file_browser_path(entry_path)
        self.run_list_files()

    def _selected_file_entry_path(self) -> tuple[str, bool]:
        entries = self._selected_file_entries()
        if not entries:
            return "", False
        return entries[-1]

    def _selected_file_entries(self) -> list[tuple[str, bool]]:
        rows = [index.row() for index in self.files_table.selectionModel().selectedRows()]
        if not rows:
            row = self.files_table.currentRow()
            rows = [row] if row >= 0 else []
        selected: list[tuple[str, bool]] = []
        for row in sorted(set(rows)):
            name_item = self.files_table.item(row, 0)
            if name_item is None:
                continue
            entry_path = str(name_item.data(Qt.UserRole) or "")
            if not entry_path:
                continue
            selected.append((entry_path, bool(name_item.data(Qt.UserRole + 1))))
        return selected

    def _clear_file_map_highlight(self) -> None:
        disk_map = self.map_widget.disk_map
        if disk_map is not None and getattr(disk_map, "highlighted_sectors", None):
            disk_map.highlighted_sectors.clear()
            self.map_widget.update()

    def highlight_selected_file_on_map(self) -> None:
        disk_map = self.map_widget.disk_map
        if disk_map is None:
            return
        file_path, is_dir = self._selected_file_entry_path()
        if not file_path or is_dir or self.current_path is None or self.current_summary is None:
            self._clear_file_map_highlight()
            return
        try:
            allocation = file_allocation_for_image(
                self.current_path,
                self._selected_layout() or None,
                self._selected_encoding(),
                file_path,
            )
        except Exception:
            self._clear_file_map_highlight()
            return
        if getattr(disk_map, "address_style", "physical") == "cbm_logical" and allocation.logical_sectors is not None:
            disk_map.highlighted_sectors = set(allocation.logical_sectors)
        else:
            disk_map.highlighted_sectors = set(allocation.sectors)
        self.map_widget.update()

    def view_selected_file_hex(self) -> None:
        if not self._require_image():
            return
        file_path, is_dir = self._selected_file_entry_path()
        if not file_path:
            self._warn("Select a file entry before viewing hex.")
            return
        if is_dir:
            self._warn("Select a file, not a directory, before viewing file hex.")
            return
        assert self.current_path is not None
        layout = self._selected_layout() or None
        encoding = self._selected_encoding()
        self._run_job(
            f"hex file {file_path}",
            lambda: file_hex_dump(self.current_path, layout, encoding, file_path, max_bytes=65536),
            self._show_hex_dump,
        )

    def export_selected_file_entry(self) -> None:
        if not self._require_image():
            return
        selected_entries = self._selected_file_entries()
        if not selected_entries:
            self._warn("Select a file or directory entry before exporting.")
            return
        assert self.current_path is not None
        if len(selected_entries) == 1 and not selected_entries[0][1]:
            file_path = selected_entries[0][0]
            filename, _ = QFileDialog.getSaveFileName(self, "Export selected file", Path(file_path).name, "All files (*)")
            if not filename:
                return
            destination = Path(filename)
            layout = self._selected_layout() or None
            encoding = self._selected_encoding()
            self._run_job(
                f"export {file_path}",
                lambda: export_filesystem_entry(self.current_path, layout, encoding, file_path, destination),
                self._show_export_result,
            )
            return
        selected_paths = [entry_path for entry_path, _is_dir in selected_entries]
        if len(selected_entries) == 1:
            destination_name = QFileDialog.getExistingDirectory(self, "Choose export destination folder", "")
        else:
            destination_name = QFileDialog.getExistingDirectory(self, "Choose folder for selected exports", "")
        if not destination_name:
            return
        destination = Path(destination_name)
        selected_rows = [index.row() for index in self.files_table.selectionModel().selectedRows()]
        if not selected_rows:
            selected_rows = [self.files_table.currentRow()]
        selected_names = [
            self.files_table.item(row, 0).text()
            for row in sorted(set(selected_rows))
            if row >= 0 and self.files_table.item(row, 0) is not None
        ]
        export_names = [safe_export_name(name) for name in selected_names]
        conflicts = [
            destination / name
            for name in export_names
            if (destination / name).exists() or (destination / name).is_symlink()
        ]
        overwrite = False
        if conflicts:
            names = "\n".join(f"- {conflict.name}" for conflict in conflicts[:8])
            if len(conflicts) > 8:
                names += f"\n- ... and {len(conflicts) - 8} more"
            answer = QMessageBox.question(
                self,
                "Overwrite exported items?",
                "The following export targets already exist:\n\n"
                f"{names}\n\nOverwrite them?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if answer != QMessageBox.Yes:
                return
            overwrite = True
        layout = self._selected_layout() or None
        encoding = self._selected_encoding()
        self._run_job(
            f"export {len(selected_paths)} selected item(s)",
            lambda: export_filesystem_entries(
                self.current_path,
                layout,
                encoding,
                selected_paths,
                destination,
                overwrite,
            ),
            self._show_export_result,
        )

    def _show_export_result(self, result: object) -> None:
        self.activity_label.setText(f"Exported {result.files} file(s), {result.bytes:,} bytes to {result.path}.")
        self._append_log(f"Exported {result.files} file(s), {result.bytes:,} bytes to {result.path}")

    def replace_selected_file_with_copy(self) -> None:
        if not self._require_image():
            return
        selected_entries = self._selected_file_entries()
        if len(selected_entries) != 1:
            self._warn("Select exactly one file entry before replacing.")
            return
        file_path, is_dir = selected_entries[0]
        if is_dir:
            self._warn("Select a file, not a directory, before replacing.")
            return
        assert self.current_path is not None
        replacement_name, _ = QFileDialog.getOpenFileName(
            self,
            "Choose replacement file",
            "",
            "All files (*)",
        )
        if not replacement_name:
            return
        replacement = Path(replacement_name)
        default_output = self._default_replacement_output_path(self.current_path)
        output_name, _ = QFileDialog.getSaveFileName(
            self,
            "Save modified image copy",
            str(default_output),
            f"Disk images (*{self.current_path.suffix});;All files (*)",
        )
        if not output_name:
            return
        output = Path(output_name)
        question = (
            "Fluxctl will create a new image copy and replace the selected file's contents in that copy only.\n\n"
            f"Original image:\n{self.current_path}\n\n"
            f"Filesystem file whose contents will be replaced:\n{file_path}\n\n"
            f"Host file to read replacement contents from:\n{replacement}\n\n"
            f"New image copy:\n{output}\n\n"
            "The filesystem file name and path will not be changed. The original image will not be modified. Continue?"
        )
        answer = QMessageBox.question(self, "Replace file in image copy", question, QMessageBox.Yes | QMessageBox.No)
        if answer != QMessageBox.Yes:
            return
        layout = self._selected_layout() or None
        encoding = self._selected_encoding()
        self._run_job(
            f"replace {file_path} with copy",
            lambda: replace_file_with_copy(
                self.current_path,
                layout,
                encoding,
                file_path,
                replacement,
                output,
            ),
            self._show_replace_result,
        )

    def _default_replacement_output_path(self, path: Path) -> Path:
        candidate = path.with_name(f"{path.stem}-modified{path.suffix}")
        counter = 2
        while candidate.exists():
            candidate = path.with_name(f"{path.stem}-modified-{counter}{path.suffix}")
            counter += 1
        return candidate

    def _show_replace_result(self, result: object) -> None:
        self.activity_label.setText(
            f"Replaced {result.file_path} with {result.bytes:,} bytes in new {result.filesystem} image copy: {result.path}."
        )
        self._append_log(
            f"Replaced {result.file_path} with {result.bytes:,} bytes in new {result.filesystem} image copy: {result.path}"
        )

    def delete_selected_entry_with_copy(self) -> None:
        if not self._require_image():
            return
        selected_entries = self._selected_file_entries()
        if len(selected_entries) != 1:
            self._warn("Select exactly one file or empty directory before deleting.")
            return
        entry_path, is_dir = selected_entries[0]
        assert self.current_path is not None
        output = self._choose_mutation_output("Save image copy after delete")
        if output is None:
            return
        entry_kind = "empty directory" if is_dir else "file"
        question = (
            "Fluxctl will create a new image copy and delete the selected filesystem "
            f"{entry_kind} in that copy only.\n\n"
            f"Original image:\n{self.current_path}\n\n"
            f"Filesystem entry to delete:\n{entry_path}\n\n"
            f"New image copy:\n{output}\n\n"
            "Directory delete currently requires an empty directory. The original image will not be modified. Continue?"
        )
        if not self._confirm_mutation("Delete entry in image copy", question):
            return
        layout = self._selected_layout() or None
        encoding = self._selected_encoding()
        self._run_job(
            f"delete {entry_path} with copy",
            lambda: delete_filesystem_entry_with_copy(
                self.current_path,
                layout,
                encoding,
                entry_path,
                output,
            ),
            self._show_mutation_result,
        )

    def import_file_into_copy(self) -> None:
        if not self._require_image():
            return
        assert self.current_path is not None
        host_name, _ = QFileDialog.getOpenFileName(self, "Choose host file to import", "", "All files (*)")
        if not host_name:
            return
        host_file = Path(host_name)
        output = self._choose_mutation_output("Save image copy after file import")
        if output is None:
            return
        question = (
            "Fluxctl will create a new image copy and import the host file into the current filesystem directory.\n\n"
            f"Original image:\n{self.current_path}\n\n"
            f"Current filesystem directory:\n{self.file_browser_path}\n\n"
            f"Host file to import:\n{host_file}\n\n"
            f"New image copy:\n{output}\n\n"
            "FAT12 import requires an 8.3-compatible file name. CBM DOS import infers PRG, SEQ, or USR from the suffix and defaults to PRG; REL requires side-sector support. "
            "using names up to 16 ASCII characters. Existing entries are not overwritten. The original image "
            "will not be modified. Continue?"
        )
        if not self._confirm_mutation("Import file into image copy", question):
            return
        layout = self._selected_layout() or None
        encoding = self._selected_encoding()
        self._run_job(
            f"import file {host_file.name} with copy",
            lambda: import_file_with_copy(
                self.current_path,
                layout,
                encoding,
                self.file_browser_path,
                host_file,
                output,
            ),
            self._show_mutation_result,
        )

    def import_directory_into_copy(self) -> None:
        if not self._require_image():
            return
        assert self.current_path is not None
        host_name = QFileDialog.getExistingDirectory(self, "Choose host directory to import", "")
        if not host_name:
            return
        host_directory = Path(host_name)
        output = self._choose_mutation_output("Save image copy after directory import")
        if output is None:
            return
        question = (
            "Fluxctl will create a new image copy and recursively import the host directory into the current "
            "filesystem directory.\n\n"
            f"Original image:\n{self.current_path}\n\n"
            f"Current filesystem directory:\n{self.file_browser_path}\n\n"
            f"Host directory to import:\n{host_directory}\n\n"
            f"New image copy:\n{output}\n\n"
            "FAT12 import currently requires 8.3-compatible file and directory names. CBM DOS 1581 import "
            "supports ASCII names up to 16 characters. Existing entries are not overwritten. The original image "
            "will not be modified. Continue?"
        )
        if not self._confirm_mutation("Import directory into image copy", question):
            return
        layout = self._selected_layout() or None
        encoding = self._selected_encoding()
        self._run_job(
            f"import directory {host_directory.name} with copy",
            lambda: import_directory_with_copy(
                self.current_path,
                layout,
                encoding,
                self.file_browser_path,
                host_directory,
                output,
            ),
            self._show_mutation_result,
        )

    def create_directory_in_copy(self) -> None:
        if not self._require_image():
            return
        assert self.current_path is not None
        name, accepted = QInputDialog.getText(self, "Create directory", "Directory name")
        if not accepted or not name:
            return
        output = self._choose_mutation_output("Save image copy after directory creation")
        if output is None:
            return
        question = (
            "Fluxctl will create a new image copy and create one empty directory in the current filesystem directory.\n\n"
            f"Original image:\n{self.current_path}\n\n"
            f"Current filesystem directory:\n{self.file_browser_path}\n\n"
            f"New directory name:\n{name}\n\n"
            f"New image copy:\n{output}\n\n"
            "FAT12 directory creation currently requires an 8.3-compatible name. CBM DOS 1581 directory creation "
            "supports ASCII names up to 16 characters. Existing entries are not overwritten. The original image "
            "will not be modified. Continue?"
        )
        if not self._confirm_mutation("Create directory in image copy", question):
            return
        layout = self._selected_layout() or None
        encoding = self._selected_encoding()
        self._run_job(
            f"mkdir {name} with copy",
            lambda: create_directory_with_copy(
                self.current_path,
                layout,
                encoding,
                self.file_browser_path,
                name,
                output,
            ),
            self._show_mutation_result,
        )

    def _choose_mutation_output(self, title: str) -> Optional[Path]:
        assert self.current_path is not None
        default_output = self._default_replacement_output_path(self.current_path)
        suffix = self.current_path.suffix.lower()
        if suffix in {".d64", ".d71", ".d81"}:
            filter_text = f"Disk images (*{suffix});;All files (*)"
        else:
            filter_text = "Disk images (*.img);;All files (*)"
        output_name, _ = QFileDialog.getSaveFileName(
            self,
            title,
            str(default_output),
            filter_text,
        )
        return Path(output_name) if output_name else None

    def _confirm_mutation(self, title: str, question: str) -> bool:
        answer = QMessageBox.question(self, title, question, QMessageBox.Yes | QMessageBox.No)
        return answer == QMessageBox.Yes

    def _show_mutation_result(self, result: object) -> None:
        self.activity_label.setText(
            f"{result.operation} wrote {result.entries} entries, {result.bytes:,} bytes to new {result.filesystem} image copy: {result.path}."
        )
        self._append_log(
            f"{result.operation} wrote {result.entries} entries, {result.bytes:,} bytes to new {result.filesystem} image copy: {result.path}"
        )

    def view_sector_hex(self) -> None:
        if not self._require_image():
            return
        assert self.current_path is not None
        layout = self._selected_layout() or None
        encoding = self._selected_encoding()
        try:
            track = self.hex_track_input.value()
            head = self.hex_head_input.value()
            sector = self.hex_sector_input.value()
        except ValueError:
            self._warn("Track, head, and sector must be integer values.")
            return
        self._run_job(
            f"hex sector {track}:{head}:{sector}",
            lambda: self._sector_hex_dump_for_display(layout, encoding, track, head, sector),
            self._show_hex_dump,
        )

    def _auto_view_sector_hex(self, _value: int) -> None:
        """Refresh the sector view immediately after manual CHS stepping."""

        if self.current_path is not None:
            self.view_sector_hex()

    def load_sector_hex_from_map(self, track: int, head: int, sector: int) -> None:
        if self._current_map_uses_cbm_logical_addressing():
            display_track, display_head, display_sector = track, head, sector
        else:
            display_track, display_head, display_sector = self._internal_to_display_chs(track, head, sector)
        for input_widget in (
            self.hex_track_input,
            self.hex_head_input,
            self.hex_sector_input,
        ):
            input_widget.blockSignals(True)
        try:
            self.hex_track_input.setValue(display_track)
            self.hex_head_input.setValue(display_head)
            self.hex_sector_input.setValue(display_sector)
        finally:
            for input_widget in (
                self.hex_track_input,
                self.hex_head_input,
                self.hex_sector_input,
            ):
                input_widget.blockSignals(False)
        self.view_sector_hex()

    def _show_hex_dump(self, dump: object) -> None:
        if self.mode.currentIndex() == 1:
            self._show_advanced_hex_dump(dump)
            return
        self.hex_title_label.setText(f"{dump.title}  ({dump.size:,} bytes)")
        self.hex_text.setPlainText(dump.text)
        self._show_hex_tab(advanced=False)
        self.activity_label.setText(f"Loaded hex view for {dump.title}.")
        self._append_log(f"Loaded hex view for {dump.title} ({dump.size:,} bytes)")

    def run_info(self) -> None:
        if not self._require_image():
            return
        assert self.current_path is not None
        self._run_job("info", lambda: summarize_image(self.current_path), self._show_summary)

    def run_sectors(self) -> None:
        if not self._require_image():
            return
        assert self.current_path is not None
        try:
            track = int(self.track_input.text())
            head = int(self.head_input.text())
        except ValueError:
            self._warn("Track and head must be integer values.")
            return
        layout = self._selected_layout() or None
        encoding = self._selected_encoding()
        try:
            internal_track, internal_head, _sector = self._display_to_internal_chs(track, head, 0)
        except ValueError as exc:
            self._warn(str(exc))
            return
        self._run_job(
            f"sectors T{track} H{head}",
            lambda: sector_list(self.current_path, layout, encoding, internal_track, internal_head),
            self._show_text_view,
        )

    def run_dump(self) -> None:
        if not self._require_image():
            return
        mode = str(self.dump_mode_combo.currentData() or "sector")
        if mode == "file":
            self.run_file_dump()
            return
        layout = self._selected_layout()
        if not layout:
            self._warn("Choose a layout before dumping a sector.")
            return
        assert self.current_path is not None
        try:
            track = int(self.track_input.text())
            head = int(self.head_input.text())
            sector = int(self.sector_input.text())
        except ValueError:
            self._warn("Track, head, and sector must be integer values.")
            return
        encoding = self._selected_encoding()
        self._run_job(
            f"dump T{track} H{head} S{sector}",
            lambda: self._sector_hex_dump_for_display(layout, encoding, track, head, sector),
            self._show_advanced_hex_dump,
        )

    def run_file_dump(self) -> None:
        assert self.current_path is not None
        file_path = self._advanced_file_path_text()
        if not file_path or file_path == "/":
            self._warn("Choose a file path before dumping file contents.")
            return
        if self._advanced_file_path_is_selected_directory():
            self._warn("File Dump requires a file. Choose a file, not a directory.")
            return
        layout = self._selected_layout() or None
        encoding = self._selected_encoding()
        self._run_job(
            f"dump file {file_path}",
            lambda: file_hex_dump(self.current_path, layout, encoding, file_path),
            self._show_advanced_hex_dump,
        )

    def revert_advanced_hex_edit(self) -> None:
        if self._advanced_hex_dump is None:
            self._warn("Load a sector or file dump before reverting edited hex.")
            return
        self.advanced_hex_text.setPlainText(self._advanced_hex_dump.text)
        self.activity_label.setText(f"Reverted edited hex view for {self._advanced_hex_dump.title}.")

    def synchronize_advanced_hex_columns(self) -> None:
        """Rebuild the paired hex/ASCII column after an Advanced editor edit."""

        dump = self._advanced_hex_dump
        if dump is None:
            self._warn("Load a sector or file dump before editing hex data.")
            return
        cursor = self.advanced_hex_text.textCursor()
        line = cursor.block().text()
        ascii_marker = line.find("|")
        edit_ascii = ascii_marker >= 0 and cursor.positionInBlock() > ascii_marker
        try:
            original_data = dump.data
            if len(original_data) != dump.size:
                original_data = services.parse_hex_dump_text(dump.text, expected_size=dump.size)
            if edit_ascii:
                edited = services.apply_ascii_hex_dump_edits(
                    self.advanced_hex_text.toPlainText(), original_data
                )
            else:
                edited = services.parse_hex_dump_text(
                    self.advanced_hex_text.toPlainText(), expected_size=dump.size
                )
        except ValueError as exc:
            self._warn(str(exc))
            return
        cursor_position = cursor.position()
        self.advanced_hex_text.setPlainText(services.format_hex_dump(edited))
        refreshed_cursor = self.advanced_hex_text.textCursor()
        refreshed_cursor.setPosition(min(cursor_position, len(self.advanced_hex_text.toPlainText())))
        self.advanced_hex_text.setTextCursor(refreshed_cursor)
        self.activity_label.setText("Synchronized Advanced hex and ASCII columns.")

    def save_advanced_hex_edit(self) -> None:
        if not self._require_image():
            return
        dump = self._advanced_hex_dump
        if dump is None:
            self._warn("Load a sector or file dump before saving edited hex.")
            return
        try:
            edited = services.parse_hex_dump_text(self.advanced_hex_text.toPlainText(), expected_size=dump.size)
        except ValueError as exc:
            self._warn(str(exc))
            return
        if edited == dump.data:
            self._warn("No byte changes were found in the Advanced hex editor.")
            return
        assert self.current_path is not None
        default_output = self._default_hex_edit_output_path(self.current_path)
        output_name, _ = QFileDialog.getSaveFileName(
            self,
            "Save edited image copy",
            str(default_output),
            f"Disk images (*{self.current_path.suffix});;All files (*)",
        )
        if not output_name:
            return
        output = Path(output_name)
        target = dump.file_path if dump.source_kind == "file" else f"T{dump.track} H{dump.head} S{dump.sector}"
        question = (
            "Fluxctl will create a new image copy with the edited Advanced hex bytes.\n\n"
            f"Original image:\n{self.current_path}\n\n"
            f"Edited target:\n{target}\n\n"
            f"New image copy:\n{output}\n\n"
            "The original image will not be modified. Continue?"
        )
        answer = QMessageBox.question(self, "Save edited hex to image copy", question, QMessageBox.Yes | QMessageBox.No)
        if answer != QMessageBox.Yes:
            return
        layout = self._selected_layout() or None
        encoding = self._selected_encoding()
        if dump.source_kind == "file":
            if not dump.file_path:
                self._warn("The loaded file dump does not include a filesystem path.")
                return
            self._run_job(
                f"save edited file hex {dump.file_path}",
                lambda: replace_file_bytes_with_copy(
                    self.current_path,
                    layout,
                    encoding,
                    dump.file_path,
                    edited,
                    output,
                ),
                self._show_advanced_hex_edit_result,
            )
            return
        if dump.source_kind == "sector":
            if not layout:
                self._warn("Choose a layout before saving edited sector hex.")
                return
            if dump.track is None or dump.head is None or dump.sector is None:
                self._warn("The loaded sector dump does not include a complete sector address.")
                return
            self._run_job(
                f"save edited sector hex T{dump.track} H{dump.head} S{dump.sector}",
                lambda: replace_flat_sector_bytes_with_copy(
                    self.current_path,
                    layout,
                    dump.track,
                    dump.head,
                    dump.sector,
                    edited,
                    output,
                ),
                self._show_advanced_hex_edit_result,
            )
            return
        self._warn("The loaded hex dump cannot be saved because its source type is unknown.")

    def _default_hex_edit_output_path(self, path: Path) -> Path:
        candidate = path.with_name(f"{path.stem}-hexedit{path.suffix}")
        counter = 2
        while candidate.exists():
            candidate = path.with_name(f"{path.stem}-hexedit-{counter}{path.suffix}")
            counter += 1
        return candidate

    def qc_export_dialog(self) -> None:
        if not self._require_image():
            return
        filename, _ = QFileDialog.getSaveFileName(self, "Save QC JSON", "qc.json", "JSON (*.json);;All files (*)")
        if not filename:
            return
        layout = self._selected_layout()
        output = Path(filename)
        self._run_job(
            "export QC JSON",
            lambda: export_qc_json(
                self.current_path,
                output,
                layout or None,
                self._selected_encoding(),
            ),
            lambda result: self._show_report_export_result("QC JSON", result),
        )

    def svg_export_dialog(self) -> None:
        if not self._require_image():
            return
        filename, _ = QFileDialog.getSaveFileName(self, "Save SVG disk map", "map.svg", "SVG (*.svg);;All files (*)")
        if not filename:
            return
        layout = self._selected_layout()
        output = Path(filename)
        self._run_job(
            "export SVG map",
            lambda: export_disk_map_svg(
                self.current_path,
                output,
                layout or None,
                self._selected_encoding(),
            ),
            lambda result: self._show_report_export_result("SVG map", result),
        )

    def _show_report_export_result(self, label: str, result: object) -> None:
        self.summary_labels["status"].setText("ready")
        self.activity_label.setText(f"Wrote {label} to {result}")
        self._append_log(f"Wrote {label} to {result}")

    def extract_dialog(self) -> None:
        if not self._require_image():
            return
        file_path = self._advanced_file_path_text()
        if not file_path or file_path == "/":
            self._warn("Enter a file path to extract.")
            return
        filename, _ = QFileDialog.getSaveFileName(self, "Save extracted file", Path(file_path).name, "All files (*)")
        if not filename:
            return
        layout = self._selected_layout()
        output = Path(filename)
        self._run_job(
            f"extract {file_path}",
            lambda: extract_file_to_path(
                self.current_path,
                layout or None,
                self._selected_encoding(),
                file_path,
                output,
            ),
            lambda result: self._show_report_export_result("file", result),
        )

    def patch_dialog(self) -> None:
        if not self._require_image():
            return
        layout = self._selected_layout()
        if not layout:
            self._warn("Choose a layout before patching a sector.")
            return
        payload = self.patch_payload_input.text().strip()
        if not payload:
            self._warn("Enter a full-sector hex payload before patching.")
            return
        filename, _ = QFileDialog.getSaveFileName(self, "Save patched raw image", "patched.img", "Raw image (*.img);;All files (*)")
        if not filename:
            return
        try:
            replacement = bytes.fromhex(payload)
            track, head, sector = self._display_to_internal_chs(
                int(self.track_input.text()),
                int(self.head_input.text()),
                int(self.sector_input.text()),
            )
        except ValueError as exc:
            self._warn(str(exc))
            return
        output = Path(filename)
        self._run_job(
            f"patch T{track} H{head} S{sector}",
            lambda: replace_flat_sector_bytes_with_copy(
                self.current_path,
                layout,
                track,
                head,
                sector,
                replacement,
                output,
            ),
            self._show_advanced_hex_edit_result,
        )

    def convert_dialog(self) -> None:
        if not self._require_image():
            return
        assert self.current_path is not None
        current_kind = self.current_summary.kind if self.current_summary else self.current_path.suffix.lower().lstrip(".")
        layout_id = self.current_summary.layout_id if self.current_summary else ""
        encoding = self.current_summary.encoding if self.current_summary else self._selected_encoding()
        default_exporter = self.export_combo.currentText() if hasattr(self, "export_combo") else ""
        if not default_exporter:
            default_exporter = self._default_exporter_for_image(current_kind, layout_id, encoding)
        exporter = self._choose_convert_exporter(default_exporter, current_kind, layout_id, encoding)
        if not exporter:
            return
        if exporter == "imd" and self._is_amiga_context(current_kind, layout_id):
            self._warn(
                "IMD will store decoded Amiga sectors only. It will not preserve Amiga physical track "
                "encoding. Use ADF for native Amiga images or SCP for preservation."
            )
        suffix = ".img" if exporter == "raw" else f".{exporter}"
        default_output = self._default_converted_output_path(suffix)
        filename, _ = QFileDialog.getSaveFileName(
            self,
            "Save converted image",
            str(default_output),
            "All files (*)",
        )
        if not filename:
            return
        output = Path(filename)
        if output.suffix == "":
            output = output.with_suffix(suffix)
        if not output.is_absolute() and self.current_path is not None:
            output = self.current_path.parent / output
        layout = self._selected_layout()
        self._run_job(
            f"convert {self.current_path.name} to {exporter}",
            lambda: convert_image(
                self.current_path,
                output,
                exporter,
                layout,
                self._selected_encoding(),
            ),
            self._show_conversion_result,
        )

    def _show_conversion_result(self, result: object) -> None:
        self.summary_labels["status"].setText("ready")
        self.activity_label.setText(
            f"Converted to {result.output_path} ({result.output_size:,} bytes)"
        )
        if result.lossy_warning:
            self._append_log("Warning: conversion may be lossy due to missing or low-confidence sectors")

    def _choose_convert_exporter(self, default_exporter: str, kind: str, layout_id: str, encoding: str) -> str:
        choices = self._exporter_choices_for_image(kind, layout_id, encoding)
        labels = [label for _exporter, label in choices]
        default_index = next(
            (index for index, (exporter, _label) in enumerate(choices) if exporter == default_exporter),
            0,
        )
        selected, ok = QInputDialog.getItem(
            self,
            "Convert Target",
            "Convert image to:",
            labels,
            default_index,
            False,
        )
        if not ok:
            return ""
        for exporter, label in choices:
            if label == selected:
                return exporter
        return ""

    def _is_amiga_context(self, kind: str, layout_id: str) -> bool:
        return kind == "adf" or layout_id.startswith("amiga_")

    def roundtrip_dialog(self) -> None:
        if not self._require_image():
            return
        assert self.current_path is not None
        current_kind = self.current_summary.kind if self.current_summary else self.current_path.suffix.lower().lstrip(".")
        default_to = self.export_combo.currentText() if hasattr(self, "export_combo") else "raw"
        if not default_to:
            default_to = self._default_exporter_for_image(
                current_kind,
                self.current_summary.layout_id if self.current_summary else "",
                self.current_summary.encoding if self.current_summary else "",
            )
        default_back = self._default_roundtrip_back_exporter_for_image(current_kind)
        options = self._roundtrip_options_dialog(default_to, default_back)
        if options is None:
            return

        work_dir = options.get("work_dir")
        json_out = options.get("json_out")
        layout = self._selected_layout()
        self._run_job(
            f"roundtrip {self.current_path.name}",
            lambda: roundtrip_image(
                self.current_path,
                str(options["to"]),
                str(options["back_to"]) if options.get("back_to") else None,
                layout or None,
                self._selected_encoding(),
                work_dir=self._resolve_source_relative_path(Path(str(work_dir))) if work_dir else None,
                json_out=self._resolve_source_relative_path(Path(str(json_out))) if json_out else None,
            ),
            self._show_roundtrip_result,
        )

    def _show_roundtrip_result(self, result: object) -> None:
        state = "MATCH" if result.roundtrip_match else "DIFFER"
        self.summary_labels["status"].setText("ready" if result.roundtrip_match else "suspect")
        self.activity_label.setText(f"Round-trip check: {state}")
        text = json.dumps(result.report, indent=2)
        self.advanced_detail_stack.setCurrentWidget(self.advanced_output)
        self.advanced_output.setPlainText(text)
        self._show_advanced_tab()
        self._advanced_hex_dump = None
        self._update_advanced_hex_edit_actions()
        self.log.append(f"Round-trip {state}:\n{text}")

    def _roundtrip_options_dialog(self, default_to: str, default_back: str) -> Optional[dict[str, object]]:
        dialog = QDialog(self)
        dialog.setWindowTitle("Round Trip Conversion Check")
        layout = QVBoxLayout(dialog)
        intro = QLabel(
            "Convert to an intermediate format, convert back, then compare decoded sector hashes. "
            "This confirms sector-image fidelity rather than raw flux timing equality."
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        form = QFormLayout()
        exporters = ["raw", "imd", "adf", "d64", "d71", "d81", "g64"]
        to_combo = QComboBox()
        to_combo.addItems(exporters)
        self._select_combo_text(to_combo, default_to)
        back_combo = QComboBox()
        back_combo.addItem("Auto", "")
        back_combo.addItems(exporters)
        self._select_combo_text(back_combo, default_back)
        work_dir_input = QLineEdit("")
        report_input = QLineEdit("")
        work_row = QHBoxLayout()
        work_row.addWidget(work_dir_input, 1)
        work_browse = QPushButton("Browse...")
        work_row.addWidget(work_browse)
        report_row = QHBoxLayout()
        report_row.addWidget(report_input, 1)
        report_browse = QPushButton("Browse...")
        report_row.addWidget(report_browse)

        def choose_work_dir() -> None:
            start = str(self.current_path.parent) if self.current_path is not None else ""
            selected = QFileDialog.getExistingDirectory(dialog, "Keep intermediate images in folder", start)
            if selected:
                work_dir_input.setText(selected)

        def choose_report() -> None:
            default = "roundtrip.json"
            if self.current_path is not None:
                default = str(self.current_path.parent / f"{self.current_path.stem}-roundtrip.json")
            selected, _ = QFileDialog.getSaveFileName(dialog, "Save round-trip JSON report", default, "JSON (*.json);;All files (*)")
            if selected:
                report_input.setText(selected)

        work_browse.clicked.connect(choose_work_dir)
        report_browse.clicked.connect(choose_report)
        form.addRow("Intermediate format", to_combo)
        form.addRow("Return format", back_combo)
        form.addRow("Keep intermediates", work_row)
        form.addRow("JSON report", report_row)
        layout.addLayout(form)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)

        if dialog.exec() != QDialog.Accepted:
            return None
        result: dict[str, object] = {"to": to_combo.currentText()}
        back_to = str(back_combo.currentData() or "")
        if back_to:
            result["back_to"] = back_to
        work_dir = work_dir_input.text().strip()
        if work_dir:
            result["work_dir"] = Path(work_dir)
        report = report_input.text().strip()
        if report:
            report_path = Path(report)
            if report_path.suffix == "":
                report_path = report_path.with_suffix(".json")
            if not report_path.is_absolute() and self.current_path is not None:
                report_path = self.current_path.parent / report_path
            result["json_out"] = report_path
        return result

    def _resolve_source_relative_path(self, path: Path) -> Path:
        if path.is_absolute() or self.current_path is None:
            return path
        return self.current_path.parent / path

    def _default_converted_output_path(self, suffix: str) -> Path:
        if self.current_path is None:
            return Path(f"converted{suffix}").resolve()
        source = self.current_path
        base_name = f"{source.stem}-converted{suffix}"
        return source.parent / base_name

    def compare_dialog(self) -> None:
        if not self._require_image():
            return
        other, _ = QFileDialog.getOpenFileName(self, "Compare with image", "", "Disk images (*.scp *.woz *.po *.do *.nib *.img *.imd *.dsk *.dmk *.d64 *.d71 *.d81 *.adf);;All files (*)")
        if not other:
            return
        other_path = Path(other)
        self._run_job(
            f"compare {self.current_path.name} with {other_path.name}",
            lambda: compare_images(self.current_path, other_path),
            self._show_comparison_result,
        )

    def _show_comparison_result(self, result: object) -> None:
        self.summary_labels["status"].setText("ready" if result.identical else "suspect")
        state = "MATCH" if result.identical else "DIFFER"
        self.activity_label.setText(f"Comparison: {state}")
        text = json.dumps(result.report, indent=2)
        self.advanced_detail_stack.setCurrentWidget(self.advanced_output)
        self.advanced_output.setPlainText(text)
        self._show_advanced_tab()
        self._advanced_hex_dump = None
        self._update_advanced_hex_edit_actions()
        self.log.append(f"Comparison {state}:\n{text}")

    def open_provenance(self) -> None:
        filename, _ = QFileDialog.getOpenFileName(self, "Open provenance", "", "Provenance (*.json);;All files (*)")
        if not filename:
            return
        self._run_job("provenance show", lambda: provenance_json(Path(filename)), lambda data: self._append_log(json.dumps(data, indent=2)))

    def _show_command_result(self, result: object) -> None:
        self.summary_labels["status"].setText("ready" if result.returncode == 0 else "error")
        self.activity_label.setText(
            f"Command finished with exit {result.returncode}: {' '.join(result.args)}"
        )
        lines = [f"$ {' '.join(result.args)}", f"exit {result.returncode}"]
        if result.stdout:
            lines.append(result.stdout.rstrip())
        if result.stderr:
            lines.append(result.stderr.rstrip())
        text = "\n".join(lines)
        self.advanced_detail_stack.setCurrentWidget(self.advanced_output)
        self.advanced_output.setPlainText(text)
        self._show_advanced_tab()
        self._advanced_hex_dump = None
        self._update_advanced_hex_edit_actions()
        self.log.append(text)

    def _show_text_view(self, report: object) -> None:
        self.advanced_detail_stack.setCurrentWidget(self.advanced_output)
        self.advanced_output.setPlainText(report.text)
        self._show_advanced_tab()
        self._advanced_hex_dump = None
        self._update_advanced_hex_edit_actions()
        self.activity_label.setText(f"Loaded {report.title}.")
        self.log.append(f"{report.title}\n{report.text}")

    def _show_advanced_hex_dump(self, dump: object) -> None:
        self._advanced_hex_dump = dump
        self.advanced_hex_title_label.setText(f"{dump.title}  ({dump.size:,} bytes)")
        self.advanced_hex_text.setPlainText(dump.text)
        self._update_advanced_hex_edit_actions()
        self._show_hex_tab(advanced=True)
        self.activity_label.setText(f"Loaded hex view for {dump.title}.")
        self.log.append(f"Loaded hex view for {dump.title} ({dump.size:,} bytes)")

    def _show_advanced_hex_edit_result(self, result: object) -> None:
        self.activity_label.setText(
            f"Saved edited {result.mode} hex for {result.target} ({result.bytes:,} bytes) to {result.path}."
        )
        self._append_log(
            f"Saved edited {result.mode} hex for {result.target} ({result.bytes:,} bytes) to {result.path}"
        )

    def _require_image(self) -> bool:
        if self.current_path is None:
            self._warn("Open a disk image first.")
            return False
        return True

    def _warn(self, message: str) -> None:
        QMessageBox.warning(self, "Fluxctl Studio", message)


def main() -> None:
    """Launch Fluxctl Studio."""

    app = QApplication(sys.argv)
    window = FluxctlStudio()
    window.show()
    raise SystemExit(app.exec())


if __name__ == "__main__":
    main()
