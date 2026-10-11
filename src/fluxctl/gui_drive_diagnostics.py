"""Live Greaseweazle drive diagnostic page for Fluxctl Studio."""
from __future__ import annotations

from html import escape

from PySide6.QtCore import QProcess, QTimer
from PySide6.QtGui import QTextCursor
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDoubleSpinBox, QGridLayout,
    QHBoxLayout, QLabel, QLineEdit, QPushButton, QSpinBox, QTextBrowser,
    QTextEdit, QVBoxLayout, QWidget,
)

from .drive_diagnostics import (
    DIAGNOSTIC_PRESETS, DiagnosticOptions, DiagnosticPreset, VALID_KEYS,
    diagnostic_availability, parse_status,
    reading_levels, strip_ansi, worker_command,
)


LEVEL_COLORS = {
    "good": "#6fdb9a",
    "suspect": "#ffc46b",
    "bad": "#ff8389",
    "info": "#8fc7ff",
}

HELP_HTML = """
<h2>Reading the drive diagnostics</h2>
<p>Each sample describes one fresh read at the current track and head. For a
meaningful test, use a known-good IBM FM or MFM formatted disk and select its
data rate and encoding.</p>
<p><b style="color:#6fdb9a">Green</b> means a nominal measurement;
<b style="color:#ffc46b">amber</b> means unknown or unavailable;
<b style="color:#ff8389">red</b> marks an unexpected reading; and
<b style="color:#8fc7ff">blue</b> shows a selected setting or other
informational state. A red reading on an unformatted track or with no disk
inserted is expected and does not by itself diagnose a faulty drive.</p>

<h3>Fields</h3>
<table border="0" cellspacing="0" cellpadding="6" width="100%">
<tr><th align="left">Field</th><th align="left">Meaning and what to expect</th></tr>
<tr><td><b>Drive</b></td><td>Selected drive: A/B on a PC bus or 0–3 on a Shugart bus.</td></tr>
<tr><td><b>Track (T)</b></td><td>Logical cylinder under the head. Double step moves two physical
cylinders per logical track when reading a 40-track disk in an 80-track drive.</td></tr>
<tr><td><b>Head (H)</b></td><td>Selected side, usually 0 or 1.</td></tr>
<tr><td><b>RPM</b></td><td>Measured spindle speed. With a format preset selected,
green means within 5 RPM of that preset's expected speed. In Manual mode,
295–305 or 355–365 RPM is green. <b>ERR</b> means no usable index reading;
<b>off</b> means you switched the motor off.</td></tr>
<tr><td><b>Sectors (Sx/y)</b></td><td>Cleanly decoded sectors / expected sectors for
the current track. A full count, such as 9/9, is good on a known formatted
track. A lower count calls for checking the disk, format settings and drive.
The expected count is guessed for standard rate/RPM combinations unless you
set it; <b>?</b> means it is unknown.</td></tr>
<tr><td><b>Off-track (OT)</b></td><td><b>NO</b> means no sectors claimed another
track. A value such as T11/S2 means two decoded sector IDs claim track 11;
this can indicate mistracking or an incorrect seek.</td></tr>
<tr><td><b>Select (SEL)</b></td><td>Whether the host is selecting the drive.
This is independent of motor control.</td></tr>
<tr><td><b>Motor (MOT)</b></td><td>Whether the host has commanded the spindle
motor on. OFF is normal when you deliberately toggle it off.</td></tr>
<tr><td><b>Write protect (WP)</b></td><td>Raw pin 28 level and decoded state:
<b>H Unprot</b> or <b>L Prot</b>. Protection is a disk/drive state, not a
read-quality fault.</td></tr>
<tr><td><b>Track 0 (TK0)</b></td><td>Track-zero sensor on pin 26. It is
active-low: <b>L ON</b> is expected at track 0; <b>H OFF</b> elsewhere.</td></tr>
<tr><td><b>Density (DEN 2)</b></td><td>Level the host drives on pin 2, not a
measured readback. Some drives change speed when it changes. On some 8-inch
adapters pin 2 is TG43; use the TG43 option to control it automatically.</td></tr>
<tr><td><b>Disk change / ready (DC34)</b></td><td>Pin 34 input.
<b>?</b> means this Greaseweazle cannot read it back. Its function depends
on the drive and cable.</td></tr>
</table>

<h3>Typical IBM MFM examples</h3>
<p>250 kbps at 300 RPM: 9 sectors/track; 300 kbps at 360 RPM: 9;
500 kbps at 360 RPM: 15; 500 kbps at 300 RPM: 18; 1000 kbps at 300 RPM:
36. Other formats, especially FM, may need an explicit expected sector count.</p>

<h3>Controls</h3>
<p><b>Track −/+</b> steps one logical track; <b>Jump</b> goes to the
chosen multiple of ten; <b>Recalibrate</b> homes the head; and the toggle
buttons change head, motor, drive select or density. TG43 disables manual
density control. <b>Stop Diagnostics</b> ends the session.</p>
<h3>Disk format, adapter and step delay</h3>
<p><b>Disk format</b> fills data rate, encoding, expected sectors, cylinders,
heads and double step for common IBM FM/MFM disks. Choose <b>Manual / Custom</b>
to enter your own values. Editing a filled field switches back to Manual while
keeping your current numbers. Bytes per sector, density and expected RPM are
shown for reference. The preset does not switch density pin 2 or force the
decoder's Fixed RPM; use Toggle Density if your drive requires it.</p>
<p>The TRS-80 presets describe common physical track formats, not a particular
DOS. NEWDOS/80, LDOS and TRSDOS can use different disk parameters, and some
boot disks put track 0 in the opposite density from the data tracks. This
diagnostic applies one encoding and expected sector count across the session;
for a mixed-density boot disk, check track 0 and the data tracks in separate
sessions with the matching presets/settings. Sector numbering and DOS directory
structures are outside this drive check.</p>
<p>Choose the preset for the <b>disk inserted</b>, regardless of which computer
the drive came from. A 40-track single-sided drive can use a known-good IBM PC
180 KB disk (9 × 512-byte sectors) or a Kaypro II 200 KB disk (10 × 512-byte
sectors); select the corresponding preset. The TRS-80 87.5 KB SSSD format is
roughly 90 KB in decimal units and tests only tracks 0–34. Check the drive's
rated track count before seeking farther.</p>
<p><b>Adapter port: Auto-detect</b> leaves the serial port blank so Greaseweazle
selects the best matching connected adapter. Enter a port such as
<code>/dev/cu.usbmodem…</code> or <code>COM3</code> to choose one explicitly.
The separate <b>Drive</b> field chooses A/B or Shugart 0–3 on that adapter.</p>
<p><b>Step delay</b> is the pause after each head step, in microseconds.
The default uses the Greaseweazle delay already set on the device. If you see
"Track0 signal asserted after seek", try <b>Recalibrate</b>. If it persists,
stop the session, choose the <b>20 ms preset</b> (20,000 µs), then start
diagnostics again. This overrides the delay for that diagnostic session; it
does not change the saved <code>gw delays --step</code> setting.</p>
<p><a href="https://github.com/misterblack1/greaseweazle/blob/diag/README.md#reading-the-live-status-line">
Adrian Black's full diagnostic README</a></p>
"""


