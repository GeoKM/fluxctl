from pathlib import Path

from fluxctl.decoding.fm import fm_decoder
from fluxctl.decoding.mfm import mfm_decoder
from fluxctl.exceptions import FluxDecodeError
from fluxctl.layouts.loader import ensure_layout_loaded
from fluxctl.reports.qc import DiskQCReport, build_qc_report, _resolve_expected_and_missing
from fluxctl.sector.models import Sector, TrackSectors
from fluxctl.scp import parse_scp


class BrokenDecoder:
    """Decoder stub that forces a failure path for QC reporting."""

    def decode_revolution(self, rev):  # pragma: no cover - intentional failure
        raise FluxDecodeError("forced failure for testing")


FIXTURE_GOOD = Path("tests/fixtures/5.25inch/IBM/IBM-Generic-SSDD-MFM-IBMPC-180K.scp")
FIXTURE_8IN_1200K_FAT = Path("tests/fixtures/8inch/IBM/IBM-Generic-DSDD-MFM-IBMPC-1200K-B.scp")
FIXTURE_DISPLAYWRITER = Path("tests/fixtures/8inch/IBM/IBM-6580-SSDD-FM-DisplayWriter-284K.scp")
FIXTURE_XDF = Path("tests/fixtures/3.5inch/IBM/IBM-XDF-DSHD-MFM-OS2-1890K.scp")


def test_qc_report_counts_good_disk() -> None:
    image = parse_scp(FIXTURE_GOOD)
    report = build_qc_report(image, mfm_decoder)

    assert len(report.tracks) == len(image.tracks)
    first_track = report.tracks[0]
    assert first_track.total_sectors == 9
    assert first_track.bad_sectors == 0
    assert first_track.good_sectors == first_track.total_sectors
    assert first_track.confidence > 0.5
    assert report.overall_confidence > 0.5
    assert first_track.crc_errors == 0
    assert report.status == "good"
    assert report.suspect_sectors == 0
    assert report.total_sectors == sum(track.total_sectors for track in report.tracks[:40])


def test_qc_uses_pll_fallback_for_8inch_mfm_fat() -> None:
    image = parse_scp(FIXTURE_8IN_1200K_FAT)
    layout = ensure_layout_loaded("ibm_mfm_8inch_1200k")
    report = build_qc_report(image, mfm_decoder, layout)

    assert len(report.tracks) == 156
    assert report.total_sectors == 2340
    assert report.total_good_sectors >= 2200
    assert report.total_bad_sectors < 100


def test_qc_uses_fm_pll_fallback_for_displaywriter() -> None:
    image = parse_scp(FIXTURE_DISPLAYWRITER)
    layout = ensure_layout_loaded("ibm_displaywriter_fm_284k")
    report = build_qc_report(image, fm_decoder, layout)

    assert len(report.tracks) == 77
    assert report.total_sectors == 1166
    assert report.total_good_sectors == 1166
    assert report.total_missing_sectors == 0
    assert report.total_bad_sectors == 0
    assert report.status == "good"


def test_qc_handles_ibm_xdf_mixed_sector_tracks() -> None:
    image = parse_scp(FIXTURE_XDF)
    layout = ensure_layout_loaded("ibm_xdf_1890k")
    report = build_qc_report(image, mfm_decoder, layout)

    assert len(report.tracks) == 160
    assert report.total_sectors == 670
    assert report.total_good_sectors == 670
    assert report.suspect_sectors == 0
    assert report.status == "good"
    assert report.tracks[0].total_sectors == 19
    assert report.tracks[2].total_sectors == 4


def test_qc_json_roundtrip_and_failure_detection() -> None:
    image = parse_scp(FIXTURE_GOOD)
    failing_decoder = BrokenDecoder()
    report = build_qc_report(image, failing_decoder)

    first_track = report.tracks[0]
    assert first_track.bad_sectors >= 1
    assert first_track.crc_errors == first_track.bad_sectors

    restored = DiskQCReport.from_json(report.to_json())
    assert restored.tracks[0].bad_sectors == first_track.bad_sectors
    assert restored.overall_confidence == report.overall_confidence


def test_qc_expected_counts_respect_layout() -> None:
    class DummyLayout:
        def expected_sectors_for_track(  # pragma: no cover - simple stub
            self, track: int, head: int | None = None
        ) -> int:
            return 3

    track_sectors = TrackSectors(
        track=0,
        head=0,
        sectors=[
            Sector(cylinder=0, head=0, sector_id=1, size_code=0, data=b"\x00", crc_ok=True, confidence=1.0)
        ],
        missing=2,
    )
    expected, missing = _resolve_expected_and_missing(track_sectors, DummyLayout(), logical_track=0, expected_hint=0)
    assert expected == 3
    assert missing == 2
