# fluxctl

fluxctl is a modular toolkit for inspecting and converting floppy disk flux captures. It supports decoding flux streams, reconstructing sectors, quality control, visualization, extraction, and exporting to standard image formats.

## Getting started
- `python3 -m venv .venv` and `.venv/bin/python -m pip install --upgrade pip` to prepare a local environment.
- Install the project and CLI helpers with `.venv/bin/python -m pip install -e .`.
- Run `.venv/bin/fluxctl --help` to confirm the CLI loads and to explore available targets.

## Supported operations
- **info**: inspect SCP headers and inferred geometry.
- **probe**: detect encoding, layout, and filesystem (where detectable) for SCP and flat images.
- **compare**: hash + byte diff two images; SCP inputs are decoded first.
- **qc**: generate quality control reports (JSON or text).
- **visualize**: render ASCII or SVG disk maps.
- **extract**: detect filesystems and extract files or raw sectors.
- **convert**: export to raw, IMD, ADF, D64, and G64 images.
- **sectors/dump/patch**: per-track listing, hex dump, and simple patching helpers.

## Encodings and filesystems
- Encodings: MFM, FM, GCR (Commodore) via plugin registry.
- Filesystems detected: FAT12, CBM DOS, CP/M (C64 CP/M 2.2, C128 CP/M 3.0), Amiga OFS/FFS, RT-11 (RX02), Displaywriter probe; raw sector dumps always supported.

## Usage examples
```bash
# Quick identification
fluxctl info disk.scp
fluxctl probe disk.scp

# Compare two images (SCP decoded on the fly)
fluxctl compare a.scp b.img --json-out diff.json

# Quality reports and maps
fluxctl qc disk.scp --json-out qc.json
fluxctl visualize disk.scp --format ascii --out map.txt

# Export / convert
fluxctl convert disk.scp --to d64 --out disk.d64 --layout commodore_gcr_1541_170k
fluxctl convert disk.scp --to g64 --out disk.g64 --layout commodore_gcr_1541_170k
fluxctl convert disk.img --to raw --out copy.img

# Extraction
fluxctl extract disk.img --list
fluxctl extract disk.img --path FILE.TXT --out output.bin
fluxctl extract disk.scp --layout ibm_mfm_720k --path README.TXT --out readme.bin

# Per-track inspection
fluxctl sectors disk.scp --track 0 --head 0 --encoding mfm
fluxctl dump disk.scp --layout ibm_mfm_720k --track 0 --side 0 --sector 1
```

### Commodore exports

- **D64**: reconstructed 256-byte logical sectors written to a flat image. This
  is convenient for filesystem access but loses per-track GCR details and any
  copy-protection data.
- **G64**: preserves the decoded GCR nibble stream for each track in a
  half-track container. This format retains gaps and sync marks for better
  fidelity in emulators, but currently derives half-tracks from full-track
  captures only (no separate half-track decoding yet).

## Testing
- Execute `.venv/bin/python -m pytest` after activating the venv to cover CLI helpers, decoding, exporters, and filesystems.
- The repository also includes `tests/fixtures` with annotated samples so you can run targeted commands against known media.
- For full CLI validation across SCP fixtures (with longer GCR timeouts), run `scripts/fixture_cli_smoke.py`.

## Optional integrations

### Greaseweazle-assisted Amiga decoding
Fluxctl can fall back to Greaseweazle’s Amiga codec for higher-fidelity PLL decoding. This is **optional**; when missing, fluxctl uses its own PLL/parser.

Steps:
1. Get dependencies:
   ```
   .venv/bin/pip install -e .[greaseweazle]
   ```
2. Clone Greaseweazle alongside fluxctl (sibling directory) and install it editable:
   ```
   git clone https://github.com/keirf/Greaseweazle.git ../greaseweazle
   .venv/bin/pip install -e ../greaseweazle
   ```
No configuration is required; fluxctl will auto-detect the import at runtime when present.

### HxC Floppy Emulator (hxcfe) hints / conversions
HxCFE can provide layout hints and ADF conversions for Amiga and other formats.

Steps:
1. Clone and build `hxcfe` (the CLI from HxCFloppyEmulator). Example:
   ```
   git clone https://github.com/jfdelnero/HxCFloppyEmulator.git ../HxCFloppyEmulator
   cd ../HxCFloppyEmulator/HxCFloppyEmulator_cmdline/build
   make       # or use the provided build script for your platform
   ```
2. Point fluxctl at the binary when running commands that accept `--hxcfe`, e.g.:
   ```
   fluxctl qc disk.scp --layout amiga_mfm_880k --hxcfe ~/src/HxCFloppyEmulator/HxCFloppyEmulator_cmdline/build/hxcfe
   fluxctl probe disk.scp --hxcfe /path/to/hxcfe
   ```
HxCFE is optional; when omitted, fluxctl uses its own detectors.

## Contributor guide
See [AGENTS.md](AGENTS.md) for coding standards, workflows, and review expectations.

## Provenance
All commands emit provenance sidecars alongside outputs (e.g. `map.txt.provenance.json`). Records capture tool version, inputs, outputs, parameters, and timestamps so artefacts can be verified later.