def _reading_html(reading: dict[str, str], expected_rpm: int | None = None) -> str:
    levels = reading_levels(reading, expected_rpm)

    def field(label: str, value: str, key: str) -> str:
        color = LEVEL_COLORS[levels[key]]
        return (f'<span style="color:#aabbd0">{escape(label)}</span>&nbsp;'
                f'<b style="color:{color}">{escape(value)}</b>')

    first = [
        field("Drive", reading["drive"], "drive"),
        field("Track", reading["cylinder"], "cylinder"),
        field("Head", reading["head"], "head"),
        field("RPM", reading["rpm"], "rpm"),
        field("Sectors", f'{reading["sectors"]}/{reading["expected"]}', "sectors"),
        field("Off-track", reading["off_track"], "off_track"),
    ]
    second = [
        field("Select", reading["select"], "select"),
        field("Motor", reading["motor"], "motor"),
        field("Write protect", reading["write_protect"], "write_protect"),
        field("Track 0", reading["track_zero"], "track_zero"),
        field("Density", f'{reading["density_pin"]}:{reading["density"]}', "density"),
        field("Disk change", f'{reading["disk_change_pin"]}:{reading["disk_change"]}', "disk_change"),
    ]
    gap = "&nbsp;&nbsp;&nbsp;"
    return ("<p style='margin-top:0; margin-bottom:12px; line-height:150%'>"
            + gap.join(first) + "<br>" + gap.join(second) + "</p>")


