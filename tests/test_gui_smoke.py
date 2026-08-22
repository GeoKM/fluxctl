import os
from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("PySide6")

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEvent, QItemSelectionModel, QTimer, Qt
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QApplication, QAbstractItemView, QFileDialog, QMessageBox

from fluxctl import studio_services as services
from fluxctl.gui import DiskMapWidget, FluxctlStudio, Job
from fluxctl.reports.map import DiskMap, SectorMapEntry


FIXTURE_IMG = Path("tests/fixtures/3.5inch/IBM/IBM-Generic-DSDD-MFM-IBMPC-720K.img").resolve()


def _app() -> QApplication:
    app = QApplication.instance()
    return app if app is not None else QApplication([])


@pytest.fixture(autouse=True)
def _qt_app() -> QApplication:
    """Ensure every GUI smoke test has a QApplication before constructing widgets."""

    return _app()


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
    _wait_until(app, lambda: window.current_summary is not None and window.map_widget.disk_map is not None)
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


def test_disk_map_widget_labels_cbm_logical_tracks_without_physical_offset() -> None:
    physical = DiskMap([["good"]], 1, 1, track_ids=[(0, 0)])
    cbm_logical = DiskMap([["bam_system"]], 1, 1, track_ids=[(18, 0)], address_style="cbm_logical")

    assert DiskMapWidget.track_label(0, physical) == "T01"
    assert DiskMapWidget.track_label(18, cbm_logical) == "T18"


def test_disk_map_widget_wraps_large_cbm_bam_grids_into_readable_panes() -> None:
    disk_map = DiskMap(
        [["bam_free"] * 40 for _track in range(80)],
        80,
        40,
        render_style="grid",
        track_ids=[(track, 0) for track in range(1, 81)],
        address_style="cbm_logical",
    )
    groups = DiskMapWidget.head_groups(disk_map)
    panes = DiskMapWidget.grid_panes(groups, disk_map)

    assert [title for _head, title, _rows in panes] == ["Head 0 T01-T40", "Head 0 T41-T80"]
    assert [len(rows) for _head, _title, rows in panes] == [40, 40]


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
    widget.set_disk_map(DiskMap([["good"]], 1, 1, highlighted_sectors={(0, 0, 1)}))
    assert widget.legend_items()[-1] == ("selected_file", "Selected file")


def test_studio_defaults_scp_conversion_to_layout_appropriate_exporter() -> None:
    window = FluxctlStudio()

    assert window._default_exporter_for_image("scp", "commodore_gcr_1541_170k", "gcr") == "d64"
    assert window._default_exporter_for_image("scp", "commodore_gcr_1571_341k", "gcr") == "d71"
    assert window._default_exporter_for_image("scp", "commodore_mfm_1581_800k", "mfm") == "d81"
    assert window._default_exporter_for_image("d71", "commodore_gcr_1571_341k", "gcr") == "d71"
    assert window._default_exporter_for_image("d81", "commodore_mfm_1581_800k", "mfm") == "raw"
    assert window._default_exporter_for_image("scp", "ibm_mfm_720k", "mfm") == "raw"
    assert ("d71", "Commodore 1571 image (.d71)") in window._exporter_choices_for_image(
        "scp", "commodore_gcr_1571_341k", "gcr"
    )
    assert ("d81", "Commodore 1581 image (.d81)") in window._exporter_choices_for_image(
        "scp", "commodore_mfm_1581_800k", "mfm"
    )
    assert window._default_exporter_for_image(
        "woz", "apple2_gcr_nofs_140_140k", "apple2_gcr"
    ) == "po"
    assert window._exporter_choices_for_image(
        "woz", "apple2_gcr_nofs_140_140k", "apple2_gcr"
    ) == [
        ("po", "Apple ProDOS-order sector image (.po)"),
        ("do", "Apple DOS-order sector image (.do)"),
    ]

    window.close()


def test_convert_dialog_defaults_output_next_to_source(monkeypatch, tmp_path) -> None:
    window = FluxctlStudio()
    source = tmp_path / "disk.scp"
    source.write_bytes(b"")
    captured: dict[str, object] = {}
    window.current_path = source

    monkeypatch.setattr(window, "_choose_convert_exporter", lambda *_args: "raw")

    def fake_save(_parent, _title, default_name, _filter):
        captured["default_name"] = default_name
        return ("converted.img", "")

    monkeypatch.setattr(QFileDialog, "getSaveFileName", fake_save)
    monkeypatch.setattr(window, "_run_cli", lambda args: captured.setdefault("args", args))

    window.convert_dialog()

    assert captured["default_name"] == str(tmp_path / "disk-converted.img")
    assert captured["args"][5] == str(tmp_path / "converted.img")
    window.close()


