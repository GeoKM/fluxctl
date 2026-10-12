import os

import fluxctl.integrations as integrations


def test_packaged_helpers_are_found_without_developer_checkout(monkeypatch, tmp_path) -> None:
    executable = tmp_path / "release" / "fluxctl"
    executable.parent.mkdir()
    executable.touch()
    helpers = executable.parent / "helpers"
    helpers.mkdir()
    gw = helpers / ("gw.exe" if os.name == "nt" else "gw")
    gw.touch()
    hxcfe = helpers / ("hxcfe.exe" if os.name == "nt" else "hxcfe")
    hxcfe.touch()
    monkeypatch.setattr(integrations.sys, "executable", str(executable))
    monkeypatch.setattr(integrations.shutil, "which", lambda name: None)
    monkeypatch.delenv("FLUXCTL_GW_PATH", raising=False)
    monkeypatch.delenv("FLUXCTL_HXCFE_PATH", raising=False)

    assert integrations.greaseweazle_executable() == gw
    assert hxcfe in integrations.bundled_hxcfe_candidates()


def test_explicit_gw_path_precedes_bundled_copy(monkeypatch, tmp_path) -> None:
    requested = tmp_path / "custom-gw"
    requested.touch()
    monkeypatch.setenv("FLUXCTL_GW_PATH", str(requested))

    assert integrations.greaseweazle_executable() == requested
