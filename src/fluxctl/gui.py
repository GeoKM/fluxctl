"""Fluxctl Studio desktop application."""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path
from typing import Callable, Optional

from . import studio_services as services


try:  # pragma: no cover - exercised only when GUI dependencies are installed.
    from PySide6.QtCore import QObject, QRunnable, Qt, QThreadPool, Signal, Slot
    from PySide6.QtGui import QAction, QColor, QFont, QPainter, QPen
    from PySide6.QtWidgets import (
        QApplication,
        QAbstractItemView,
        QComboBox,
        QFileDialog,
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
        QSplitter,
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


class Job(QRunnable):
    def __init__(self, fn: Callable[[], object]):
        super().__init__()
        self.fn = fn
        self.signals = JobSignals()

    @Slot()
    def run(self) -> None:
        try:
            result = self.fn()
            try:
                self.signals.finished.emit(result)
            except RuntimeError:
                pass
        except Exception as exc:  # pragma: no cover - GUI error transport.
            try:
                self.signals.failed.emit(str(exc))
            except RuntimeError:
                pass


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
            return [(state, self.LEGEND_LABELS[state]) for state in ("bam_file", "bam_system", "bam_used", "bam_free")]
        return [(state, self.LEGEND_LABELS[state]) for state in ("good", "weak", "bad", "unused")]

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
                    painter.setPen(QPen(QColor("#101823"), 1.35))
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
        row_label_width = 26
        columns = max(len(head_groups), 1)
        column_width = max((width - outer_gap * 2 - column_gap * (columns - 1)) / columns, 1)
        grid_height = max(height - top - legend_height - 12, 1)

        painter.setFont(QFont("Arial", 10))
        painter.setPen(QPen(QColor("#dce7f7"), 1))
        for column, (head, rows) in enumerate(head_groups):
            if not rows:
                continue
            left = outer_gap + column * (column_width + column_gap)
            grid_width = max(column_width - row_label_width, 1)
            max_cols = max(len(sectors) for _row_index, _track_id, sectors in rows)
            cell = max(4.0, min(grid_width / max(max_cols, 1), grid_height / max(len(rows), 1)))
            cell = min(cell, 18.0)
            painter.setPen(QPen(QColor("#dce7f7"), 1))
            painter.setFont(QFont("Arial", 12, QFont.Bold))
            painter.drawText(int(left), 8, int(column_width), 24, Qt.AlignCenter, f"Head {head}")
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
                    f"T{track + 1:02d}",
                )
                for sector_index, state in enumerate(sectors):
                    x = left + row_label_width + sector_index * cell
                    painter.setBrush(self.STATE_COLORS.get(state, QColor("#6b7280")))
                    painter.setPen(QPen(QColor("#111b28"), 1.25))
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

    def _draw_legend(self, painter: QPainter, width: int, height: int) -> None:  # pragma: no cover - visual rendering.
        painter.setFont(QFont("Arial", 11))
        painter.setPen(QPen(QColor("#dce7f7"), 1))
        y = max(height - 28, 8)
        x = 18
        for state, label in self.legend_items():
            painter.setBrush(self.STATE_COLORS[state])
            painter.setPen(Qt.NoPen)
            painter.drawRoundedRect(x, y + 4, 14, 14, 3, 3)
            painter.setPen(QPen(QColor("#dce7f7"), 1))
            painter.drawText(x + 20, y, max(width - x - 20, 1), 24, Qt.AlignLeft | Qt.AlignVCenter, label)
            x += 116 if state != "unused" else 150

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
        self.resize(1320, 840)
        self.thread_pool = QThreadPool.globalInstance()
        self.active_jobs: set[Job] = set()
        self.current_path: Optional[Path] = None
        self.current_summary = None
        self.file_browser_path = "/"
        self.advanced_file_browser_path = "/"
        self._loading_advanced_file_paths = False
        self.layout_options = services.load_layout_options()
        self._build_ui()
        self._apply_style()
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
        root_layout = QHBoxLayout(root)
        root_layout.setContentsMargins(0, 0, 0, 0)

        sidebar = QFrame()
        sidebar.setObjectName("sidebar")
        sidebar_layout = QVBoxLayout(sidebar)
        self.title = QLabel("Fluxctl Studio")
        self.title.setObjectName("title")
        self.file_label = QLabel("No image loaded")
        self.file_label.setWordWrap(True)
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
        sidebar_layout.addWidget(self.title)
        sidebar_layout.addWidget(self.file_label)
        sidebar_layout.addWidget(self.mode)
        sidebar_layout.addWidget(self.map_view)
        sidebar_layout.addWidget(self.open_button)
        sidebar_layout.addWidget(self.doctor_button)
        sidebar_layout.addStretch(1)

        self.stack = QStackedWidget()
        self.simple = self._build_simple_mode()
        self.advanced = self._build_advanced_mode()
        self.stack.addWidget(self.simple)
        self.stack.addWidget(self.advanced)

        root_layout.addWidget(sidebar, 0)
        root_layout.addWidget(self.stack, 1)
        self.setCentralWidget(root)

    def _build_simple_mode(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        self.summary_grid = QGridLayout()
        self.summary_labels = {}
        for row, label in enumerate(["Layout", "Encoding", "Filesystem", "Confidence", "Size", "Status"]):
            key = label.lower()
            self.summary_grid.addWidget(QLabel(label), row, 0)
            value = QLabel("-")
            value.setObjectName("metric")
            self.summary_labels[key] = value
            self.summary_grid.addWidget(value, row, 1)
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
        ]:
            button = QPushButton(text)
            button.clicked.connect(handler)
            actions.addWidget(button)
        self.map_widget = DiskMapWidget()
        self.map_widget.sectorClicked.connect(self.load_sector_hex_from_map)
        self.file_path_label = QLabel("/")
        self.file_path_label.setObjectName("filePath")
        self.file_path_label.setWordWrap(True)
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
        self.files_table.setMinimumHeight(260)
        self.files_table.setHorizontalHeaderLabels(["Name", "Kind", "Size"])
        self.files_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.files_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.files_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.files_table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.files_table.itemDoubleClicked.connect(self.open_selected_file_entry)
        self.file_panel = QWidget()
        file_panel_layout = QVBoxLayout(self.file_panel)
        file_panel_layout.setContentsMargins(0, 0, 0, 0)
        file_panel_layout.addLayout(file_nav)
        file_panel_layout.addWidget(self.files_table)
        self.log = QTextEdit()
        self.log.setReadOnly(True)
        self.log.setMinimumHeight(260)
        self.hex_title_label = QLabel("No hex data loaded")
        self.hex_title_label.setObjectName("filePath")
        self.hex_track_input = QLineEdit("0")
        self.hex_head_input = QLineEdit("0")
        self.hex_sector_input = QLineEdit("1")
        self.hex_sector_button = QPushButton("View Sector Hex")
        self.hex_sector_button.clicked.connect(self.view_sector_hex)
        hex_controls = QHBoxLayout()
        hex_controls.addWidget(QLabel("Track"))
        hex_controls.addWidget(self.hex_track_input)
        hex_controls.addWidget(QLabel("Head"))
        hex_controls.addWidget(self.hex_head_input)
        hex_controls.addWidget(QLabel("Sector"))
        hex_controls.addWidget(self.hex_sector_input)
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

        map_toggle_row = QHBoxLayout()
        map_toggle_row.addStretch(1)
        self.map_toggle_button = QPushButton("Hide Disk Map")
        self.map_toggle_button.setToolTip("Hide the disk map and expand the Files, Hex, and Jobs panel.")
        self.map_toggle_button.clicked.connect(self.toggle_disk_map_panel)
        map_toggle_row.addWidget(self.map_toggle_button)

        self.simple_splitter = QSplitter(Qt.Vertical)
        self.simple_splitter.setChildrenCollapsible(False)
        self.map_panel = QWidget()
        self.map_panel_visible = True
        self._map_panel_sizes = [500, 340]
        upper_layout = QVBoxLayout(self.map_panel)
        upper_layout.addLayout(self.summary_grid)
        upper_layout.addWidget(self.activity_label)
        upper_layout.addLayout(actions)
        self.map_canvas_panel = QWidget()
        map_canvas_layout = QVBoxLayout(self.map_canvas_panel)
        map_canvas_layout.setContentsMargins(0, 0, 0, 0)
        map_canvas_layout.addWidget(self.map_widget, 1)
        upper_layout.addWidget(self.map_canvas_panel, 1)
        self.lower_tabs = QTabWidget()
        self.lower_tabs.setMinimumHeight(300)
        self.lower_tabs.addTab(self.file_panel, "Files")
        self.lower_tabs.addTab(self.hex_panel, "Hex")
        self.lower_tabs.addTab(self.log, "Jobs")
        self.simple_splitter.addWidget(self.map_panel)
        self.simple_splitter.addWidget(self.lower_tabs)
        self.simple_splitter.setStretchFactor(0, 3)
        self.simple_splitter.setStretchFactor(1, 2)
        self.simple_splitter.setSizes(self._map_panel_sizes)
        layout.addLayout(map_toggle_row)
        layout.addWidget(self.simple_splitter)
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
        self.export_combo.addItems(["raw", "imd", "adf", "d64", "g64"])
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
        self.advanced_hex_text = QTextEdit()
        self.advanced_hex_text.setReadOnly(True)
        self.advanced_hex_text.setFont(QFont("Menlo"))
        advanced_hex_panel = QWidget()
        advanced_hex_layout = QVBoxLayout(advanced_hex_panel)
        advanced_hex_layout.setContentsMargins(0, 0, 0, 0)
        advanced_hex_layout.addWidget(self.advanced_hex_title_label)
        advanced_hex_layout.addWidget(self.advanced_hex_text)
        self.advanced_detail_stack = QStackedWidget()
        self.advanced_detail_stack.addWidget(self.advanced_output)
        self.advanced_detail_stack.addWidget(advanced_hex_panel)
        layout.addLayout(controls)
        layout.addLayout(buttons)
        layout.addWidget(self.advanced_detail_stack, 1)
        return page

    def _apply_style(self) -> None:
        self.setStyleSheet(
            """
            QMainWindow, QWidget { background: #111722; color: #e7edf7; font-size: 13px; }
            #sidebar { background: #090d14; min-width: 230px; max-width: 280px; border-right: 1px solid #263241; }
            #title { font-size: 24px; font-weight: 700; margin-bottom: 16px; }
            QLabel#metric { color: #9ee6b8; font-weight: 600; }
            QLabel#activity { background: #172233; border: 1px solid #2f4158; border-radius: 6px; padding: 8px; color: #dce7f7; }
            QLabel#filePath { color: #9ee6b8; font-weight: 600; padding: 4px 8px; }
            QPushButton { background: #243348; border: 1px solid #40536c; border-radius: 6px; padding: 8px 10px; }
            QPushButton:hover { background: #2f435f; }
            QComboBox, QLineEdit, QTextEdit, QTableWidget {
                background: #0c1018; border: 1px solid #2d3a4b; border-radius: 6px; padding: 6px;
            }
            QHeaderView::section { background: #1b2636; color: #dce7f7; padding: 6px; border: 0; }
            QTabBar::tab { background: #1b2636; padding: 8px 14px; border-top-left-radius: 6px; border-top-right-radius: 6px; }
            QTabBar::tab:selected { background: #31455f; }
            QSplitter::handle {
                background: #263241;
                border: 1px solid #40536c;
            }
            QSplitter::handle:vertical {
                height: 10px;
                margin: 4px 0;
            }
            QSplitter::handle:hover { background: #3a516e; }
            """
        )

    def _switch_mode(self, index: int) -> None:
        self.stack.setCurrentIndex(index)

    def toggle_disk_map_panel(self) -> None:
        if self.map_panel_visible:
            sizes = self.simple_splitter.sizes()
            if sizes and sizes[0] > 0:
                self._map_panel_sizes = sizes
            self.map_canvas_panel.setVisible(False)
            self.map_panel_visible = False
            self.map_toggle_button.setText("Show Disk Map")
            self.map_toggle_button.setToolTip("Show the disk map above the Files, Hex, and Jobs panel.")
            total_size = sum(sizes) if sizes else sum(self._map_panel_sizes)
            self.simple_splitter.setSizes([1, max(1, total_size - 1)])
            return

        self.map_canvas_panel.setVisible(True)
        self.map_panel_visible = True
        self.map_toggle_button.setText("Hide Disk Map")
        self.map_toggle_button.setToolTip("Hide the disk map and expand the Files, Hex, and Jobs panel.")
        self.simple_splitter.setSizes(self._map_panel_sizes)

    def _selected_layout(self) -> str:
        return str(self.layout_combo.currentData() or (self.current_summary.layout_id if self.current_summary else ""))

    def _selected_encoding(self) -> str:
        if self.current_summary and self.current_summary.encoding:
            return self.current_summary.encoding
        return self.encoding_combo.currentText()

    def _selected_map_view(self) -> str:
        return str(self.map_view.currentData() or "logical")

    def _append_log(self, text: str) -> None:
        self.log.append(text)
        self.advanced_output.append(text)

    def _run_job(self, label: str, fn: Callable[[], object], done: Callable[[object], None]) -> None:
        self.summary_labels["status"].setText("running")
        self.activity_label.setText(f"Running {label}...")
        self._append_log(f"$ {label}")
        job = Job(fn)
        self.active_jobs.add(job)
        job.signals.finished.connect(lambda result, current_job=job: self._finish_job(current_job, label, result, done))
        job.signals.failed.connect(lambda message, current_job=job: self._fail_job(current_job, label, message))
        self.thread_pool.start(job)

    def _finish_job(self, job: Job, label: str, result: object, done: Callable[[object], None]) -> None:
        self.active_jobs.discard(job)
        self.activity_label.setText(f"Finished {label}.")
        if self.summary_labels["status"].text() == "running":
            self.summary_labels["status"].setText("ready")
        done(result)

    def _fail_job(self, job: Job, label: str, message: str) -> None:
        self.active_jobs.discard(job)
        self.summary_labels["status"].setText("error")
        self.activity_label.setText(f"{label} failed: {message}")
        self._append_log(f"Error: {message}")

    def open_image(self) -> None:
        filename, _ = QFileDialog.getOpenFileName(
            self,
            "Open disk image",
            "",
            "Disk images (*.scp *.img *.imd *.d64 *.d71 *.d81 *.adf);;All files (*)",
        )
        if filename:
            self.current_path = Path(filename)
            self.file_label.setText(str(self.current_path))
            self._clear_image_results()
            self.run_probe()

    def _clear_image_results(self) -> None:
        self.current_summary = None
        self._set_file_browser_path("/")
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
        self._run_job("doctor", services.doctor_report, self._show_doctor)

    def _show_doctor(self, report: object) -> None:
        self.summary_labels["status"].setText(str(report.get("overall", "unknown")) if isinstance(report, dict) else "unknown")
        summary = self._doctor_summary_text(report) if isinstance(report, dict) else str(report)
        self.log.append(json.dumps(report, indent=2))
        if self.current_path is None:
            self.advanced_detail_stack.setCurrentWidget(self.advanced_output)
            self.advanced_output.setPlainText(summary)

    def run_probe(self) -> None:
        if not self._require_image():
            return
        assert self.current_path is not None
        self._run_job("probe", lambda: services.summarize_image(self.current_path), self._show_summary)

    def _show_summary(self, summary: object) -> None:
        self.current_summary = summary
        self.summary_labels["layout"].setText(summary.layout_id or "unknown")
        self.summary_labels["encoding"].setText(summary.encoding or "unknown")
        self.summary_labels["filesystem"].setText(summary.filesystem or "unknown")
        self.summary_labels["confidence"].setText(f"{summary.confidence:.2f}")
        self.summary_labels["size"].setText(f"{summary.size:,} bytes")
        self.summary_labels["status"].setText("ready")
        self.activity_label.setText(
            f"Probe found {summary.layout_id or 'unknown layout'} with {summary.confidence:.2f} confidence."
        )
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
            return

        assert self.current_summary is not None
        self._select_combo_data(self.layout_combo, self.current_summary.layout_id)
        self._select_combo_text(self.encoding_combo, self.current_summary.encoding)
        self._select_combo_text(self.export_combo, self._default_exporter_for_image(self.current_summary.kind))
        self._select_combo_data(self.dump_mode_combo, "sector")
        self.track_input.setText("0")
        self.head_input.setText("0")
        self.sector_input.setText("1")
        self._load_advanced_file_path_options("/")
        self.patch_payload_input.clear()
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
                entries = services.list_files(self.current_path, layout, encoding, self.advanced_file_browser_path)
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

    def _default_exporter_for_image(self, kind: str) -> str:
        if kind in {"img", "raw"}:
            return "raw"
        if kind in {"imd", "adf", "d64", "g64"}:
            return kind
        if kind == "d71":
            return "d64"
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

    def _fat12_img_write_support_reason(self) -> tuple[bool, str]:
        if self.current_path is None or self.current_summary is None:
            return False, "Open and probe a disk image before using write actions."
        if self.current_path.suffix.lower() != ".img":
            return False, "Write actions currently support FAT12 flat .img images only."
        if self.current_summary.filesystem != "fat12":
            filesystem = self.current_summary.filesystem or "unknown"
            return False, f"Write actions currently support FAT12 only; this image detected as {filesystem}."
        return True, "Available for FAT12 flat .img images. Operations write a new image copy."

    def _update_filesystem_write_actions(self) -> None:
        supported, reason = self._fat12_img_write_support_reason()
        for button in self._filesystem_write_buttons():
            button.setEnabled(supported)
            button.setToolTip(reason)

    def run_qc(self) -> None:
        if not self._require_image():
            return
        assert self.current_path is not None
        layout = self._selected_layout() or None
        encoding = self._selected_encoding()
        self._run_job("qc", lambda: services.build_qc_for_image(self.current_path, layout, encoding), self._show_qc)

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
            lambda: services.build_disk_map_for_image(self.current_path, layout, encoding, map_view),
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
            lambda: services.list_files(self.current_path, layout, encoding, directory),
            self._show_files,
        )

    def _show_files(self, entries: object) -> None:
        if not entries:
            self.files_table.setRowCount(1)
            self.files_table.setItem(0, 0, QTableWidgetItem(f"No filesystem entries found in {self.file_browser_path}"))
            self.files_table.setItem(0, 1, QTableWidgetItem("-"))
            self.files_table.setItem(0, 2, QTableWidgetItem("-"))
            self.activity_label.setText(f"No supported filesystem entries were found in {self.file_browser_path}.")
            self._append_log(f"Listed 0 filesystem entries in {self.file_browser_path}")
            return
        self.files_table.setRowCount(len(entries))
        for row, entry in enumerate(entries):
            name_item = QTableWidgetItem(entry.name)
            name_item.setData(Qt.UserRole, entry.path)
            name_item.setData(Qt.UserRole + 1, entry.is_dir)
            self.files_table.setItem(row, 0, name_item)
            self.files_table.setItem(row, 1, QTableWidgetItem(entry.kind))
            self.files_table.setItem(row, 2, QTableWidgetItem(str(entry.size)))
        self.activity_label.setText(f"Listed {len(entries)} filesystem entries in {self.file_browser_path}.")
        self._append_log(f"Listed {len(entries)} filesystem entries in {self.file_browser_path}")

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
            lambda: services.file_hex_dump(self.current_path, layout, encoding, file_path, max_bytes=65536),
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
                lambda: services.export_filesystem_entry(self.current_path, layout, encoding, file_path, destination),
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
        layout = self._selected_layout() or None
        encoding = self._selected_encoding()
        self._run_job(
            f"export {len(selected_paths)} selected item(s)",
            lambda: services.export_filesystem_entries(
                self.current_path,
                layout,
                encoding,
                selected_paths,
                destination,
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
            "Disk images (*.img);;All files (*)",
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
            lambda: services.replace_file_with_copy(
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
            lambda: services.delete_filesystem_entry_with_copy(
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
            "FAT12 import currently requires an 8.3-compatible file name and does not overwrite existing entries. "
            "The original image will not be modified. Continue?"
        )
        if not self._confirm_mutation("Import file into image copy", question):
            return
        layout = self._selected_layout() or None
        encoding = self._selected_encoding()
        self._run_job(
            f"import file {host_file.name} with copy",
            lambda: services.import_file_with_copy(
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
            "FAT12 import currently requires 8.3-compatible file and directory names and does not overwrite "
            "existing entries. The original image will not be modified. Continue?"
        )
        if not self._confirm_mutation("Import directory into image copy", question):
            return
        layout = self._selected_layout() or None
        encoding = self._selected_encoding()
        self._run_job(
            f"import directory {host_directory.name} with copy",
            lambda: services.import_directory_with_copy(
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
            "FAT12 directory creation currently requires an 8.3-compatible name and does not overwrite existing "
            "entries. The original image will not be modified. Continue?"
        )
        if not self._confirm_mutation("Create directory in image copy", question):
            return
        layout = self._selected_layout() or None
        encoding = self._selected_encoding()
        self._run_job(
            f"mkdir {name} with copy",
            lambda: services.create_directory_with_copy(
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
        output_name, _ = QFileDialog.getSaveFileName(
            self,
            title,
            str(default_output),
            "Disk images (*.img);;All files (*)",
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
            track = int(self.hex_track_input.text())
            head = int(self.hex_head_input.text())
            sector = int(self.hex_sector_input.text())
        except ValueError:
            self._warn("Track, head, and sector must be integer values.")
            return
        self._run_job(
            f"hex sector {track}:{head}:{sector}",
            lambda: services.sector_hex_dump(self.current_path, layout, encoding, track, head, sector),
            self._show_hex_dump,
        )

    def load_sector_hex_from_map(self, track: int, head: int, sector: int) -> None:
        self.hex_track_input.setText(str(track))
        self.hex_head_input.setText(str(head))
        self.hex_sector_input.setText(str(sector))
        self.view_sector_hex()

    def _show_hex_dump(self, dump: object) -> None:
        self.hex_title_label.setText(f"{dump.title}  ({dump.size:,} bytes)")
        self.hex_text.setPlainText(dump.text)
        self.lower_tabs.setCurrentWidget(self.hex_panel)
        self.activity_label.setText(f"Loaded hex view for {dump.title}.")
        self._append_log(f"Loaded hex view for {dump.title} ({dump.size:,} bytes)")

    def run_info(self) -> None:
        self._run_cli(["info", str(self.current_path)] if self._require_image() else [])

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
        self._run_job(
            f"sectors T{track} H{head}",
            lambda: services.sector_list(self.current_path, layout, encoding, track, head),
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
            lambda: services.sector_hex_dump(self.current_path, layout, encoding, track, head, sector),
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
            lambda: services.file_hex_dump(self.current_path, layout, encoding, file_path, max_bytes=65536),
            self._show_advanced_hex_dump,
        )

    def qc_export_dialog(self) -> None:
        if not self._require_image():
            return
        filename, _ = QFileDialog.getSaveFileName(self, "Save QC JSON", "qc.json", "JSON (*.json);;All files (*)")
        if not filename:
            return
        args = ["qc", str(self.current_path), "--json-out", filename, "--encoding", self._selected_encoding()]
        layout = self._selected_layout()
        if layout:
            args.extend(["--layout", layout])
        self._run_cli(args)

    def svg_export_dialog(self) -> None:
        if not self._require_image():
            return
        filename, _ = QFileDialog.getSaveFileName(self, "Save SVG disk map", "map.svg", "SVG (*.svg);;All files (*)")
        if not filename:
            return
        args = ["visualize", str(self.current_path), "--format", "svg", "--out", filename, "--encoding", self._selected_encoding()]
        layout = self._selected_layout()
        if layout:
            args.extend(["--layout", layout])
        self._run_cli(args)

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
        args = ["extract", str(self.current_path), "--path", file_path, "--out", filename, "--encoding", self._selected_encoding()]
        layout = self._selected_layout()
        if layout:
            args.extend(["--layout", layout])
        self._run_cli(args)

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
        target = f"{self.track_input.text()}:{self.head_input.text()}:{self.sector_input.text()}:{payload}"
        self._run_cli(
            [
                "patch",
                str(self.current_path),
                "--layout",
                layout,
                "--write-sector",
                target,
                "--out",
                filename,
            ]
        )

    def convert_dialog(self) -> None:
        if not self._require_image():
            return
        exporter = self.export_combo.currentText() if hasattr(self, "export_combo") else "raw"
        suffix = ".img" if exporter == "raw" else f".{exporter}"
        filename, _ = QFileDialog.getSaveFileName(self, "Save converted image", f"converted{suffix}", "All files (*)")
        if not filename:
            return
        layout = self._selected_layout()
        args = ["convert", str(self.current_path), "--to", exporter, "--out", filename]
        if layout:
            args.extend(["--layout", layout])
        args.extend(["--encoding", self._selected_encoding()])
        self._run_cli(args)

    def compare_dialog(self) -> None:
        if not self._require_image():
            return
        other, _ = QFileDialog.getOpenFileName(self, "Compare with image", "", "Disk images (*.scp *.img *.imd *.d64 *.d71 *.d81 *.adf);;All files (*)")
        if not other:
            return
        self._run_cli(["compare", str(self.current_path), other])

    def open_provenance(self) -> None:
        filename, _ = QFileDialog.getOpenFileName(self, "Open provenance", "", "Provenance (*.json);;All files (*)")
        if not filename:
            return
        self._run_job("provenance show", lambda: services.provenance_json(Path(filename)), lambda data: self._append_log(json.dumps(data, indent=2)))

    def _run_cli(self, args: list[str]) -> None:
        if not args:
            return
        self._run_job(" ".join(args), lambda: services.run_fluxctl_command(args), self._show_command_result)

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
        self.log.append(text)

    def _show_text_view(self, report: object) -> None:
        self.advanced_detail_stack.setCurrentWidget(self.advanced_output)
        self.advanced_output.setPlainText(report.text)
        self.activity_label.setText(f"Loaded {report.title}.")
        self.log.append(f"{report.title}\n{report.text}")

    def _show_advanced_hex_dump(self, dump: object) -> None:
        self.advanced_hex_title_label.setText(f"{dump.title}  ({dump.size:,} bytes)")
        self.advanced_hex_text.setPlainText(dump.text)
        self.advanced_detail_stack.setCurrentWidget(self.advanced_hex_text.parentWidget())
        self.activity_label.setText(f"Loaded hex view for {dump.title}.")
        self.log.append(f"Loaded hex view for {dump.title} ({dump.size:,} bytes)")

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
