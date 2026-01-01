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
* `fluxctl sectors disk.scp --track 0 --head 0 --encoding mfm` – decode a specific track and summarize sectors.
* `fluxctl qc disk.scp --encoding mfm --json-out qc.json --text-out qc.txt` – generate QC reports.
* `fluxctl visualize disk.scp --format ascii` – render ASCII disk map for quick inspection.
* `fluxctl convert disk.scp --layout ibm_mfm_1440k --to raw --out disk.img` – export a flat IMG.
* `fluxctl convert disk.scp --layout ibm_mfm_1440k --to imd --out disk.imd` – export ImageDisk with provenance.
* `fluxctl extract disk.scp --list` – auto-detect a filesystem and list the root directory (FAT12 supported).
* `fluxctl extract disk.scp --path AUTOEXEC.BAT --out autoexec.bin` – extract a file from a detected filesystem.

Example conversion from the bundled fixtures:

```bash
fluxctl convert tests/fixtures/3.5inch/IBM/IBM-Generic-DSHD-MFM-IBMPC-1440K.scp --to raw --layout ibm_mfm_1440k --out disk.img
```

### Filesystems

Filesystem plugins are registered via the plugin registry and probed when running `fluxctl extract`. The initial release ships
with a FAT12 implementation that reads MS-DOS formatted floppies, lists directory entries, and extracts file contents. If no
filesystem is detected, `fluxctl extract --out dump.bin` will concatenate reconstructed sectors into a raw dump.

## Quality control

The QC pipeline inspects each decoded track/head pair and reports sector health, weak decodes, CRC failures, and overall
confidence. JSON output is suitable for machine processing while the text report provides a quick per-track summary. Example:

```bash
fluxctl qc disk.scp --encoding mfm --json-out qc.json --text-out qc.txt
```

If no output path is provided, a brief summary is printed to stdout. Additional metrics (flux jitter, index variance) and
encoding support for FM or GCR media can be added by extending `fluxctl.reports.qc`.

## Visualization

Use the visualizer to generate quick disk maps that highlight sector health:

```bash
fluxctl visualize disk.scp --format ascii
fluxctl visualize disk.scp --format svg --out map.svg
```

ASCII output uses `■` for good sectors, `□` for weak sectors (CRC OK but low confidence), and `×` for missing or bad sectors.
SVG output draws concentric rings with green/yellow/red segments for good/weak/bad respectively. Future versions may add PNG
rendering or per-head overlays for multi-sided media.

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
