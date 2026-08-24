from pathlib import Path

from fluxctl.application.workspace_operations import load_workspace, save_workspace


def test_workspace_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "session.json"
    state = {
        "schema": "fluxctl-studio-workspace-v1",
        "image": "/tmp/disk.scp",
        "recent_images": ["/tmp/disk.scp"],
        "log": "QC good",
    }

    save_workspace(path, state)

    assert load_workspace(path) == state


def test_workspace_rejects_unknown_schema(tmp_path: Path) -> None:
    path = tmp_path / "invalid.json"
    path.write_text('{"schema": "other"}', encoding="utf-8")

    try:
        load_workspace(path)
    except ValueError as exc:
        assert "Unsupported" in str(exc)
    else:
        raise AssertionError("invalid workspace was accepted")
