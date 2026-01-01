# fluxctl

Modular SuperCard Pro (SCP) flux imaging toolkit focused on preservation workflows. This initial MVP emphasizes clear separations between flux ingestion, bitstream decoding, sector reconstruction, reporting, and export.

## Getting started

```bash
python -m pip install -e .
fluxctl --help
```

## Layouts

Layout descriptors are data-driven JSON files stored under `fluxctl/data/layouts`. Built-in identifiers:

* `ibm_mfm_1440k`
* `ibm_mfm_720k`
* `ibm_mfm_360k`
* `ibm_mfm_1200k`

## Key commands

* `fluxctl info disk.scp` – basic SCP header details.
* `fluxctl probe disk.scp` – list candidate encodings/layouts.
* `fluxctl qc disk.scp --layout ibm_mfm_1440k --out qc.json` – generate QC report.
* `fluxctl map disk.scp --layout ibm_mfm_1440k --ascii` – render ASCII disk map.
* `fluxctl convert disk.scp --layout ibm_mfm_1440k --to img --out disk.img` – export IMG.
* `fluxctl convert disk.scp --layout ibm_mfm_1440k --to imd --out disk.imd` – export IMD.
* `fluxctl extract disk.scp --layout ibm_mfm_1440k --fs fat12 --out outdir/` – extract reconstructed sectors.

## Testing

Run the minimal sanity test suite with:

```bash
pytest
```

The integration suite builds on disk images stored under `tests/fixtures`, following the naming convention documented in that
folder. Use `fluxctl.fixtures.discover_fixtures` to parameterize tests across every available sample without hard-coding paths.

## Contributor guide

For coding standards, workflows, and review expectations see [AGENTS.md](AGENTS.md).

## Development planning

For the comprehensive prompt that guides the next sprint toward a feature-complete v1.0 release, see [docs/CODEX_PROMPT.md](docs/CODEX_PROMPT.md).
