from pathlib import Path

import pytest

from fluxctl.cli import _prepare_image
from fluxctl.exceptions import FilesystemError
from fluxctl.filesystems.displaywriter import DisplaywriterFS
from fluxctl.layouts.loader import load_builtin_layouts


FIXTURE_DISPLAYWRITER = Path("tests/fixtures/8inch/IBM/IBM-6580-SSDD-FM-DisplayWriter-284K.scp")


def test_displaywriter_lists_standard_label_entries() -> None:
    load_builtin_layouts()
    image = _prepare_image(FIXTURE_DISPLAYWRITER, "ibm_displaywriter_fm_284k", "fm")
    fs = DisplaywriterFS()

    assert fs.probe(image)
    entries = fs.list_directory("/")

    assert [entry.name for entry in entries] == ["WPE"]
    assert entries[0].cluster_start == 8
    assert entries[0].attributes == 256


def test_displaywriter_file_extraction_is_explicitly_unsupported() -> None:
    load_builtin_layouts()
    image = _prepare_image(FIXTURE_DISPLAYWRITER, "ibm_displaywriter_fm_284k", "fm")
    fs = DisplaywriterFS()
    assert fs.probe(image)

    with pytest.raises(FilesystemError, match="not implemented"):
        fs.extract_file("/WPE")
