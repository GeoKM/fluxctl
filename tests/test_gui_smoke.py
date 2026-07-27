import os
from pathlib import Path

import pytest

pytest.importorskip("PySide6")

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from fluxctl.gui import FluxctlStudio
from fluxctl.gui import DiskMapWidget
from fluxctl.reports.map import DiskMap, SectorMapEntry


FIXTURE_IMG = Path("tests/fixtures/3.5inch/IBM/IBM-Generic-DSDD-MFM-IBMPC-720K.img").resolve()


def _app() -> QApplication:
    app = QApplication.instance()
    return app if app is not None else QApplication([])


def _wait_until(app: QApplication, predicate, timeout_ms: int = 5000) -> None:
    deadline = timeout_ms

    def tick() -> None:
        nonlocal deadline
        if predicate() or deadline <= 0:
            app.quit()
            return
        deadline -= 50
        QTimer.singleShot(50, tick)

    QTimer.singleShot(0, tick)
    app.exec()
    assert predicate()


def test_simple_mode_buttons_update_visible_activity() -> None:
    app = _app()
    window = FluxctlStudio()
    window.current_path = FIXTURE_IMG
    window.file_label.setText(str(FIXTURE_IMG))

    window.run_probe()
    _wait_until(app, lambda: "Rendered map" in window.activity_label.text())
    assert window.current_summary is not None

    window.run_qc()
    _wait_until(app, lambda: window.activity_label.text().startswith("QC "))
    assert window.summary_labels["status"].text() in {"good", "suspect"}

    window.run_map()
    _wait_until(app, lambda: "Rendered map" in window.activity_label.text())
    assert window.map_widget.disk_map is not None

    window.run_list_files()
    _wait_until(app, lambda: window.files_table.rowCount() >= 1)
    assert window.files_table.rowCount() >= 1

    window.close()


def test_disk_map_widget_groups_double_sided_media_by_head() -> None:
    disk_map = DiskMap(
        tracks=[
            ["good", "good"],
            ["weak", "good"],
            ["bad", "good"],
            ["good", "bad"],
        ],
        total_tracks=4,
        max_sectors_per_track=2,
        track_ids=[(0, 0), (0, 1), (1, 0), (1, 1)],
    )

    groups = DiskMapWidget.head_groups(disk_map)

    assert [head for head, _rows in groups] == [0, 1]
    assert [track_id for _row_index, track_id, _sectors in groups[0][1]] == [(0, 0), (1, 0)]
    assert [track_id for _row_index, track_id, _sectors in groups[1][1]] == [(0, 1), (1, 1)]


def test_opening_new_image_clears_file_panel() -> None:
    window = FluxctlStudio()
    window.files_table.setRowCount(1)
    window.summary_labels["filesystem"].setText("cbm_dos")

    window._clear_image_results()

    assert window.files_table.rowCount() == 0
    assert window.summary_labels["filesystem"].text() == "-"
    assert window.map_widget.disk_map is None
    window.close()


def test_disk_map_widget_tooltip_includes_sector_metadata() -> None:
    disk_map = DiskMap(
        tracks=[["weak"]],
        total_tracks=1,
        max_sectors_per_track=1,
        track_ids=[(7, 1)],
        sector_details=[
            [
                SectorMapEntry(
                    sector_id=12,
                    state="weak",
                    size=256,
                    crc_ok=False,
                    confidence=0.42,
                    deleted=True,
                    has_data=True,
                )
            ]
        ],
    )
    widget = DiskMapWidget()
    widget.set_disk_map(disk_map)

    text = widget.sector_detail_text(0, 0)

    assert "Track 7  Head 1" in text
    assert "Sector ID 12" in text
    assert "CRC: bad" in text
    assert "Confidence: 0.42" in text
    assert "Deleted: yes" in text
