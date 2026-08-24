import hashlib
import json
from pathlib import Path

from fluxctl.cli import _prepare_image
from fluxctl.filesystem_detection import detect_filesystem
from fluxctl.layouts.loader import load_builtin_layouts


ROOT = Path("tests/fixtures")
SIDECARS = sorted(ROOT.rglob("*.json"))


def test_fixture_sidecars_use_expectation_schema() -> None:
    assert SIDECARS
    for sidecar in SIDECARS:
        payload = json.loads(sidecar.read_text(encoding="utf-8"))
        assert payload["schema"] == "fluxctl.fixture-expectation/v1"
        assert payload["verified_by"]
        assert payload["geometry"]["layout"]
        assert payload["geometry"]["encoding"]
        assert payload["filesystem"]
        assert set(payload["qc"]) >= {"good_min", "weak_max", "missing_max", "bad_max"}
        assert isinstance(payload["directory_entries"], list)
        assert isinstance(payload["selected_file_hashes"], dict)
        assert isinstance(payload["supported_conversions"], list)


def test_verified_sidecar_file_hashes_match_fixture_content() -> None:
    sidecar = ROOT / "3.5inch/IBM/IBM-Generic-DSDD-MFM-IBMPC-720K.json"
    payload = json.loads(sidecar.read_text(encoding="utf-8"))
    fixture = sidecar.with_suffix(".img")
    load_builtin_layouts()
    image = _prepare_image(fixture, payload["geometry"]["layout"], payload["geometry"]["encoding"])
    detection = detect_filesystem(image)
    assert detection.primary == payload["filesystem"]
    assert detection.plugin is not None
    for name, expected_hash in payload["selected_file_hashes"].items():
        actual = hashlib.sha256(detection.plugin.extract_file(f"/{name}")).hexdigest()
        assert actual == expected_hash