def test_convert_dialog_can_choose_raw_for_amiga_scp(monkeypatch, tmp_path) -> None:
    window = FluxctlStudio()
    source = tmp_path / "amiga.scp"
    source.write_bytes(b"")
    captured: dict[str, object] = {}
    window.current_path = source
    window.current_summary = services.ImageSummary(
        path=str(source),
        size=0,
        kind="scp",
        layout_id="amiga_mfm_880k",
        encoding="mfm",
        filesystem="amiga_ffs",
        confidence=1.0,
        evidence=[],
    )

    monkeypatch.setattr(window, "_choose_convert_exporter", lambda *_args: "raw")
    monkeypatch.setattr(
        QFileDialog,
        "getSaveFileName",
        lambda _parent, _title, default_name, _filter: (default_name, ""),
    )
    monkeypatch.setattr(window, "_run_cli", lambda args: captured.setdefault("args", args))

    window.convert_dialog()

    assert captured["args"] == [
        "convert",
        str(source),
        "--to",
        "raw",
        "--out",
        str(tmp_path / "amiga-converted.img"),
        "--layout",
        "amiga_mfm_880k",
        "--encoding",
        "mfm",
    ]
    window.close()


def test_convert_dialog_warns_for_amiga_imd_target(monkeypatch, tmp_path) -> None:
    window = FluxctlStudio()
    source = tmp_path / "amiga.scp"
    source.write_bytes(b"")
    captured: dict[str, object] = {}
    warnings: list[str] = []
    window.current_path = source
    window.current_summary = services.ImageSummary(
        path=str(source),
        size=0,
        kind="scp",
        layout_id="amiga_mfm_880k",
        encoding="mfm",
        filesystem="amiga_ffs",
        confidence=1.0,
        evidence=[],
    )

    monkeypatch.setattr(window, "_choose_convert_exporter", lambda *_args: "imd")
    monkeypatch.setattr(window, "_warn", lambda message: warnings.append(message))
    monkeypatch.setattr(
        QFileDialog,
        "getSaveFileName",
        lambda _parent, _title, default_name, _filter: (default_name, ""),
    )
    monkeypatch.setattr(window, "_run_cli", lambda args: captured.setdefault("args", args))

    window.convert_dialog()

    assert warnings == [
        "IMD will store decoded Amiga sectors only. It will not preserve Amiga physical track "
        "encoding. Use ADF for native Amiga images or SCP for preservation."
    ]
    assert captured["args"][3] == "imd"
    assert captured["args"][5] == str(tmp_path / "amiga-converted.imd")
    window.close()


def test_roundtrip_dialog_runs_cli_with_selected_options(monkeypatch, tmp_path) -> None:
    window = FluxctlStudio()
    source = tmp_path / "disk.adf"
    source.write_bytes(b"")
    captured: dict[str, object] = {}
    window.current_path = source
    window.current_summary = services.ImageSummary(
        path=str(source),
        size=0,
        kind="adf",
        layout_id="amiga_mfm_880k",
        encoding="mfm",
        filesystem="amiga_ffs",
        confidence=1.0,
        evidence=[],
    )
    monkeypatch.setattr(services, "list_files", lambda *_args, **_kwargs: [])
    window._update_advanced_context()

    monkeypatch.setattr(
        window,
        "_roundtrip_options_dialog",
        lambda _default_to, _default_back: {
            "to": "raw",
            "back_to": "adf",
            "work_dir": tmp_path / "roundtrip-work",
            "json_out": Path("disk-roundtrip.json"),
        },
    )
    monkeypatch.setattr(window, "_run_cli", lambda args: captured.setdefault("args", args))

    window.roundtrip_dialog()

    assert captured["args"] == [
        "roundtrip",
        str(source),
        "--to",
        "raw",
        "--back-to",
        "adf",
        "--work-dir",
        str(tmp_path / "roundtrip-work"),
        "--json-out",
        str(tmp_path / "disk-roundtrip.json"),
        "--layout",
        "amiga_mfm_880k",
        "--encoding",
        "mfm",
    ]
    window.close()


def test_disk_map_widget_detects_highlighted_sector() -> None:
    widget = DiskMapWidget()
    widget.set_disk_map(
        DiskMap(
            [["good", "good"]],
            1,
            2,
            track_ids=[(73, 1)],
            sector_details=[
                [
                    SectorMapEntry(4, "good", 512, True, 1.0),
                    SectorMapEntry(5, "good", 512, True, 1.0),
                ]
            ],
            highlighted_sectors={(73, 1, 5)},
        )
    )

    assert not widget._sector_is_highlighted(0, 0)
    assert widget._sector_is_highlighted(0, 1)


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


