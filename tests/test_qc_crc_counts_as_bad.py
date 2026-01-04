from fluxctl.reports.qc import _summarize_track_sectors
from fluxctl.sector.models import Sector, TrackSectors


def _build_sample_track_sectors() -> TrackSectors:
    return TrackSectors(
        track=0,
        head=0,
        sectors=[
            Sector(
                cylinder=0,
                head=0,
                sector_id=1,
                size_code=2,
                data=b"\x00",
                crc_ok=True,
                confidence=0.9,
            ),
            Sector(
                cylinder=0,
                head=0,
                sector_id=2,
                size_code=2,
                data=b"\x00",
                crc_ok=False,
                confidence=0.9,
            ),
            Sector(
                cylinder=0,
                head=0,
                sector_id=3,
                size_code=2,
                data=b"",
                crc_ok=True,
                confidence=0.9,
            ),
        ],
    )


def test_crc_errors_counted_as_bad_when_present() -> None:
    track_sectors = _build_sample_track_sectors()

    summary = _summarize_track_sectors(track_sectors, missing=0)

    assert summary["crc_errors"] == 1
    assert summary["no_data"] == 1
    assert summary["good"] == 1
    assert summary["bad"] == 2


def test_missing_sectors_fold_into_bad_counts() -> None:
    track_sectors = _build_sample_track_sectors()

    summary = _summarize_track_sectors(track_sectors, missing=2)

    assert summary["bad"] == 2
