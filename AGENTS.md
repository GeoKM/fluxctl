# Repository Guidelines

## Project Structure & Module Organization
The toolkit follows a classic src layout: Python packages live in `src/fluxctl`, with subpackages dedicated to decoding (`decoding/`), reporting (`reports/`), media reconstruction (`sector/`), filesystem extraction (`filesystems/`), and exporters (`exporters/`). The CLI entrypoint is `src/fluxctl/cli.py`, which wires Typer commands to the underlying modules. Layout descriptors and other static assets sit under `src/fluxctl/data/layouts`. Tests reside in `tests/`, mirroring the package structure (`tests/test_layouts.py`, `tests/test_exceptions.py`) and should be expanded alongside new modules.

## Build, Test, and Development Commands
- `python -m pip install -e .` installs fluxctl in editable mode for local hacking.
- `fluxctl --help` verifies the CLI wiring and lists subcommands once dependencies are installed.
- `pytest` runs the current sanity suite; use `pytest tests/test_layouts.py -k mfm` when iterating on a specific area. Keep runs fast—tests rely on tmp_path fixtures instead of large fixture images.

## Coding Style & Naming Conventions
Target Python 3.11+, four-space indentation, and fully type-annotated public APIs. Modules and functions should remain snake_case, while user-facing classes (e.g., `TrackSectors`) stay PascalCase. CLI options follow Typer’s long-flag style (`--layout`, `--out`). Prefer small, pure helpers that can be imported into Typer commands; raise `FluxctlError` or its subclasses so `_handle_cli_errors` surfaces friendly messages. Keep data files JSON-formatted and named after their layout identifiers.

## Testing Guidelines
Pytest is the canonical framework. Each bug fix or feature requires at least one new `test_*` case near related functionality, using tmp_path and lightweight SCP headers as shown in `tests/test_exceptions.py`. Maintain deterministic expectations instead of probabilistic flux data, and include regression tests for CLI error handling or layout parsing. While no numeric coverage threshold is enforced, aim to keep new modules shipped with both positive and negative cases.

## Commit & Pull Request Guidelines
Git history favors short, imperative summaries (“Add custom exceptions…”) followed by optional detail in the body. Reference issues or docs when relevant and describe observable changes (new CLI flag, new layout). Pull requests should include: context on the preservation scenario, CLI transcripts or sample outputs when UI changes occur, and notes about backward compatibility. Ensure CI-critical commands (`pytest`, targeted fluxctl invocations) pass locally before requesting review, and update README or layout JSON when behavior changes.