def test_left_panel_can_create_blank_image(monkeypatch, tmp_path) -> None:
    window = FluxctlStudio()
    output = tmp_path / "new-disk.img"
    captured: dict[str, object] = {}

    def run_immediate(label, fn, done):
        captured["label"] = label
        done(fn())

    def fake_save(_parent, _title, default_name, _filter):
        captured["default_name"] = default_name
        return ("new-disk.img", "")

    monkeypatch.setattr(window, "_blank_image_default_directory", lambda: tmp_path)
    monkeypatch.setattr(QFileDialog, "getSaveFileName", fake_save)
    monkeypatch.setattr(window, "_run_job", run_immediate)
    monkeypatch.setattr(window, "run_probe", lambda: captured.setdefault("probed", True))
    window.blank_image_combo.setCurrentIndex(
        window.blank_image_combo.findData("fat12_720k")
    )

    window.create_blank_image_dialog()

    assert output.exists()
    assert output.stat().st_size == 737280
    assert window.current_path == output
    assert captured["default_name"] == str(tmp_path / "blank-fat12_720k.img")
    assert captured["probed"] is True
    assert "create blank" in str(captured["label"])
    window.close()


def test_left_panel_confirms_blank_image_overwrite(monkeypatch, tmp_path) -> None:
    window = FluxctlStudio()
    output = tmp_path / "existing.img"
    output.write_bytes(b"existing")
    captured: dict[str, object] = {}

    def run_immediate(label, fn, done):
        captured["label"] = label
        done(fn())

    monkeypatch.setattr(window, "_blank_image_default_directory", lambda: tmp_path)
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *_args, **_kwargs: ("existing.img", ""))
    monkeypatch.setattr(QMessageBox, "question", lambda *_args, **_kwargs: QMessageBox.Yes)
    monkeypatch.setattr(window, "_run_job", run_immediate)
    monkeypatch.setattr(window, "run_probe", lambda: captured.setdefault("probed", True))
    window.blank_image_combo.setCurrentIndex(
        window.blank_image_combo.findData("fat12_180k")
    )

    window.create_blank_image_dialog()

    assert output.stat().st_size == 184320
    assert output.read_bytes() != b"existing"
    assert captured["probed"] is True
    assert "create blank" in str(captured["label"])
    window.close()


def test_left_panel_disables_greaseweazle_read_when_missing(monkeypatch) -> None:
    monkeypatch.setattr(
        services,
        "greaseweazle_status",
        lambda: services.GreaseweazleStatus(False, "", "gw missing", "install greaseweazle"),
    )
    window = FluxctlStudio()

    assert not window.greaseweazle_read_button.isEnabled()
    assert "missing" in window.greaseweazle_status_label.text().lower()

    window.close()


def test_left_panel_runs_greaseweazle_read_to_scp(monkeypatch, tmp_path) -> None:
    captured = {}
    monkeypatch.setattr(
        services,
        "greaseweazle_status",
        lambda: services.GreaseweazleStatus(True, str(tmp_path / "gw"), "gw available"),
    )
    monkeypatch.setattr(
        services,
        "greaseweazle_formats",
        lambda: [services.GreaseweazleFormat("ibm.1440", "ibm.1440")],
    )
    window = FluxctlStudio()
    window.greaseweazle_format_combo.setCurrentIndex(window.greaseweazle_format_combo.findData("ibm.1440"))
    window.greaseweazle_revs_input.setText("2")
    window.greaseweazle_tracks_input.setText("c=0-39:h=0-1")

    def run_immediate(label, fn, done):
        captured["label"] = label
        done(fn())

    def fake_read(output, **kwargs):
        captured["output"] = output
        captured["kwargs"] = kwargs
        return SimpleNamespace(
            path=str(output),
            command=["gw", "read", "--raw", str(output)],
            command_display=f"gw read --raw {output}",
            stdout="read ok",
            stderr="",
        )

    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *_args, **_kwargs: (str(tmp_path / "capture.scp"), ""))
    monkeypatch.setattr(services, "read_disk_with_greaseweazle", fake_read)
    monkeypatch.setattr(window, "_run_job", run_immediate)
    monkeypatch.setattr(window, "run_probe", lambda: captured.setdefault("probed", True))

    window.read_disk_with_greaseweazle_dialog()

    assert captured["label"] == "greaseweazle read A"
    assert captured["output"] == tmp_path / "capture.scp"
    assert captured["kwargs"]["drive"] == "A"
    assert captured["kwargs"]["gw_format"] == "ibm.1440"
    assert captured["kwargs"]["tracks"] == "c=0-39:h=0-1"
    assert captured["kwargs"]["revs"] == 2
    assert captured["probed"] is True
    assert window.current_path == tmp_path / "capture.scp"
    assert window.main_tabs.currentIndex() == window.jobs_tab_index

    window.close()


