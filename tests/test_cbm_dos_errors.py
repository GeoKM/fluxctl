import json
from pathlib import Path

from fluxctl.cbm_dos_errors import cbm_dos_error_for_sector
from fluxctl.layouts.loader import ensure_layout_loaded, load_builtin_layouts
from fluxctl.reports.map import build_disk_map_from_tracksectors
from fluxctl.reports.qc import build_qc_report_from_tracks, write_qc_report_text
from fluxctl.sector.models import Sector, TrackSectors


def _cbm_layout():
    load_builtin_layouts()
    return ensure_layout_loaded("commodore_gcr_1541_170k")


def test_cbm_dos_error_mapping_distinguishes_missing_and_bad_data() -> None:
    missing = cbm_dos_error_for_sector(None, data_block_missing=True)
    bad = cbm_dos_error_for_sector(
        Sector(0, 0, 0, 1, b"data", crc_ok=False, confidence=1.0)
    )

    assert missing is not None
    assert (missing.code, missing.message) == (22, "READ ERROR (data block not present)")
    assert bad is not None
    assert (bad.code, bad.message) == (23, "READ ERROR (checksum error in data block)")


def test_cbm_qc_reports_inferred_error_codes_and_map_details(tmp_path: Path) -> None:
    layout = _cbm_layout()
    tracks = [
        TrackSectors(
            track=0,
            head=0,
            sectors=[
                Sector(0, 0, 0, 1, b"bad", crc_ok=False, confidence=1.0),
                Sector(0, 0, 1, 1, b"good", crc_ok=True, confidence=1.0),
            ],
        )
    ]

    report = build_qc_report_from_tracks(tracks, layout=layout)
    assert report.cbm_dos_errors == {"22": 19, "23": 1}
    payload = json.loads(report.to_json())
    assert payload["cbm_dos_errors"] == {"22": 19, "23": 1}

    text_path = tmp_path / "qc.txt"
    write_qc_report_text(report, text_path, layout=layout)
    text = text_path.read_text()
    assert "22: READ ERROR (data block not present) (19)" in text
    assert "23: READ ERROR (checksum error in data block) (1)" in text

    disk_map = build_disk_map_from_tracksectors(tracks, layout=layout)
    assert disk_map.sector_details[0][0].cbm_dos_error_code == 23
    assert disk_map.sector_details[0][0].cbm_dos_error.endswith("checksum error in data block)")
