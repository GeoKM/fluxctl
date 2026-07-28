import os
from pathlib import Path

import pytest

pytest.importorskip("PySide6")

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QItemSelectionModel, QTimer
from PySide6.QtWidgets import QApplication, QAbstractItemView

from fluxctl import studio_services as services
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
    _wait_until(app, lambda: "Rendered" in window.activity_label.text())
    assert window.current_summary is not None

    window.run_qc()
    _wait_until(app, lambda: window.activity_label.text().startswith("QC "))
    assert window.summary_labels["status"].text() in {"good", "suspect"}

    window.run_map()
    _wait_until(app, lambda: "Rendered" in window.activity_label.text())
    assert window.map_widget.disk_map is not None

    window.map_view.setCurrentIndex(1)
    _wait_until(app, lambda: "whole physical disk map" in window.activity_label.text().lower())

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


def test_disk_map_widget_exposes_colour_legend_items() -> None:
    widget = DiskMapWidget()
    assert widget.legend_items() == [
        ("good", "Good"),
        ("weak", "Weak"),
        ("bad", "Bad"),
        ("unused", "Unused/free"),
    ]
    widget.set_disk_map(DiskMap([["bam_file"]], 1, 1, render_style="grid"))
    assert widget.legend_items() == [
        ("bam_file", "File"),
        ("bam_system", "System"),
        ("bam_used", "Allocated"),
        ("bam_free", "Free"),
    ]


def test_opening_new_image_clears_file_panel() -> None:
    window = FluxctlStudio()
    assert window.map_view.itemData(2) == "bam"
    assert window.files_table.editTriggers() == QAbstractItemView.NoEditTriggers
    window.files_table.setRowCount(1)
    window._set_file_browser_path("/SOMEWHERE")
    window.summary_labels["filesystem"].setText("cbm_dos")

    window._clear_image_results()

    assert window.files_table.rowCount() == 0
    assert window.file_browser_path == "/"
    assert window.summary_labels["filesystem"].text() == "-"
    assert window.map_widget.disk_map is None
    assert not window.file_import_button.isEnabled()
    assert "Open and probe" in window.file_import_button.toolTip()
    window.close()


def test_fat12_write_actions_enable_only_for_supported_flat_img() -> None:
    window = FluxctlStudio()

    window.current_path = Path("/tmp/example.d64")
    window.current_summary = services.ImageSummary(
        path="/tmp/example.d64",
        size=174848,
        kind="d64",
        layout_id="commodore_gcr_1541_170k",
        encoding="gcr",
        filesystem="cbm_dos",
        confidence=1.0,
        evidence=[],
    )
    window._update_filesystem_write_actions()

    assert not window.file_replace_button.isEnabled()
    assert "FAT12 flat .img" in window.file_replace_button.toolTip()

    window.current_path = FIXTURE_IMG
    window.current_summary = services.ImageSummary(
        path=str(FIXTURE_IMG),
        size=FIXTURE_IMG.stat().st_size,
        kind="img",
        layout_id="ibm_mfm_720k",
        encoding="mfm",
        filesystem="fat12",
        confidence=1.0,
        evidence=[],
    )
    window._update_filesystem_write_actions()

    for button in [
        window.file_replace_button,
        window.file_delete_button,
        window.file_import_button,
        window.directory_import_button,
        window.directory_create_button,
    ]:
        assert button.isEnabled()
        assert "new image copy" in button.toolTip()
    window.close()


def test_file_panel_double_click_opens_directory(monkeypatch) -> None:
    window = FluxctlStudio()
    opened_paths: list[str] = []
    monkeypatch.setattr(window, "run_list_files", lambda: opened_paths.append(window.file_browser_path))
    entries = [
        services.FileEntryView("SUBDIR", "<DIR>", 0, "/SUBDIR", True),
        services.FileEntryView("README.TXT", "file", 42, "/README.TXT", False),
    ]
    window._show_files(entries)

    window.open_selected_file_entry(window.files_table.item(0, 0))

    assert window.file_browser_path == "/SUBDIR"
    assert opened_paths == ["/SUBDIR"]
    window.close()