def test_studio_uses_five_top_level_workflow_tabs() -> None:
    window = FluxctlStudio()

    assert [window.main_tabs.tabText(index) for index in range(window.main_tabs.count())] == [
        "Disk && Imaging",
        "Files && Directories",
        "HEX && ASCII",
        "Advanced",
        "Jobs && Logs",
    ]
    assert window.main_tabs.currentIndex() == window.disk_tab_index
    assert not window.main_tabs.isTabEnabled(window.advanced_tab_index)
    assert window.main_tabs.isTabEnabled(window.jobs_tab_index)
    assert not window.map_canvas_panel.isHidden()

    window.mode.setCurrentIndex(1)

    assert window.main_tabs.isTabEnabled(window.advanced_tab_index)
    assert window.hex_mode_stack.currentWidget() == window.advanced_hex_panel

    window.main_tabs.setCurrentIndex(window.advanced_tab_index)
    window.mode.setCurrentIndex(0)

    assert window.main_tabs.currentIndex() == window.disk_tab_index
    assert window.hex_mode_stack.currentWidget() == window.hex_panel
    window.close()


def test_studio_styles_disabled_actions_distinctly() -> None:
    window = FluxctlStudio()
    style = window.styleSheet()

    assert "QPushButton:disabled" in style
    assert "color: #697386" in style
    assert "QComboBox:disabled" in style
    assert "QToolTip" in style
    window.close()


def test_studio_exposes_job_progress_and_cancellation_controls() -> None:
    window = FluxctlStudio()

    assert window.job_progress.minimum() == 0
    assert window.job_progress.maximum() == 0
    assert window.job_cancel_button.text() == "Cancel Job"
    assert window.job_status_label.text().startswith("Running doctor") or window.job_status_label.text() == "No active jobs"

    window.close()


def test_job_cancellation_discards_result() -> None:
    events: list[str] = []
    finished: list[object] = []
    job = Job(lambda: events.append("finished"))
    job.signals.cancelled.connect(lambda: events.append("cancelled"))
    job.signals.finished.connect(finished.append)

    job.cancel()
    job.run()

    assert events == ["finished", "cancelled"]
    assert finished == []


def test_stale_job_result_is_discarded() -> None:
    window = FluxctlStudio()
    applied: list[object] = []
    stale_job = Job(lambda: None)
    window._job_generation = 2

    window._finish_job(stale_job, 1, "old probe", object(), applied.append)

    assert applied == []
    assert "Discarded stale result from old probe." in window.log.toPlainText()
    window.close()


def test_simple_mode_uses_sidebar_summary_and_larger_file_area() -> None:
    window = FluxctlStudio()

    assert window.sidebar.minimumWidth() >= 340
    assert window.summary_labels["layout"].text() == "-"
    assert window.files_table.minimumHeight() >= 340
    assert window.main_tabs.widget(window.files_tab_index) == window.file_panel
    assert window.main_tabs.widget(window.hex_tab_index) == window.hex_page
    assert window.main_tabs.widget(window.jobs_tab_index) == window.jobs_page
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

    assert window.file_replace_button.isEnabled()
    assert window.file_delete_button.isEnabled()
    assert window.file_import_button.isEnabled()
    assert not window.directory_import_button.isEnabled()
    assert not window.directory_create_button.isEnabled()
    assert "root-level PRG import" in window.file_import_button.toolTip()
    assert "replace, and delete" in window.file_replace_button.toolTip()

    window.current_path = Path("/tmp/example.d81")
    window.current_summary = services.ImageSummary(
        path="/tmp/example.d81",
        size=819200,
        kind="d81",
        layout_id="commodore_mfm_1581_800k",
        encoding="mfm",
        filesystem="cbm_dos_1581",
        confidence=1.0,
        evidence=[],
    )
    window._update_filesystem_write_actions()

    assert window.file_replace_button.isEnabled()
    assert window.file_delete_button.isEnabled()
    assert window.file_import_button.isEnabled()
    assert window.directory_import_button.isEnabled()
    assert window.directory_create_button.isEnabled()
    assert "1581 .d81 PRG import" in window.file_import_button.toolTip()

    window.current_path = Path("/tmp/example.adf")
    window.current_summary = services.ImageSummary(
        path="/tmp/example.adf",
        size=901120,
        kind="adf",
        layout_id="amiga_mfm_880k",
        encoding="mfm",
        filesystem="amiga_ffs",
        confidence=1.0,
        evidence=[],
    )
    window._update_filesystem_write_actions()

    assert not window.file_import_button.isEnabled()
    assert "modelled CP/M .img" in window.file_import_button.toolTip()

    window.current_path = Path("/tmp/example.img")
    window.current_summary = services.ImageSummary(
        path="/tmp/example.img",
        size=204800,
        kind="img",
        layout_id="kaypro_mfm_ssdd_40_200k",
        encoding="mfm",
        filesystem="cpm",
        confidence=1.0,
        evidence=[],
    )
    window._update_filesystem_write_actions()

    assert not window.file_replace_button.isEnabled()
    assert window.file_delete_button.isEnabled()
    assert window.file_import_button.isEnabled()
    assert not window.directory_import_button.isEnabled()
    assert not window.directory_create_button.isEnabled()
    assert "modelled CP/M flat .img" in window.file_import_button.toolTip()

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


