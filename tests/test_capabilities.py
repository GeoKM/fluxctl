from pathlib import Path

from fluxctl.capabilities import capability_markdown, filesystem_capability, filesystem_capabilities


def test_registry_resolves_container_specific_mutations() -> None:
    assert filesystem_capability("fat12", "img").mutation_actions == {
        "replace_file", "delete_entry", "import_file", "import_directory", "create_directory"
    }
    assert filesystem_capability("cbm_dos", "d64").mutation_actions == {
        "replace_file", "delete_entry", "import_file"
    }
    assert filesystem_capability("cbm_dos", "scp") is None


def test_generated_capability_table_covers_every_registry_entry() -> None:
    table = capability_markdown()
    for capability in filesystem_capabilities():
        assert f"`{capability.filesystem}`" in table
    assert Path("docs/capability_registry.md").read_text(encoding="utf-8").endswith(table + "\n")
