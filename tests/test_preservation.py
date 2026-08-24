from fluxctl.reports.preservation import (
    SectorCandidateDiagnostic,
    SectorDiagnostic,
    build_flat_sector_diagnostic,
)
from fluxctl.sector.models import Sector, TrackSectors


def test_flat_sector_diagnostic_reports_read_only_provenance_limit() -> None:
    sector = Sector(
        cylinder=2,
        head=1,
        sector_id=5,
        size_code=1,
        data=b"ABCD",
        crc_ok=True,
        confidence=0.75,
        deleted=False,
    )

    diagnostic = build_flat_sector_diagnostic(
        [TrackSectors(track=2, head=1, sectors=[sector])],
        track=2,
        head=1,
        sector_id=5,
    )

    assert diagnostic.selected is not None
    assert diagnostic.selected.crc_ok is True
    assert diagnostic.selected.confidence == 0.75
    assert diagnostic.candidates[0].revolution is None
    assert "per-revolution timing" in diagnostic.to_text()


def test_candidate_text_includes_all_byte_differences() -> None:
    candidate = SectorCandidateDiagnostic(
        revolution=3,
        found=True,
        sector_id=1,
        size=3,
        crc_ok=False,
        confidence=0.4,
        differences=(
            {"offset": 0, "selected": "41", "candidate": "42"},
            {"offset": 2, "selected": "43", "candidate": None},
        ),
    )

    rendered = SectorDiagnostic(
        track=0,
        head=0,
        sector_id=1,
        selected=candidate,
        candidates=(candidate,),
        source_kind="scp",
    ).to_text()
    assert "offset 0: selected=41 candidate=42" in rendered
    assert "offset 2: selected=43 candidate=--" in rendered