def test_file_selection_highlights_file_allocation_on_map(monkeypatch) -> None:
    _app()
    window = FluxctlStudio()
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
    window.map_widget.set_disk_map(
        DiskMap(
            [["good", "good"]],
            1,
            2,
            track_ids=[(73, 1)],
            sector_details=[
                [
                    SectorMapEntry(4, "good", 512, True, 1.0),
                    SectorMapEntry(5, "good", 512, True, 1.0),
                ]
            ],
        )
    )
    monkeypatch.setattr(
        services,
        "file_allocation_for_image",
        lambda *_args: services.FileAllocationView("/AUTOEXEC.BAT", {(73, 1, 4), (73, 1, 5)}),
    )
    window._show_files(
        [
            services.FileEntryView("AUTOEXEC.BAT", "file", 39, "/AUTOEXEC.BAT", False),
            services.FileEntryView("TOOLS", "<DIR>", 0, "/TOOLS", True),
        ]
    )

    window.files_table.setCurrentCell(0, 0)

    assert window.map_widget.disk_map.highlighted_sectors == {(73, 1, 4), (73, 1, 5)}

    window.files_table.setCurrentCell(1, 0)

    assert window.map_widget.disk_map.highlighted_sectors == set()
    window.close()


def test_file_selection_uses_logical_allocation_on_cbm_bam_map(monkeypatch) -> None:
    _app()
    window = FluxctlStudio()
    window.current_path = FIXTURE_IMG
    window.current_summary = services.ImageSummary(
        path=str(FIXTURE_IMG),
        size=FIXTURE_IMG.stat().st_size,
        kind="img",
        layout_id="commodore_gcr_1541_170k",
        encoding="gcr",
        filesystem="cbm_dos",
        confidence=1.0,
        evidence=[],
    )
    window.map_widget.set_disk_map(
        DiskMap(
            [["bam_file"]],
            1,
            1,
            render_style="grid",
            track_ids=[(18, 0)],
            sector_details=[[SectorMapEntry(6, "bam_file", 256, True, 1.0)]],
            address_style="cbm_logical",
        )
    )
    monkeypatch.setattr(
        services,
        "file_allocation_for_image",
        lambda *_args: services.FileAllocationView(
            "/HELLO",
            {(17, 0, 6)},
            logical_sectors={(18, 0, 6)},
        ),
    )
    window._show_files([services.FileEntryView("HELLO", "file", 39, "/HELLO", False)])

    window.files_table.setCurrentCell(0, 0)

    assert window.map_widget.disk_map.highlighted_sectors == {(18, 0, 6)}
    window.close()


def test_file_panel_selected_file_hex_updates_hex_tab() -> None:
    window = FluxctlStudio()
    entries = [services.FileEntryView("README.TXT", "file", 42, "/README.TXT", False)]
    window._show_files(entries)
    window.files_table.setCurrentCell(0, 0)
    window.main_tabs.setCurrentIndex(window.files_tab_index)

    window._show_hex_dump(services.HexDumpView("File /README.TXT", 5, services.format_hex_dump(b"HELLO")))

    assert window._selected_file_entry_path() == ("/README.TXT", False)
    assert window.main_tabs.currentIndex() == window.hex_tab_index
    assert window.hex_mode_stack.currentWidget() == window.hex_panel
    assert "File /README.TXT" in window.hex_title_label.text()
    assert "48 45 4C 4C 4F" in window.hex_text.toPlainText()
    window.close()


def test_file_panel_displays_filesystem_volume_info() -> None:
    window = FluxctlStudio()
    window._show_files(
        services.FileListView(
            [services.FileEntryView("HELLO", "file", 39, "/HELLO", False)],
            "Name: TEST DISK  ID: 01  DOS: 2A",
        )
    )

    assert window.file_volume_label.text() == "Name: TEST DISK  ID: 01  DOS: 2A"
    assert window.files_table.item(0, 0).text() == "HELLO"
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


def test_advanced_command_result_updates_output_panel() -> None:
    window = FluxctlStudio()
    window.mode.setCurrentIndex(1)
    window._show_advanced_hex_dump(services.HexDumpView("Sector T0 H0 S1", 1, "00000000  00  |.|"))

    window._show_command_result(
        services.CommandResult(["info", "disk.img"], 0, "Size: 737280 bytes\nFilesystem: fat12\n", "")
    )

    assert window.advanced_detail_stack.currentWidget() == window.advanced_output
    assert "$ info disk.img" in window.advanced_output.toPlainText()
    assert "Filesystem: fat12" in window.advanced_output.toPlainText()
    assert "Filesystem: fat12" in window.log.toPlainText()
    assert window.main_tabs.currentIndex() == window.advanced_tab_index
    window.close()


