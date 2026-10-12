"""Configuration and status parsing for the optional Greaseweazle drive diagnostic."""
from __future__ import annotations

from dataclasses import dataclass
import importlib.util
import re
import sys


VALID_KEYS = frozenset("0123456789+-,.rhmsdq")


@dataclass(frozen=True)
class DiagnosticPreset:
    """A common IBM FM/MFM disk format for live drive checks.

    Density and bytes per sector describe the disk; gw diag has no matching
    command-line options for them. RPM is an expectation, not a fixed override.
    """

    key: str
    label: str
    encoding: str
    rate: int
    cylinders: int
    heads: int
    sectors: int
    bytes_per_sector: int
    expected_rpm: int
    density: str
    double_step: bool = False


DIAGNOSTIC_PRESETS: tuple[DiagnosticPreset, ...] = (
    DiagnosticPreset("ibm_160", "IBM PC 5¼″ 160 KB · SS DD", "mfm", 250, 40, 1, 8, 512, 300, "Double density"),
    DiagnosticPreset("ibm_180", "IBM PC 5¼″ 180 KB · SS DD", "mfm", 250, 40, 1, 9, 512, 300, "Double density"),
    DiagnosticPreset("ibm_320", "IBM PC 5¼″ 320 KB · DS DD", "mfm", 250, 40, 2, 8, 512, 300, "Double density"),
    DiagnosticPreset("ibm_360", "IBM PC 5¼″ 360 KB · DS DD", "mfm", 250, 40, 2, 9, 512, 300, "Double density"),
    DiagnosticPreset("ibm_360_hd_drive", "IBM PC 5¼″ 360 KB in 80-track drive · DS DD", "mfm", 300, 40, 2, 9, 512, 360, "Double density", True),
    DiagnosticPreset("ibm_720", "IBM PC 3½″ 720 KB · DS DD", "mfm", 250, 80, 2, 9, 512, 300, "Double density"),
    DiagnosticPreset("ibm_1200", "IBM PC 5¼″ 1.2 MB · DS HD", "mfm", 500, 80, 2, 15, 512, 360, "High density"),
    DiagnosticPreset("ibm_1440", "IBM PC 3½″ 1.44 MB · DS HD", "mfm", 500, 80, 2, 18, 512, 300, "High density"),
    DiagnosticPreset("ibm_2880", "IBM PC 3½″ 2.88 MB · DS ED", "mfm", 1000, 80, 2, 36, 512, 300, "Extra density"),
    DiagnosticPreset("dec_rx01", "DEC RX01 8″ · SS SD (FM)", "fm", 250, 77, 1, 26, 128, 360, "Single density"),
    DiagnosticPreset("acorn_dfs_ss", "BBC Micro DFS 5¼″ 100 KB · SS SD (FM)", "fm", 125, 40, 1, 10, 256, 300, "Single density"),
    DiagnosticPreset("acorn_dfs_ds", "BBC Micro DFS 5¼″ 200 KB · DS SD (FM)", "fm", 125, 40, 2, 10, 256, 300, "Single density"),
    DiagnosticPreset("acorn_dfs_ds80", "BBC Micro DFS 5¼″ 400 KB · DS SD (FM)", "fm", 125, 80, 2, 10, 256, 300, "Single density"),
    DiagnosticPreset("trs80_sssd_35", "TRS-80 5¼″ Full-height SSSD 87.5 KB · 35-track", "fm", 125, 35, 1, 10, 256, 300, "Single density"),
    DiagnosticPreset("trs80_ssdd_40", "TRS-80 5¼″ Full-height SSDD 180 KB · 40-track", "mfm", 250, 40, 1, 18, 256, 300, "Double density"),
    DiagnosticPreset("kaypro_ii_200", "Kaypro II 5¼″ 200 KB · SS DD (MFM)", "mfm", 250, 40, 1, 10, 512, 300, "Double density"),
    DiagnosticPreset("osborne_sd", "Osborne 1 5¼″ 100 KB · SS SD (FM)", "fm", 125, 40, 1, 10, 256, 300, "Single density"),
)
_ANSI = re.compile(r"\x1b\[[0-9;]*m")
_STATUS = re.compile(
    r"^Drive (?P<drive>[^:]+): T(?P<cylinder>\d+), H(?P<head>\d+), "
    r"RPM (?P<rpm>[^,]+), S(?P<sectors>\d+)/(?P<expected>\d+|\?), "
    r"OT (?P<off_track>.*?), SEL:(?P<select>ON|OFF), "
    r"MOT:(?P<motor>ON|OFF), WP:(?P<write_protect>.*?), "
    r"TK0:(?P<track_zero>.*?), DEN (?P<density_pin>\d+):(?P<density>[HL]), "
    r"DC(?P<disk_change_pin>\d+):(?P<disk_change>.+)$"
)


