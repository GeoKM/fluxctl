import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))


_FIXTURE_FULL_MODULES = {
    "test_amiga.py", "test_cpm_filesystem.py", "test_displaywriter_filesystem.py",
    "test_fat12_filesystem.py", "test_filesystem_detection.py", "test_newdos80_filesystem.py",
    "test_probe.py", "test_qc.py", "test_qc_crc_counts_as_bad.py", "test_seiko_8300.py",
    "test_studio_services.py", "test_trs80.py", "test_trsdos_filesystem.py",
    "test_visualization.py", "test_wang.py",
}

_EXTENDED_MODULES = {
    "test_amiga_pll_fallback.py", "test_apple2.py", "test_exporters.py", "test_sector_reconstruction.py",
}


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", "fast: deterministic tests suitable for every pull request")
    config.addinivalue_line("markers", "fixture_full: tests that exercise the broad fixture collection")
    config.addinivalue_line("markers", "extended: expensive conversion, round-trip, or decoder tests")
    config.addinivalue_line("markers", "hardware: tests requiring a real Greaseweazle or floppy device")


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Assign a default tier while allowing individual tests to add markers."""
    for item in items:
        module_name = Path(str(item.fspath)).name
        if module_name in _EXTENDED_MODULES:
            item.add_marker(pytest.mark.extended)
        if module_name in _FIXTURE_FULL_MODULES:
            item.add_marker(pytest.mark.fixture_full)
        if module_name not in _EXTENDED_MODULES and module_name not in _FIXTURE_FULL_MODULES:
            item.add_marker(pytest.mark.fast)