def test_advanced_sector_report_updates_output_panel() -> None:
    window = FluxctlStudio()
    window.mode.setCurrentIndex(1)
    window._show_advanced_hex_dump(services.HexDumpView("Sector T0 H0 S1", 1, "00000000  00  |.|"))

    window._show_text_view(services.TextView("Sectors T0 H0", "Track 0 head 0: 9 sectors"))

    assert window.advanced_detail_stack.currentWidget() == window.advanced_output
    assert window.activity_label.text() == "Loaded Sectors T0 H0."
    assert "Track 0 head 0" in window.advanced_output.toPlainText()
    assert "Track 0 head 0" in window.log.toPlainText()
    assert window.main_tabs.currentIndex() == window.advanced_tab_index
    window.close()


def test_advanced_dump_hex_updates_hex_panel() -> None:
    window = FluxctlStudio()
    window.mode.setCurrentIndex(1)

    window._show_advanced_hex_dump(services.HexDumpView("Sector T0 H0 S1", 5, services.format_hex_dump(b"HELLO")))

    assert window.main_tabs.currentIndex() == window.hex_tab_index
    assert window.hex_mode_stack.currentWidget() == window.advanced_hex_panel
    assert "Sector T0 H0 S1" in window.advanced_hex_title_label.text()
    assert "48 45 4C 4C 4F" in window.advanced_hex_text.toPlainText()
    assert "Loaded hex view for Sector T0 H0 S1" in window.activity_label.text()
    window.close()


def test_advanced_hex_editor_synchronizes_hex_edits_to_ascii() -> None:
    window = FluxctlStudio()
    window._show_advanced_hex_dump(
        services.HexDumpView("File /HELLO.TXT", 5, services.format_hex_dump(b"HELLO"), data=b"HELLO")
    )
    window.advanced_hex_text.setPlainText(services.format_hex_dump(b"JELLO"))
    cursor = window.advanced_hex_text.textCursor()
    cursor.setPosition(11)
    window.advanced_hex_text.setTextCursor(cursor)

    QApplication.sendEvent(
        window.advanced_hex_text,
        QKeyEvent(QEvent.KeyPress, Qt.Key_Return, Qt.NoModifier),
    )

    assert "4A 45 4C 4C 4F" in window.advanced_hex_text.toPlainText()
    assert "|JELLO|" in window.advanced_hex_text.toPlainText()
    window.close()


def test_advanced_hex_editor_synchronizes_ascii_edits_to_hex() -> None:
    window = FluxctlStudio()
    window._show_advanced_hex_dump(
        services.HexDumpView("File /HELLO.TXT", 5, services.format_hex_dump(b"H\x00LLO"), data=b"H\x00LLO")
    )
    window.advanced_hex_text.setPlainText(services.format_hex_dump(b"H\x00LLO").replace("|H.LLO|", "|HALLO|"))
    line = window.advanced_hex_text.toPlainText()
    cursor = window.advanced_hex_text.textCursor()
    cursor.setPosition(line.find("|") + 2)
    window.advanced_hex_text.setTextCursor(cursor)

    window.advanced_hex_text.syncRequested.emit()

    assert "48 41 4C 4C 4F" in window.advanced_hex_text.toPlainText()
    assert "|HALLO|" in window.advanced_hex_text.toPlainText()
    window.close()


def test_simple_hex_panel_stays_read_only() -> None:
    window = FluxctlStudio()

    assert window.hex_text.isReadOnly()
    window.close()


def test_advanced_hex_panel_is_editable_and_can_save_file_copy(monkeypatch, tmp_path) -> None:
    window = FluxctlStudio()
    output = tmp_path / "edited.img"
    captured: dict[str, object] = {}
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
    window._update_advanced_context()
    window._show_advanced_hex_dump(
        services.HexDumpView(
            "File /AUTOEXEC.BAT",
            5,
            services.format_hex_dump(b"HELLO"),
            data=b"HELLO",
            source_kind="file",
            file_path="/AUTOEXEC.BAT",
        )
    )
    window.advanced_hex_text.setPlainText(services.format_hex_dump(b"JELLO"))

    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *_args, **_kwargs: (str(output), ""))
    monkeypatch.setattr(QMessageBox, "question", lambda *_args, **_kwargs: QMessageBox.Yes)
    monkeypatch.setattr(window, "_run_job", lambda _label, fn, done: done(fn()))

    def fake_replace(path, layout, encoding, fs_path, replacement, out):
        captured.update(
            {
                "path": path,
                "layout": layout,
                "encoding": encoding,
                "fs_path": fs_path,
                "replacement": replacement,
                "out": out,
            }
        )
        return services.HexEditResult(str(out), fs_path, len(replacement), "file")

    monkeypatch.setattr(services, "replace_file_bytes_with_copy", fake_replace)

    window.save_advanced_hex_edit()

    assert not window.advanced_hex_text.isReadOnly()
    assert captured["replacement"] == b"JELLO"
    assert captured["fs_path"] == "/AUTOEXEC.BAT"
    assert captured["out"] == output
    assert "Saved edited file hex" in window.activity_label.text()
    window.close()


