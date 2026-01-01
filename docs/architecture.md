# Architecture

fluxctl is organised around plugins:
- **Decoders** convert flux revolutions to bitstreams (MFM, GCR).
- **Sector reconstruction** turns bitstreams into sector models.
- **Filesystems** interpret sector images (FAT12, Amiga OFS).
- **Exporters** serialise images (raw, IMD, ADF).
- **Reports** provide QC summaries and visual maps.

CLI commands wire these layers together using Typer.
