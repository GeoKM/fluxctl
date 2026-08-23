import hashlib
import json
from pathlib import Path

from fluxctl.application.recovery_operations import recover_image


def test_recovery_records_competing_revolutions_and_leaves_source_unchanged(tmp_path: Path) -> None:
    source = Path("tests/fixtures/5.25inch/Commodore/Commodore-1541-SSDD-GCR-C64-170K.scp")
    source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    output = tmp_path / "recovered.img"

    result = recover_image(
        source,
        output,
        None,
        "commodore_gcr_1541_170k",
        "gcr",
        "strict-crc",
        "raw",
    )

    manifest = json.loads(result.manifest_path.read_text())
    assert output.exists()
    assert manifest["input"]["sha256"] == source_hash
    assert manifest["source_unchanged"] is True
    assert manifest["policy"] == "strict-crc"
    assert manifest["tracks"]
    assert manifest["tracks"][0]["revolutions"]
    assert manifest["tracks"][0]["selections"]
    assert manifest["tracks"][0]["selections"][0]["selected_revolution"] is not None
    assert hashlib.sha256(source.read_bytes()).hexdigest() == source_hash