def test_advanced_panel_starts_blank_until_image_is_loaded() -> None:
    window = FluxctlStudio()

    assert window.layout_combo.currentIndex() == -1
    assert window.encoding_combo.currentIndex() == -1
    assert window.export_combo.currentIndex() == -1
    assert window.dump_mode_combo.currentIndex() == -1
    assert window.track_input.text() == ""
    assert window.file_path_input.currentText() == ""
    assert not window.track_input.isEnabled()
    assert all(not button.isEnabled() for button in window.advanced_image_buttons)
    window.close()


def test_advanced_panel_shows_doctor_summary_without_image() -> None:
    window = FluxctlStudio()

    window._show_doctor(
        {
            "version": "0.3.3",
            "overall": "ok",
            "checks": [{"name": "layouts", "status": "ok", "detail": "114 loaded", "suggestion": ""}],
        }
    )

    assert "Fluxctl Doctor: ok" in window.advanced_output.toPlainText()
    assert "OK: layouts - 114 loaded" in window.advanced_output.toPlainText()
    window.close()


def test_advanced_panel_populates_from_loaded_image_summary() -> None:
    window = FluxctlStudio()
    window.mode.setCurrentIndex(1)
    summary = services.ImageSummary(
        path=str(FIXTURE_IMG),
        size=FIXTURE_IMG.stat().st_size,
        kind="img",
        layout_id="ibm_mfm_720k",
        encoding="mfm",
        filesystem="fat12",
        confidence=1.0,
        evidence=["boot_sector=valid"],
    )

    window.current_path = FIXTURE_IMG
    window.current_summary = summary
    window._update_advanced_context()

    assert window.layout_combo.currentData() == "ibm_mfm_720k"
    assert window.encoding_combo.currentText() == "mfm"
    assert window.export_combo.currentText() == "raw"
    assert window.dump_mode_combo.currentData() == "sector"
    assert window.track_input.text() == "0"
    assert window.head_input.text() == "0"
    assert window.sector_input.text() == "1"
    assert window.file_path_input.currentText() == "/"
    assert window.track_input.isEnabled()
    assert all(button.isEnabled() for button in window.advanced_image_buttons)
    assert "Loaded Image" in window.advanced_output.toPlainText()
    assert "Filesystem: fat12" in window.advanced_output.toPlainText()
    window.close()


def test_advanced_dump_file_mode_loads_file_hex(monkeypatch) -> None:
    window = FluxctlStudio()
    window.mode.setCurrentIndex(1)
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
    window._update_advanced_context()
    window.dump_mode_combo.setCurrentIndex(window.dump_mode_combo.findData("file"))
    window.file_path_input.setEditText("/AUTOEXEC.BAT")

    monkeypatch.setattr(
        services,
        "file_hex_dump",
        lambda *_args, **_kwargs: services.HexDumpView("File /AUTOEXEC.BAT", 5, services.format_hex_dump(b"HELLO")),
    )
    monkeypatch.setattr(window, "_run_job", lambda _label, fn, done: done(fn()))

    window.run_dump()

    assert "File /AUTOEXEC.BAT" in window.advanced_hex_title_label.text()
    assert "48 45 4C 4C 4F" in window.advanced_hex_text.toPlainText()
    assert window.main_tabs.currentIndex() == window.hex_tab_index
    assert window.hex_mode_stack.currentWidget() == window.advanced_hex_panel
    window.close()


def test_advanced_dump_file_mode_rejects_selected_directory(monkeypatch) -> None:
    window = FluxctlStudio()
    warnings: list[str] = []
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
    window._update_advanced_context()
    window.dump_mode_combo.setCurrentIndex(window.dump_mode_combo.findData("file"))
    window.file_path_input.addItem("TOOLS/", {"path": "/TOOLS", "is_dir": True})
    window.file_path_input.setEditText("/TOOLS")
    monkeypatch.setattr(window, "_warn", lambda message: warnings.append(message))
    monkeypatch.setattr(
        services,
        "file_hex_dump",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("file dump should not run")),
    )

    window.run_dump()

    assert warnings == ["File Dump requires a file. Choose a file, not a directory."]
    window.close()


def test_advanced_file_path_dropdown_lists_and_selects_files(monkeypatch) -> None:
    window = FluxctlStudio()
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
    monkeypatch.setattr(
        services,
        "list_files",
        lambda *_args: [
            services.FileEntryView("TOOLS", "<DIR>", 0, "/TOOLS", True),
            services.FileEntryView("AUTOEXEC.BAT", "file", 39, "/AUTOEXEC.BAT", False),
        ],
    )

    window._load_advanced_file_path_options("/")

    file_index = window.file_path_input.findText("AUTOEXEC.BAT")
    assert file_index >= 0
    window._advanced_file_path_activated(file_index)

    assert window.file_path_input.currentText() == "/AUTOEXEC.BAT"
    window.close()