class DriveDiagnosticsPage(QWidget):
    """Operate the fork's diagnostic session and display its live readings."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.process: QProcess | None = None
        self.available = False
        self.help_dialog: QDialog | None = None
        self._pending_output = ""
        self._lines: list[str] = []
        self._build_ui()
        self.refresh_availability()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        title = QLabel("Floppy Drive Diagnostics")
        title.setObjectName("title")
        layout.addWidget(title)
        description = QLabel(
            "Live readings from Adrian Black's Greaseweazle diagnostic fork. "
            "Use a known-good, standard IBM FM or MFM disk for sector and alignment checks."
        )
        description.setWordWrap(True)
        layout.addWidget(description)
        self.availability_label = QLabel()
        self.availability_label.setWordWrap(True)
        layout.addWidget(self.availability_label)

        form = QGridLayout()
        form.setHorizontalSpacing(24)
        form.setVerticalSpacing(8)
        form.setColumnStretch(1, 1)
        form.setColumnStretch(3, 1)
        self.preset_combo = QComboBox()
        self.preset_combo.addItem("Manual / Custom", None)
        for preset in DIAGNOSTIC_PRESETS:
            self.preset_combo.addItem(preset.label, preset.key)
        self.preset_combo.setToolTip(
            "Fill the diagnostic settings for a common FM/MFM disk format. "
            "Manual / Custom keeps all numeric fields editable."
        )
        self.device_input = QLineEdit()
        self.device_input.setPlaceholderText("Auto-detect Greaseweazle serial port")
        self.device_input.setToolTip(
            "Leave blank to select the best matching Greaseweazle serial port. "
            "Enter a port to choose an adapter explicitly; Drive A/B is selected separately."
        )
        self.drive_combo = QComboBox()
        self.drive_combo.addItems(["A", "B", "0", "1", "2", "3"])
        self.rate_spin = QSpinBox()
        self.rate_spin.setRange(1, 5000)
        self.rate_spin.setValue(250)
        self.rate_spin.setSuffix(" kbps")
        self.encoding_combo = QComboBox()
        self.encoding_combo.addItems(["mfm", "fm"])
        self.sectors_spin = QSpinBox()
        self.sectors_spin.setRange(0, 255)
        self.sectors_spin.setSpecialValueText("Auto from rate and RPM")
        self.rpm_spin = QDoubleSpinBox()
        self.rpm_spin.setRange(0, 1000)
        self.rpm_spin.setDecimals(2)
        self.rpm_spin.setSpecialValueText("Live measurement")
        self.cylinders_spin = QSpinBox()
        self.cylinders_spin.setRange(1, 255)
        self.cylinders_spin.setValue(84)
        self.heads_spin = QSpinBox()
        self.heads_spin.setRange(1, 2)
        self.heads_spin.setValue(2)
        self.double_step_check = QCheckBox("80-track drive reading a 40-track disk")
        self.step_delay_spin = QSpinBox()
        self.step_delay_spin.setRange(0, 65535)
        self.step_delay_spin.setSpecialValueText("Use saved Greaseweazle delay")
        self.step_delay_spin.setSuffix(" µs")
        self.step_delay_spin.setToolTip(
            "Pause after each head step. 20000 µs = 20 ms. "
            "Applies when the next diagnostic session starts."
        )
        self.slow_seek_button = QPushButton("20 ms preset")
        self.slow_seek_button.setToolTip("Set a 20000 µs step delay for the next session")
        self.slow_seek_button.clicked.connect(lambda: self.step_delay_spin.setValue(20000))
        step_delay_field = QWidget()
        step_delay_layout = QHBoxLayout(step_delay_field)
        step_delay_layout.setContentsMargins(0, 0, 0, 0)
        step_delay_layout.addWidget(self.step_delay_spin, 1)
        step_delay_layout.addWidget(self.slow_seek_button)
        self.tg43_check = QCheckBox("Automatically drive pin 2 as TG43 (8-inch drives)")
        for index, (label, widget) in enumerate((
            ("Disk format", self.preset_combo), ("Drive", self.drive_combo),
            ("Data rate", self.rate_spin), ("Encoding", self.encoding_combo),
            ("Expected sectors", self.sectors_spin), ("Fixed RPM", self.rpm_spin),
            ("Cylinders", self.cylinders_spin), ("Heads", self.heads_spin),
            ("Double step", self.double_step_check), ("Step delay (µs)", step_delay_field),
            ("TG43", self.tg43_check), ("Adapter port", self.device_input),
        )):
            row, pair = divmod(index, 2)
            form.addWidget(QLabel(label), row, pair * 2)
            form.addWidget(widget, row, pair * 2 + 1)
        layout.addLayout(form)
        self.preset_summary = QLabel()
        self.preset_summary.setWordWrap(True)
        self.preset_summary.setStyleSheet("color: #aabbd0;")
        layout.addWidget(self.preset_summary)
        self.preset_combo.currentIndexChanged.connect(self._apply_preset)
        for signal in (
            self.rate_spin.valueChanged, self.encoding_combo.currentIndexChanged,
            self.sectors_spin.valueChanged, self.cylinders_spin.valueChanged,
            self.heads_spin.valueChanged, self.double_step_check.toggled,
        ):
            signal.connect(self._mark_custom)
        self._apply_preset()
        self.seek_hint_label = QLabel(
            "Track0 seek error: try Recalibrate. If it repeats, stop diagnostics, "
            "choose the 20 ms preset, and start again."
        )
        self.seek_hint_label.setWordWrap(True)
        self.seek_hint_label.setStyleSheet("color: #ffc46b;")
        self.seek_hint_label.hide()
        layout.addWidget(self.seek_hint_label)
        self._configuration = [
            self.preset_combo, self.device_input, self.drive_combo, self.rate_spin, self.encoding_combo,
            self.sectors_spin, self.rpm_spin, self.cylinders_spin, self.heads_spin,
            self.double_step_check, self.step_delay_spin, self.slow_seek_button,
            self.tg43_check,
        ]

        session = QHBoxLayout()
        self.start_button = QPushButton("Start Diagnostics")
        self.start_button.clicked.connect(self.start_session)
        self.stop_button = QPushButton("Stop Diagnostics")
        self.stop_button.clicked.connect(self.stop_session)
        self.refresh_button = QPushButton("Check Installation")
        self.refresh_button.clicked.connect(self.refresh_availability)
        self.help_button = QPushButton("Help…")
        self.help_button.clicked.connect(self.show_help)
        session.addWidget(self.start_button)
        session.addWidget(self.stop_button)
        session.addWidget(self.refresh_button)
        session.addWidget(self.help_button)
        session.addStretch()
        layout.addLayout(session)

        self.readings: dict[str, QLabel] = {}
        readings_grid = QGridLayout()
        fields = [
            ("drive", "Drive"), ("cylinder", "Track"), ("head", "Head"),
            ("rpm", "RPM"), ("sectors", "Clean sectors"),
            ("off_track", "Off-track IDs"), ("select", "Drive select"),
            ("motor", "Motor"), ("write_protect", "Write protect"),
            ("track_zero", "Track 0"), ("density", "Density pin 2"),
            ("disk_change", "Disk change / ready"),
        ]
        for index, (key, label) in enumerate(fields):
            row, column = divmod(index, 3)
            readings_grid.addWidget(QLabel(label), row, column * 2)
            value = QLabel("—")
            value.setObjectName(f"diag_{key}")
            readings_grid.addWidget(value, row, column * 2 + 1)
            self.readings[key] = value
        layout.addLayout(readings_grid)

        controls = QHBoxLayout()
        self.previous_button = self._command_button("Track −", "-")
        self.next_button = self._command_button("Track +", "+")
        self.jump_combo = QComboBox()
        for digit in range(10):
            self.jump_combo.addItem(f"Track {digit * 10}", str(digit))
        self.jump_button = QPushButton("Jump")
        self.jump_button.clicked.connect(lambda: self.send_key(str(self.jump_combo.currentData())))
        self.recalibrate_button = self._command_button("Recalibrate", "r")
        self.head_button = self._command_button("Toggle Head", "h")
        self.motor_button = self._command_button("Toggle Motor", "m")
        self.select_button = self._command_button("Toggle Select", "s")
        self.density_button = self._command_button("Toggle Density", "d")
        self._controls = [self.previous_button, self.next_button, self.jump_combo,
                          self.jump_button, self.recalibrate_button, self.head_button,
                          self.motor_button, self.select_button, self.density_button]
        for widget in self._controls:
            controls.addWidget(widget)
        layout.addLayout(controls)
        self.session_label = QLabel("Idle")
        layout.addWidget(self.session_label)
        self.output = QTextEdit()
        self.output.setReadOnly(True)
        self.output.setMinimumHeight(170)
        layout.addWidget(self.output, 1)
        self._update_controls(False)

    def selected_preset(self) -> DiagnosticPreset | None:
        key = self.preset_combo.currentData()
        return next((preset for preset in DIAGNOSTIC_PRESETS if preset.key == key), None)

    def _apply_preset(self) -> None:
        preset = self.selected_preset()
        if preset is None:
            self.preset_summary.setText("Manual settings — enter the disk's data rate, encoding and geometry below.")
            return
        for widget in (
            self.rate_spin, self.encoding_combo, self.sectors_spin,
            self.cylinders_spin, self.heads_spin, self.double_step_check,
        ):
            widget.blockSignals(True)
        try:
            self.rate_spin.setValue(preset.rate)
            self.encoding_combo.setCurrentText(preset.encoding)
            self.sectors_spin.setValue(preset.sectors)
            self.cylinders_spin.setValue(preset.cylinders)
            self.heads_spin.setValue(preset.heads)
            self.double_step_check.setChecked(preset.double_step)
        finally:
            for widget in (
                self.rate_spin, self.encoding_combo, self.sectors_spin,
                self.cylinders_spin, self.heads_spin, self.double_step_check,
            ):
                widget.blockSignals(False)
        self.preset_summary.setText(
            f"Expected: {preset.cylinders} tracks × {preset.heads} "
            f"side{'s' if preset.heads == 2 else ''}; "
            f"{preset.sectors} × {preset.bytes_per_sector} bytes/sector; "
            f"{preset.rate} kbps {preset.encoding.upper()}; "
            f"{preset.expected_rpm} RPM; {preset.density}. "
            "Density pin 2 remains under manual control."
        )

    def _mark_custom(self) -> None:
        if self.preset_combo.currentIndex() != 0:
            self.preset_combo.setCurrentIndex(0)

    def _command_button(self, label: str, key: str) -> QPushButton:
        button = QPushButton(label)
        button.clicked.connect(lambda: self.send_key(key))
        return button

    def show_help(self) -> None:
        """Open an in-app guide without interrupting a diagnostic session."""
        if self.help_dialog is None:
            dialog = QDialog(self)
            dialog.setWindowTitle("Drive Diagnostics Help")
            dialog.resize(850, 650)
            dialog_layout = QVBoxLayout(dialog)
            browser = QTextBrowser(dialog)
            browser.setObjectName("driveDiagnosticsHelp")
            browser.setOpenExternalLinks(True)
            browser.setStyleSheet(
                "background: #0c1018; color: #e7edf7; "
                "border: 1px solid #2d3a4b; padding: 12px;"
            )
            browser.setHtml(HELP_HTML)
            dialog_layout.addWidget(browser)
            close_button = QPushButton("Close", dialog)
            close_button.clicked.connect(dialog.close)
            dialog_layout.addWidget(close_button)
            self.help_dialog = dialog
        self.help_dialog.show()
        self.help_dialog.raise_()
        self.help_dialog.activateWindow()

    def refresh_availability(self) -> None:
        self.available, detail = diagnostic_availability()
        self.availability_label.setText(detail)
        self._update_controls(self.is_running())

    def is_running(self) -> bool:
        return self.process is not None and self.process.state() != QProcess.NotRunning

    def _update_controls(self, running: bool) -> None:
        for widget in self._configuration:
            widget.setEnabled(not running)
        self.start_button.setEnabled(self.available and not running)
        self.stop_button.setEnabled(running)
        for widget in self._controls:
            widget.setEnabled(running)
        self.density_button.setEnabled(running and not self.tg43_check.isChecked())
        self.head_button.setEnabled(running and self.heads_spin.value() == 2)

    def options(self) -> DiagnosticOptions:
        return DiagnosticOptions(
            rate=self.rate_spin.value(), drive=self.drive_combo.currentText(),
            device=self.device_input.text(),
            sectors=self.sectors_spin.value() or None,
            rpm=self.rpm_spin.value() or None,
            encoding=self.encoding_combo.currentText(),
            cylinders=self.cylinders_spin.value(), heads=self.heads_spin.value(),
            double_step=self.double_step_check.isChecked(),
            step_delay=self.step_delay_spin.value() or None,
            gen_tg43=self.tg43_check.isChecked(),
        )

    def start_session(self) -> None:
        if self.is_running():
            return
        self.refresh_availability()
        if not self.available:
            return
        command = worker_command(self.options())
        self._pending_output = ""
        self._lines.clear()
        self.output.clear()
        self.seek_hint_label.hide()
        for label in self.readings.values():
            label.setText("—")
            label.setStyleSheet("")
        self.process = QProcess(self)
        self.process.setProcessChannelMode(QProcess.MergedChannels)
        self.process.readyReadStandardOutput.connect(self._read_output)
        self.process.started.connect(lambda: self._update_controls(True))
        self.process.finished.connect(self._session_finished)
        self.process.errorOccurred.connect(self._process_error)
        self.session_label.setText("Starting diagnostics…")
        self.process.start(command[0], command[1:])

    def send_key(self, key: str) -> None:
        if key not in VALID_KEYS:
            raise ValueError(f"Unsupported diagnostic key: {key!r}")
        if self.is_running() and self.process is not None:
            self.process.write((key + "\n").encode("ascii"))

    def stop_session(self) -> None:
        if self.is_running() and self.process is not None:
            process = self.process
            self.send_key("q")
            self.session_label.setText("Stopping diagnostics…")
            QTimer.singleShot(
                2000,
                lambda: process.kill()
                if self.process is process and process.state() != QProcess.NotRunning
                else None,
            )

    def _read_output(self) -> None:
        if self.process is None:
            return
        self._pending_output += bytes(self.process.readAllStandardOutput()).decode("utf-8", errors="replace")
        while "\n" in self._pending_output:
            line, self._pending_output = self._pending_output.split("\n", 1)
            self._accept_line(line.rstrip("\r"))

    def _accept_line(self, line: str) -> None:
        clean = strip_ansi(line)
        if "track0 signal asserted after seek" in clean.lower():
            self.seek_hint_label.show()
        self._lines.append(clean)
        self._lines = self._lines[-300:]
        self.output.setHtml(
            '<div style="font-family:monospace; font-size:13px; color:#e7edf7">'
            + "".join(
                _reading_html(reading, self._expected_rpm()) if (reading := parse_status(entry)) is not None
                else f"<p style='margin-top:0; margin-bottom:8px'>{escape(entry)}</p>"
                for entry in self._lines
            )
            + "</div>"
        )
        self.output.moveCursor(QTextCursor.End)
        reading = parse_status(clean)
        if reading is not None:
            for key, label in self.readings.items():
                if key == "sectors":
                    label.setText(f"{reading['sectors']}/{reading['expected']}")
                elif key == "density":
                    label.setText(f"{reading['density_pin']}:{reading['density']}")
                elif key == "disk_change":
                    label.setText(f"{reading['disk_change_pin']}:{reading['disk_change']}")
                else:
                    label.setText(reading[key])
            for key, level in reading_levels(reading, self._expected_rpm()).items():
                if key in self.readings:
                    self.readings[key].setStyleSheet(f"color: {LEVEL_COLORS[level]};")

    def _expected_rpm(self) -> int | None:
        preset = self.selected_preset()
        return preset.expected_rpm if preset is not None else None

    def _session_finished(self, exit_code: int, _status: QProcess.ExitStatus) -> None:
        self._read_output()
        if self._pending_output:
            self._accept_line(self._pending_output)
            self._pending_output = ""
        self.session_label.setText(f"Diagnostics stopped (exit {exit_code})")
        self._update_controls(False)
        if self.process is not None:
            self.process.deleteLater()
            self.process = None

    def _process_error(self, _error: QProcess.ProcessError) -> None:
        if self.process is not None:
            self.session_label.setText(self.process.errorString())
        self._update_controls(False)

    def shutdown(self) -> None:
        if self.is_running() and self.process is not None:
            self.send_key("q")
            if not self.process.waitForFinished(2000):
                self.process.kill()
                self.process.waitForFinished(1000)
