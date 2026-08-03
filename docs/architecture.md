# Architecture

fluxctl is organised around plugins:
- **Decoders** convert flux revolutions to bitstreams (MFM, GCR).
- **Sector reconstruction** turns bitstreams into sector models.
- **Filesystems** interpret sector images (FAT12, Amiga OFS).
- **Exporters** serialise images (raw, IMD, ADF).
- **Reports** provide QC summaries and visual maps.

CLI commands wire these layers together using Typer.

`convert` writes one output image through an exporter. `roundtrip` uses the
same exporter path twice: source to an intermediate container, then intermediate
back to a requested canonical container. It compares decoded sector bytes after
each leg, which is the useful losslessness check for preservation workflows.
The comparison deliberately does not require regenerated flux containers to
match original flux timing byte-for-byte.

File-producing CLI commands also write provenance sidecars through
`fluxctl.provenance.write_provenance`. The sidecar records the operation,
input/output paths and hashes, command parameters, and decoder/exporter
identifiers. Terminal-only inspection commands do not create sidecars unless the
user supplies an output path such as `--json-out`, `--text-out`, or `--out`.
