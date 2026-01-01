# fluxctl

fluxctl is a modular toolkit for inspecting and converting floppy disk flux captures. It supports decoding flux streams, reconstructing sectors, quality control, visualization, extraction, and exporting to standard image formats.

## Supported operations
- **info/probe**: inspect SCP headers and candidate layouts.
- **qc**: generate quality control reports (JSON or text).
- **visualize**: render ASCII or SVG disk maps.
- **extract**: detect filesystems (FAT12, simplified Amiga OFS) and extract files or raw sectors.
- **convert**: export to raw, IMD, and ADF images.

## Encodings and filesystems
- Encodings: MFM, GCR (Commodore) via plugin registry.
- Filesystems: FAT12, simplified Amiga OFS; raw sector dumps supported.

## Usage examples
```bash
fluxctl qc disk.scp --json-out qc.json
fluxctl visualize disk.scp --format ascii --out map.txt
fluxctl convert disk.img --to raw --out copy.img
fluxctl extract disk.img --list
fluxctl extract disk.img --path FILE.TXT --out output.bin
fluxctl convert disk.scp --to adf --out disk.adf --encoding gcr
```

## Provenance
All commands emit provenance sidecars alongside outputs (e.g. `map.txt.provenance.json`). Records capture tool version, inputs, outputs, parameters, and timestamps so artefacts can be verified later.
