# Architecture

fluxctl is organised around plugins:
- **Decoders** convert flux revolutions to bitstreams (MFM, GCR).
- **Sector reconstruction** turns bitstreams into sector models.
- **Filesystems** interpret sector images (FAT12, Amiga OFS/FFS, CBM DOS,
  CP/M, Apple II, Tandy, RT-11, DisplayWriter, Seiko, and Wang readers).
- **Exporters** serialise images (raw, IMD, ADF).
- **Reports** provide QC summaries and visual maps.

CLI commands wire these layers together using Typer.

`convert` writes one output image through an exporter. `roundtrip` uses the
same exporter path twice: source to an intermediate container, then intermediate
back to a requested canonical container. It compares decoded sector bytes after
each leg, plus physical sector identity/order, sizes, deleted marks, CRC and
missing/synthesized state, and readable filesystem file hashes. The report
separates data, logical geometry, and preservation equivalence; the legacy
decoded byte match remains available as `roundtrip_match` for scripts.
The comparison deliberately does not require regenerated flux containers to
match original flux timing byte-for-byte.

`recover` is the explicit multi-revolution repair path. It decodes each SCP
revolution independently, records competing sector copies and the selected
candidate, then writes a new image and JSON recovery manifest. `strict-crc`
only accepts populated CRC-valid candidates; `best-effort` may select the best
populated candidate when no valid copy exists. Recovery rejects an output path
that aliases the source and never edits the source capture.

File-producing CLI commands also write provenance sidecars through
`fluxctl.provenance.write_provenance`. The sidecar records the operation,
input/output paths and hashes, command parameters, and decoder/exporter
identifiers. Terminal-only inspection commands do not create sidecars unless the
user supplies an output path such as `--json-out`, `--text-out`, or `--out`.
