from fluxctl.application.conversion_operations import _compare_snapshots, _track_snapshot
from fluxctl.sector.models import Sector, TrackSectors


def _snapshot(*, deleted: bool = False, crc_ok: bool = True, data: bytes = b"data") -> dict:
    track = TrackSectors(
        track=0,
        head=0,
        sectors=[
            Sector(
                cylinder=0,
                head=0,
                sector_id=1,
                size_code=0,
                data=data,
                crc_ok=crc_ok,
                confidence=1.0,
                deleted=deleted,
            )
        ],
    )
    return _track_snapshot([track])


def _files() -> dict:
    return {"readable": False, "filesystem": None, "files": {}, "extraction_errors": []}


def test_roundtrip_comparison_separates_data_from_deleted_mark_preservation() -> None:
    comparison = _compare_snapshots(_snapshot(), _snapshot(deleted=True), _files(), _files())

    assert comparison["data"]["match"] is True
    assert comparison["logical_geometry"]["match"] is True
    assert comparison["preservation"]["match"] is False
    assert comparison["preservation"]["deleted_marks_match"] is False


def test_roundtrip_comparison_reports_sector_data_and_crc_differences() -> None:
    comparison = _compare_snapshots(
        _snapshot(),
        _snapshot(crc_ok=False, data=b"changed"),
        _files(),
        _files(),
    )

    assert comparison["data"]["match"] is False
    assert comparison["data"]["different_sector_count"] == 1
    assert comparison["preservation"]["crc_status_match"] is False
