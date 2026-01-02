from pathlib import Path

from fluxctl.decoding.mfm import mfm_decoder
from fluxctl.exceptions import FluxDecodeError
from fluxctl.reports.qc import DiskQCReport, build_qc_report
from fluxctl.scp import parse_scp


class BrokenDecoder:
    """Decoder stub that forces a failure path for QC reporting."""

    def decode_revolution(self, rev):  # pragma: no cover - intentional failure
        raise FluxDecodeError("forced failure for testing")


FIXTURE_GOOD = Path("tests/fixtures/5.25inch/IBM/IBM-Generic-SSDD-MFM-IBMPC-180K.scp")


def test_qc_report_counts_good_disk() -> None:
    image = parse_scp(FIXTURE_GOOD)
    report = build_qc_report(image, mfm_decoder)

    assert len(report.tracks) == len(image.tracks)
    first_track = report.tracks[0]
    assert first_track.total_sectors == 9
    # CRC failures should be reflected in the bad sector count so the summary matches the per-sector map.
    assert first_track.bad_sectors == first_track.total_sectors
    assert first_track.confidence > 0.5
    assert report.overall_confidence > 0.5
    assert first_track.crc_errors == first_track.total_sectors


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
