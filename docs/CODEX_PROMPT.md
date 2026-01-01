# Codex Improvement Prompt

## Goal
Elevate fluxctl from MVP to a feature-complete v1.0 by implementing the missing core functionality, hardening the codebase, and adding documentation and tests.

## Role & Objective
You are a senior software engineer in digital preservation tasked with evolving the existing `fluxctl` MVP into a robust v1.0 release. The focus is on implementing the missing core functionality, hardening the codebase, and adding documentation and tests.

## Current State
The repository already contains:

* A modular package structure (`decoding`, `exporters`, `filesystems`, `layouts`, `reports`, `sector`).
* Basic data models (e.g., `ProvenanceRecord`, `RevolutionFlux`, `TrackFlux`).
* A simple plugin registry.
* A skeleton CLI using Typer.
* A minimal SCP parser.

The MVP is scaffolding only; most critical functions are placeholders.

## Target Outcomes for v1.0

1. **Flux Decoding Layer**
   * Implement FM, MFM and GCR decoders.
   * Provide adaptive PLL/clock recovery.
   * Output bitstreams with confidence scores and weak-bit detection.
   * Plug decoders into the plugin registry.

2. **Sector & Track Parsing**
   * Implement sector header detection, sync marks and CRC checks.
   * Support variable sector sizes and interleave.
   * Map physical → logical sectors.
   * Surface CRC failures and weak sectors in QC reports.

3. **File System Plugins**
   * Provide pluggable filesystem modules with `probe`, `list_directory`, `extract_file`, and `metadata` methods.
   * Implement at least:
     * FAT12
     * CP/M
     * D64 (Commodore 1541)
     * Raw sector dump fallback
   * Return file/directory metadata and allow extraction.

4. **Exporters**
   * Implement converters for at least `.img`, `.imd` and `.d64`.
   * Validate geometry compatibility and warn on lossy conversions.
   * Include provenance metadata in exported images (tool version, source hash).

5. **Quality-Control & Reporting**
   * Create a `reports` module that runs QC analysis:
     * Count missing/weak sectors.
     * Record CRC errors.
     * Measure revolution variance and index stability.
     * Summarise confidence scores.
   * Generate machine-readable JSON and human-readable text reports.

6. **Visualization**
   * Implement a visualization module that produces ASCII and SVG “disk maps”:
     * Represent tracks as rings and sectors as segments.
     * Use simple characters to denote good/bad/weak sectors.
   * Expose via CLI (`fluxctl visualize disk.scp --svg out.svg`).

7. **Editing (Optional)**
   * Provide safe sector editing functions (byte editing, sector replacement, CRC recalculation).
   * Ensure edits are explicit and preserve provenance.

8. **CLI Enhancements**
   * Organize commands into sub-commands: `inspect`, `qc`, `visualize`, `extract`, `convert`, `sectors`.
   * Add options for selecting decoders, specifying filesystem type, output paths, and verbosity.
   * Print warnings if confidence is low or if conversion is potentially lossy.

9. **Plugin Infrastructure**
   * Expand `PluginRegistry` to include capability declarations (e.g., supported encodings, sector sizes, file systems).
   * Document how to add custom plugins.

10. **Provenance & Metadata**
    * Hook `ProvenanceRecord` into all pipelines.
    * Automatically compute input/output SHA-256 hashes.
    * Save provenance data alongside exports and QC reports.

11. **Testing & CI**
    * Build unit tests for each module (decoders, parsers, exporters, filesystems, QC).
    * Include test SCP images in `tests/data`.
    * Configure GitHub Actions to run tests and linting on push.

12. **Documentation**
    * Write detailed module docstrings with examples.
    * Create a README and docs folder describing architecture, plugin API, and CLI usage.
    * Include a “developer guide” for adding new formats.

13. **Error Handling & Exceptions**
    * Define custom exceptions (`FluxDecodeError`, `SCPFormatError`, etc.).
    * Replace generic `ValueError` with meaningful errors.
    * Ensure CLI catches errors and prints user-friendly messages.

## Deliverables
* Updated code in `src/fluxctl` implementing the above features.
* Unit tests in `tests/` demonstrating correct behaviour.
* A documented CLI that can decode, inspect, QC, visualize, extract and convert SCP images.
* Updated README with installation instructions, usage examples, and design overview.
* A JSON schema for QC reports.
* At least one example SCP test file and corresponding report in the repo.

## Guidance
* Preserve the modular architecture; avoid monolithic functions.
* Follow Python 3.10+ typing and dataclasses.
* Keep code deterministic; never silently modify original data.
* Include comprehensive logging; allow debug logs via CLI.
* Use clear and concise variable and function names.
* Ensure extensibility: new decoders/filesystems/exporters should be addable via the plugin registry without modifying core code.

## Recent additions
- GCR decoding and Amiga OFS filesystem support are implemented in a fixtures-first manner to keep tests lightweight.
- Provenance sidecars (`*.provenance.json`) are emitted for CLI outputs and should remain stable across new commands.