def test_file_panel_selected_file_hex_updates_hex_tab() -> None:
    window = FluxctlStudio()
    entries = [services.FileEntryView("README.TXT", "file", 42, "/README.TXT", False)]
    window._show_files(entries)
    window.files_table.setCurrentCell(0, 0)
    window.lower_tabs.setCurrentWidget(window.file_panel)

    window._show_hex_dump(services.HexDumpView("File /README.TXT", 5, services.format_hex_dump(b"HELLO")))

    assert window._selected_file_entry_path() == ("/README.TXT", False)
    assert window.lower_tabs.currentWidget() == window.hex_panel
    assert "File /README.TXT" in window.hex_title_label.text()
    assert "48 45 4C 4C 4F" in window.hex_text.toPlainText()
    window.close()


def test_file_panel_tracks_multiple_selected_entries() -> None:
    window = FluxctlStudio()
    entries = [
        services.FileEntryView("ONE.TXT", "file", 1, "/ONE.TXT", False),
        services.FileEntryView("TWO.TXT", "file", 2, "/TWO.TXT", False),
        services.FileEntryView("DIR", "<DIR>", 0, "/DIR", True),
    ]
    window._show_files(entries)
    selection = window.files_table.selectionModel()
    selection.select(window.files_table.model().index(0, 0), QItemSelectionModel.Select | QItemSelectionModel.Rows)
    selection.select(window.files_table.model().index(2, 0), QItemSelectionModel.Select | QItemSelectionModel.Rows)

    assert window._selected_file_entries() == [("/ONE.TXT", False), ("/DIR", True)]
    window.close()


def test_file_panel_export_result_updates_activity() -> None:
    window = FluxctlStudio()

    window._show_export_result(services.ExportResult("/tmp/exported.bin", 1, 42))

    assert "Exported 1 file(s), 42 bytes" in window.activity_label.text()
    assert "exported.bin" in window.log.toPlainText()
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


def test_disk_map_widget_resolves_click_to_sector_address() -> None:
    app = _app()
    disk_map = DiskMap(
        tracks=[["good"]],
        total_tracks=1,
        max_sectors_per_track=1,
        track_ids=[(7, 1)],
        sector_details=[
            [
                SectorMapEntry(
                    sector_id=12,
                    state="good",
                    size=256,
                    crc_ok=True,
                    confidence=1.0,
                    has_data=True,
                )
            ]
        ],
    )
    widget = DiskMapWidget()
    widget.resize(300, 300)
    widget.set_disk_map(disk_map)
    widget.show()
    _wait_until(app, lambda: bool(widget._head_layouts))

    assert widget.sector_address_at(150, 140) == (7, 1, 12)
    widget.close()


def test_disk_map_widget_ignores_bam_click_for_sector_hex() -> None:
    app = _app()
    disk_map = DiskMap(
        tracks=[["bam_file"]],
        total_tracks=1,
        max_sectors_per_track=1,
        track_ids=[(7, 1)],
        sector_details=[
            [
                SectorMapEntry(
                    sector_id=12,
                    state="bam_file",
                    size=256,
                    crc_ok=True,
                    confidence=1.0,
                    has_data=True,
                )
            ]
        ],
        render_style="grid",
    )
    widget = DiskMapWidget()
    widget.resize(300, 300)
    widget.set_disk_map(disk_map)
    widget.show()
    _wait_until(app, lambda: bool(widget._head_layouts))

    assert widget.sector_address_at(54, 52) is None
    widget.close()


def test_map_sector_click_updates_sector_hex_inputs(monkeypatch) -> None:
    window = FluxctlStudio()
    loaded: list[tuple[str, str, str]] = []
    monkeypatch.setattr(
        window,
        "view_sector_hex",
        lambda: loaded.append(
            (window.hex_track_input.text(), window.hex_head_input.text(), window.hex_sector_input.text())
        ),
    )

    window.load_sector_hex_from_map(7, 1, 12)

    assert loaded == [("7", "1", "12")]
    window.close()
