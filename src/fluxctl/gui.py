"""Fluxctl Studio desktop application."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Callable, Optional

from . import studio_services as services


try:  # pragma: no cover - exercised only when GUI dependencies are installed.
    from PySide6.QtCore import QObject, QRunnable, Qt, QThreadPool, Signal, Slot
    from PySide6.QtGui import QAction, QColor, QPainter, QPen
    from PySide6.QtWidgets import (
        QApplication,
        QComboBox,
        QFileDialog,
        QFrame,
        QGridLayout,
        QHBoxLayout,
        QHeaderView,
        QLabel,
        QLineEdit,
        QMainWindow,
        QMessageBox,
        QPushButton,
        QSplitter,
        QStackedWidget,
        QTableWidget,
        QTableWidgetItem,
        QTabWidget,
        QTextEdit,
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
            self.signals.finished.emit(self.fn())
        except Exception as exc:  # pragma: no cover - GUI error transport.
            self.signals.failed.emit(str(exc))


class DiskMapWidget(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.disk_map = None
        self.setMinimumHeight(360)

    def set_disk_map(self, disk_map) -> None:
        self.disk_map = disk_map
        self.update()

    def paintEvent(self, _event) -> None:  # pragma: no cover - visual rendering.
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.fillRect(self.rect(), QColor("#0c1018"))
        if not self.disk_map or not self.disk_map.tracks:
            painter.setPen(QPen(QColor("#788296"), 1))
            painter.drawText(self.rect(), Qt.AlignCenter, "Open an image to render the disk map")
            return

        width = self.width()
        height = self.height()
        cx = width / 2
        cy = height / 2
        max_radius = min(width, height) * 0.45
        track_count = max(len(self.disk_map.tracks), 1)
        ring_width = max(max_radius / track_count, 2.0)
        colors = {
            "good": QColor("#35d07f"),
            "weak": QColor("#f2c94c"),
            "bad": QColor("#e05a47"),
        }
        painter.setPen(Qt.NoPen)
        for track_idx, sectors in enumerate(self.disk_map.tracks):
            radius = max_radius - (track_idx * ring_width)
            if radius <= 4:
                break
            sector_count = max(len(sectors), 1)
            for sector_idx, state in enumerate(sectors):
                painter.setBrush(colors.get(state, QColor("#6b7280")))
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
            inner_radius = max(radius - ring_width + 1, 0)
            painter.drawEllipse(
                int(cx - inner_radius),
                int(cy - inner_radius),
                int(inner_radius * 2),
                int(inner_radius * 2),
            )


class FluxctlStudio(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Fluxctl Studio")
        self.resize(1320, 840)
        self.thread_pool = QThreadPool.globalInstance()
        self.current_path: Optional[Path] = None
        self.current_summary = None
        self.layout_options = services.load_layout_options()
        self._build_ui()
        self._apply_style()
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
        self.doctor_button = QPushButton("Run Doctor")
        self.doctor_button.clicked.connect(self.run_doctor)
        self.open_button = QPushButton("Open Image")
        self.open_button.clicked.connect(self.open_image)
        sidebar_layout.addWidget(self.title)
        sidebar_layout.addWidget(self.file_label)
        sidebar_layout.addWidget(self.mode)
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
        self.files_table = QTableWidget(0, 3)
        self.files_table.setHorizontalHeaderLabels(["Name", "Kind", "Size"])
        self.files_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.log = QTextEdit()
        self.log.setReadOnly(True)

        splitter = QSplitter(Qt.Vertical)
        upper = QWidget()
        upper_layout = QVBoxLayout(upper)
        upper_layout.addLayout(self.summary_grid)
        upper_layout.addLayout(actions)
        upper_layout.addWidget(self.map_widget, 1)
        lower = QTabWidget()
        lower.addTab(self.files_table, "Files")
        lower.addTab(self.log, "Jobs")
        splitter.addWidget(upper)
        splitter.addWidget(lower)
        splitter.setSizes([560, 220])
        layout.addWidget(splitter)
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
        self.track_input = QLineEdit("0")
        self.head_input = QLineEdit("0")
        self.sector_input = QLineEdit("1")
        self.file_path_input = QLineEdit("/")
        self.patch_payload_input = QLineEdit("")
        controls.addWidget(QLabel("Layout"), 0, 0)
        controls.addWidget(self.layout_combo, 0, 1, 1, 3)
        controls.addWidget(QLabel("Encoding"), 1, 0)
        controls.addWidget(self.encoding_combo, 1, 1)
        controls.addWidget(QLabel("Exporter"), 1, 2)
        controls.addWidget(self.export_combo, 1, 3)
        controls.addWidget(QLabel("Track / Head / Sector"), 2, 0)
        controls.addWidget(self.track_input, 2, 1)
        controls.addWidget(self.head_input, 2, 2)
        controls.addWidget(self.sector_input, 2, 3)
        controls.addWidget(QLabel("File Path"), 3, 0)
        controls.addWidget(self.file_path_input, 3, 1, 1, 3)
        controls.addWidget(QLabel("Patch Hex"), 4, 0)
        controls.addWidget(self.patch_payload_input, 4, 1, 1, 3)

        buttons = QHBoxLayout()
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

        self.advanced_output = QTextEdit()
        self.advanced_output.setReadOnly(True)
        layout.addLayout(controls)
        layout.addLayout(buttons)
        layout.addWidget(self.advanced_output, 1)
        return page

    def _apply_style(self) -> None:
        self.setStyleSheet(
            """
            QMainWindow, QWidget { background: #111722; color: #e7edf7; font-size: 13px; }
            #sidebar { background: #090d14; min-width: 230px; max-width: 280px; border-right: 1px solid #263241; }
            #title { font-size: 24px; font-weight: 700; margin-bottom: 16px; }
            QLabel#metric { color: #9ee6b8; font-weight: 600; }
            QPushButton { background: #243348; border: 1px solid #40536c; border-radius: 6px; padding: 8px 10px; }
            QPushButton:hover { background: #2f435f; }
            QComboBox, QLineEdit, QTextEdit, QTableWidget {
                background: #0c1018; border: 1px solid #2d3a4b; border-radius: 6px; padding: 6px;
            }
            QHeaderView::section { background: #1b2636; color: #dce7f7; padding: 6px; border: 0; }
            QTabBar::tab { background: #1b2636; padding: 8px 14px; border-top-left-radius: 6px; border-top-right-radius: 6px; }
            QTabBar::tab:selected { background: #31455f; }
            """
        )

    def _switch_mode(self, index: int) -> None:
        self.stack.setCurrentIndex(index)

    def _selected_layout(self) -> str:
        return str(self.layout_combo.currentData() or (self.current_summary.layout_id if self.current_summary else ""))

    def _selected_encoding(self) -> str:
        if self.current_summary and self.current_summary.encoding:
            return self.current_summary.encoding
        return self.encoding_combo.currentText()

    def _append_log(self, text: str) -> None:
        self.log.append(text)
        self.advanced_output.append(text)

    def _run_job(self, label: str, fn: Callable[[], object], done: Callable[[object], None]) -> None:
        self._append_log(f"$ {label}")
        job = Job(fn)
        job.signals.finished.connect(done)
        job.signals.failed.connect(lambda message: self._append_log(f"Error: {message}"))
        self.thread_pool.start(job)

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
            self.run_probe()

    def run_doctor(self) -> None:
        self._run_job("doctor", services.doctor_report, self._show_doctor)

    def _show_doctor(self, report: object) -> None:
        self.summary_labels["status"].setText(str(report.get("overall", "unknown")) if isinstance(report, dict) else "unknown")
        self._append_log(json.dumps(report, indent=2))

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
        self._append_log(json.dumps(summary.__dict__, indent=2))
        self.run_map()

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
        self._append_log(report.to_json())

    def run_map(self) -> None:
        if not self._require_image():
            return
        assert self.current_path is not None
        layout = self._selected_layout() or None
        encoding = self._selected_encoding()
        self._run_job("map", lambda: services.build_disk_map_for_image(self.current_path, layout, encoding), self._show_map)

    def _show_map(self, disk_map: object) -> None:
        self.map_widget.set_disk_map(disk_map)
        self._append_log(f"Rendered {disk_map.total_tracks} tracks with {disk_map.max_sectors_per_track} sectors/track")

    def run_list_files(self) -> None:
        if not self._require_image():
            return
        assert self.current_path is not None
        layout = self._selected_layout() or None
        encoding = self._selected_encoding()
        self._run_job("extract --list", lambda: services.list_files(self.current_path, layout, encoding), self._show_files)

    def _show_files(self, entries: object) -> None:
        self.files_table.setRowCount(len(entries))
        for row, entry in enumerate(entries):
            self.files_table.setItem(row, 0, QTableWidgetItem(entry.name))
            self.files_table.setItem(row, 1, QTableWidgetItem(entry.kind))
            self.files_table.setItem(row, 2, QTableWidgetItem(str(entry.size)))
        self._append_log(f"Listed {len(entries)} filesystem entries")

    def run_info(self) -> None:
        self._run_cli(["info", str(self.current_path)] if self._require_image() else [])

    def run_sectors(self) -> None:
        if not self._require_image():
            return
        self._run_cli(
            [
                "sectors",
                str(self.current_path),
                "--track",
                self.track_input.text(),
                "--head",
                self.head_input.text(),
                "--encoding",
                self._selected_encoding(),
            ]
        )

    def run_dump(self) -> None:
        if not self._require_image():
            return
        layout = self._selected_layout()
        if not layout:
            self._warn("Choose a layout before dumping a sector.")
            return
        self._run_cli(
            [
                "dump",
                str(self.current_path),
                "--layout",
                layout,
                "--track",
                self.track_input.text(),
                "--side",
                self.head_input.text(),
                "--sector",
                self.sector_input.text(),
            ]
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
        file_path = self.file_path_input.text().strip()
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
        self._append_log(f"exit {result.returncode}")
        if result.stdout:
            self._append_log(result.stdout.rstrip())
        if result.stderr:
            self._append_log(result.stderr.rstrip())

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