def test_advanced_file_path_dropdown_traverses_directories(monkeypatch) -> None:
    window = FluxctlStudio()
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

    def fake_list_files(_path, _layout, _encoding, directory):
        if directory == "/":
            return [services.FileEntryView("TOOLS", "<DIR>", 0, "/TOOLS", True)]
        return [services.FileEntryView("README.TXT", "file", 5, "/TOOLS/README.TXT", False)]

    monkeypatch.setattr(services, "list_files", fake_list_files)
    window._load_advanced_file_path_options("/")

    directory_index = window.file_path_input.findText("TOOLS/")
    assert directory_index >= 0
    window._advanced_file_path_activated(directory_index)

    assert window.advanced_file_browser_path == "/TOOLS"
    assert window.file_path_input.currentText() == "/TOOLS"
    assert window.file_path_input.findText("README.TXT") >= 0
    assert window.file_path_input.findText("Up ..") >= 0
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
            (str(window.hex_track_input.value()), str(window.hex_head_input.value()), str(window.hex_sector_input.value()))
        ),
    )

    window.load_sector_hex_from_map(7, 1, 12)

    assert loaded == [("7", "1", "12")]
    window.close()


def test_sector_hex_inputs_are_stepper_controls() -> None:
    window = FluxctlStudio()

    assert window.hex_track_input.minimum() == 0
    assert window.hex_track_input.maximum() == 999
    assert window.hex_head_input.maximum() == 1
    assert window.hex_sector_input.minimum() == 0

    window.hex_track_input.stepUp()
    window.hex_head_input.stepUp()
    window.hex_sector_input.stepDown()

    assert window.hex_track_input.value() == 1
    assert window.hex_head_input.value() == 1
    assert window.hex_sector_input.value() == 0
    assert window.hex_track_input.minimumHeight() >= 52
    assert window.hex_track_input.objectName() == "hexChsInput"
    assert window.hex_sector_button.minimumHeight() >= 52
    window.close()


def test_sector_hex_stepper_refreshes_immediately(monkeypatch) -> None:
    window = FluxctlStudio()
    window.current_path = Path("/tmp/example.img")
    loaded: list[tuple[int, int, int]] = []
    monkeypatch.setattr(
        window,
        "view_sector_hex",
        lambda: loaded.append(
            (
                window.hex_track_input.value(),
                window.hex_head_input.value(),
                window.hex_sector_input.value(),
            )
        ),
    )

    window.hex_sector_input.setValue(2)

    assert loaded == [(0, 0, 2)]
    window.close()


def test_cbm_sector_hex_input_uses_logical_track_numbers(monkeypatch) -> None:
    window = FluxctlStudio()
    window.mode.setCurrentIndex(0)
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
    calls: list[tuple[int, int, int]] = []

    def fake_sector_hex(_path, _layout, _encoding, track, head, sector):
        calls.append((track, head, sector))
        return services.HexDumpView(f"Sector T{track} H{head} S{sector}", 256, "00")

    def run_immediate(_label, fn, done):
        done(fn())

    monkeypatch.setattr(services, "sector_hex_dump", fake_sector_hex)
    monkeypatch.setattr(window, "_run_job", run_immediate)
    window.hex_track_input.setValue(18)
    window.hex_head_input.setValue(0)
    window.hex_sector_input.setValue(0)
    calls.clear()

    window.view_sector_hex()

    assert calls == [(17, 0, 0)]
    assert "Sector CBM T18 H0 S0" in window.hex_title_label.text()
    window.close()


def test_cbm_map_click_shows_logical_track_numbers(monkeypatch) -> None:
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
    loaded: list[tuple[str, str, str]] = []
    monkeypatch.setattr(
        window,
        "view_sector_hex",
        lambda: loaded.append(
            (str(window.hex_track_input.value()), str(window.hex_head_input.value()), str(window.hex_sector_input.value()))
        ),
    )

    window.load_sector_hex_from_map(17, 0, 0)

    assert loaded == [("18", "0", "0")]
    window.close()


def test_cbm_bam_map_click_keeps_logical_track_numbers(monkeypatch) -> None:
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
    window.map_widget.set_disk_map(
        DiskMap(
            [["bam_system"]],
            1,
            1,
            render_style="grid",
            track_ids=[(18, 0)],
            sector_details=[[
                SectorMapEntry(
                    sector_id=0,
                    state="bam_system",
                    size=256,
                    crc_ok=True,
                    confidence=1.0,
                )
            ]],
            address_style="cbm_logical",
        )
    )
    loaded: list[tuple[str, str, str]] = []
    monkeypatch.setattr(
        window,
        "view_sector_hex",
        lambda: loaded.append(
            (str(window.hex_track_input.value()), str(window.hex_head_input.value()), str(window.hex_sector_input.value()))
        ),
    )

    window.load_sector_hex_from_map(18, 0, 0)

    assert loaded == [("18", "0", "0")]
    assert "Track 18" in window.map_widget.sector_detail_text(0, 0)
    window.close()