def diagnostic_availability() -> tuple[bool, str]:
    """Require Adrian Black's diagnostic package in Studio's Python environment."""
    if getattr(sys, "frozen", False):
        from .integrations import diagnostic_worker_executable

        if diagnostic_worker_executable() is None:
            return False, "The bundled Greaseweazle diagnostic worker is missing."
    try:
        available = importlib.util.find_spec("greaseweazle.tools.diag") is not None
    except (ImportError, ModuleNotFoundError, ValueError):
        available = False
    if available:
        return True, "Greaseweazle drive diagnostics available."
    return False, (
        "Install the diagnostic fork into Fluxctl Studio's Python environment: "
        "python -m pip install git+https://github.com/misterblack1/greaseweazle@diag"
    )


@dataclass(frozen=True)
class DiagnosticOptions:
    rate: int = 250
    drive: str = "A"
    device: str = ""
    sectors: int | None = None
    rpm: float | None = None
    encoding: str = "mfm"
    cylinders: int = 84
    heads: int = 2
    double_step: bool = False
    step_delay: int | None = None
    gen_tg43: bool = False

    def arguments(self) -> list[str]:
        if self.rate < 1 or self.cylinders < 1 or self.heads not in (1, 2):
            raise ValueError("Rate and cylinders must be positive; heads must be 1 or 2")
        if self.encoding not in ("mfm", "fm"):
            raise ValueError("Encoding must be MFM or FM")
        if self.drive not in ("A", "B", "0", "1", "2", "3"):
            raise ValueError("Drive must be A, B, or Shugart 0-3")
        if self.sectors is not None and self.sectors < 1:
            raise ValueError("Expected sectors must be positive")
        if self.rpm is not None and self.rpm <= 0:
            raise ValueError("Fixed RPM must be positive")
        if self.step_delay is not None and not 0 <= self.step_delay <= 65535:
            raise ValueError("Step delay must be between 0 and 65535 microseconds")
        args = ["--rate", str(self.rate), "--drive", self.drive,
                "--encoding", self.encoding, "--cyls", str(self.cylinders),
                "--heads", str(self.heads)]
        if self.device.strip():
            args.extend(["--device", self.device.strip()])
        if self.sectors is not None:
            args.extend(["--secs", str(self.sectors)])
        if self.rpm is not None:
            args.extend(["--rpm", str(self.rpm)])
        if self.double_step:
            args.append("--double-step")
        if self.step_delay is not None:
            args.extend(["--step-delay", str(self.step_delay)])
        if self.gen_tg43:
            args.append("--gen-tg43")
        return args


def worker_command(options: DiagnosticOptions) -> list[str]:
    if getattr(sys, "frozen", False):
        from .integrations import diagnostic_worker_executable

        worker = diagnostic_worker_executable()
        if worker is None:
            raise RuntimeError("Bundled Greaseweazle diagnostic worker is missing")
        return [str(worker), *options.arguments()]
    return [sys.executable, "-u", "-m", "fluxctl.drive_diagnostics_worker", *options.arguments()]


def strip_ansi(line: str) -> str:
    return _ANSI.sub("", line)


def parse_status(line: str) -> dict[str, str] | None:
    match = _STATUS.match(strip_ansi(line.strip()))
    return match.groupdict() if match else None


def reading_levels(reading: dict[str, str], expected_rpm: int | None = None) -> dict[str, str]:
    """Classify measurements without treating deliberate drive settings as faults."""
    levels = {key: "info" for key in reading}
    try:
        rpm = float(reading["rpm"])
    except ValueError:
        levels["rpm"] = "info" if reading["rpm"] == "off" else "bad"
    else:
        if expected_rpm is None:
            levels["rpm"] = "good" if 295 <= rpm <= 305 or 355 <= rpm <= 365 else "bad"
        else:
            levels["rpm"] = "good" if abs(rpm - expected_rpm) <= 5 else "bad"

    expected = reading["expected"]
    if reading["motor"] == "OFF" or reading["select"] == "OFF":
        levels["sectors"] = "info"
    elif expected == "?":
        levels["sectors"] = "suspect"
    else:
        levels["sectors"] = "good" if int(reading["sectors"]) == int(expected) else "bad"
    levels["off_track"] = "good" if reading["off_track"] == "NO" else "bad"

    track_zero = reading["track_zero"]
    if "?" in track_zero:
        levels["track_zero"] = "suspect"
    else:
        at_zero = int(reading["cylinder"]) == 0
        asserted = track_zero.endswith(" ON")
        levels["track_zero"] = "good" if at_zero == asserted else "bad"
    if "?" in reading["write_protect"]:
        levels["write_protect"] = "suspect"
    if "?" in reading["disk_change"]:
        levels["disk_change"] = "suspect"
    return levels
