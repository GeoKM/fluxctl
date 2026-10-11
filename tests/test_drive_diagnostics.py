"""Lightweight checks for the optional live drive-diagnostic bridge."""
from __future__ import annotations

import pytest
import sys
from types import ModuleType, SimpleNamespace

from fluxctl.drive_diagnostics import (
    DIAGNOSTIC_PRESETS, DiagnosticOptions, parse_status, reading_levels, worker_command,
)
from fluxctl.drive_diagnostics_worker import PipeKeyboard, main


def test_diagnostic_options_cover_fork_controls() -> None:
    args = DiagnosticOptions(
        rate=500, drive="1", device="/dev/ttyUSB0", sectors=15,
        rpm=360, encoding="fm", cylinders=80, heads=1,
        double_step=True, step_delay=3000, gen_tg43=True,
    ).arguments()
    assert args == [
        "--rate", "500", "--drive", "1", "--encoding", "fm",
        "--cyls", "80", "--heads", "1", "--device", "/dev/ttyUSB0",
        "--secs", "15", "--rpm", "360", "--double-step",
        "--step-delay", "3000", "--gen-tg43",
    ]
    assert worker_command(DiagnosticOptions())[1:4] == [
        "-u", "-m", "fluxctl.drive_diagnostics_worker",
    ]


@pytest.mark.parametrize("options", [
    DiagnosticOptions(rate=0), DiagnosticOptions(sectors=0),
    DiagnosticOptions(rpm=-1), DiagnosticOptions(encoding="gcr"),
    DiagnosticOptions(drive="Z"), DiagnosticOptions(step_delay=-1),
    DiagnosticOptions(step_delay=65536),
])
def test_invalid_diagnostic_options_fail_before_start(options: DiagnosticOptions) -> None:
    with pytest.raises(ValueError):
        options.arguments()


def test_parse_live_status_and_ignore_other_output() -> None:
    line = ("Drive A: T11, H1, RPM \x1b[92m297.30\x1b[0m, "
            "\x1b[91mS7/9\x1b[0m, OT T10/S2, SEL:ON, MOT:ON, "
            "WP:H Unprot, TK0:H OFF, DEN 2:L, DC34:?")
    status = parse_status(line)
    assert status is not None
    assert status["rpm"] == "297.30"
    assert status["sectors"] == "7"
    assert status["expected"] == "9"
    assert status["off_track"] == "T10/S2"
    assert status["write_protect"] == "H Unprot"
    assert parse_status("Recalibrating to track 0") is None


def test_reading_levels_separate_faults_unknowns_and_controlled_states() -> None:
    healthy = parse_status(
        "Drive A: T0, H0, RPM 300.00, S9/9, OT NO, SEL:ON, MOT:ON, "
        "WP:H Unprot, TK0:L ON, DEN 2:L, DC34:?"
    )
    assert healthy is not None
    levels = reading_levels(healthy)
    assert (levels["rpm"], levels["sectors"], levels["off_track"]) == (
        "good", "good", "good"
    )
    assert levels["disk_change"] == "suspect"
    assert levels["density"] == "info"

    faulty = {**healthy, "rpm": "ERR", "sectors": "7", "off_track": "T1/S2", "track_zero": "H OFF"}
    levels = reading_levels(faulty)
    assert all(levels[key] == "bad" for key in ("rpm", "sectors", "off_track", "track_zero"))

    stopped = {**healthy, "rpm": "off", "sectors": "0", "motor": "OFF"}
    levels = reading_levels(stopped)
    assert levels["rpm"] == "info"
    assert levels["sectors"] == "info"


def test_diagnostic_presets_cover_common_fm_and_mfm_geometries() -> None:
    presets = {preset.key: preset for preset in DIAGNOSTIC_PRESETS}
    assert len(presets) == len(DIAGNOSTIC_PRESETS)
    assert (presets["ibm_1440"].rate, presets["ibm_1440"].sectors,
            presets["ibm_1440"].heads, presets["ibm_1440"].expected_rpm) == (500, 18, 2, 300)
    assert (presets["dec_rx01"].encoding, presets["dec_rx01"].sectors,
            presets["dec_rx01"].bytes_per_sector) == ("fm", 26, 128)
    assert (presets["acorn_dfs_ss"].rate, presets["acorn_dfs_ss"].bytes_per_sector,
            presets["acorn_dfs_ss"].heads) == (125, 256, 1)
    assert (presets["ibm_360_hd_drive"].rate,
            presets["ibm_360_hd_drive"].double_step) == (300, True)


def test_selected_preset_changes_expected_rpm_classification() -> None:
    reading = parse_status(
        "Drive A: T0, H0, RPM 300.00, S9/9, OT NO, SEL:ON, MOT:ON, "
        "WP:H Unprot, TK0:L ON, DEN 2:L, DC34:?"
    )
    assert reading is not None
    assert reading_levels(reading, expected_rpm=360)["rpm"] == "bad"
    assert reading_levels({**reading, "rpm": "359.50"}, expected_rpm=360)["rpm"] == "good"


def test_keyboard_queue_supplies_individual_controls() -> None:
    keyboard = PipeKeyboard()
    assert not keyboard.kbhit()
    keyboard._keys.put("+")
    keyboard._keys.put("h")
    assert keyboard.read_key() == "+"
    assert keyboard.read_key() == "h"
    assert keyboard.read_key() is None


def test_worker_passes_options_and_keyboard_to_fork(monkeypatch) -> None:
    calls: list[list[str]] = []
    tools_module = ModuleType("greaseweazle.tools")
    tools_module.diag = SimpleNamespace(
        keyboard=SimpleNamespace(Keyboard=None),
        main=lambda argv: calls.append(argv),
    )
    monkeypatch.setitem(sys.modules, "greaseweazle", ModuleType("greaseweazle"))
    monkeypatch.setitem(sys.modules, "greaseweazle.tools", tools_module)
    monkeypatch.setattr(sys, "argv", ["worker", "--rate", "500", "--double-step"])

    assert main() == 0
    assert calls == [["gw", "diag", "--rate", "500", "--double-step"]]
    assert tools_module.diag.keyboard.Keyboard is PipeKeyboard
