# fluxctl

fluxctl is a modular toolkit for inspecting and converting floppy disk flux captures. It supports decoding flux streams, reconstructing sectors, quality control, visualization, extraction, and exporting to standard image formats.

## Getting started
The easiest source-checkout install is:

```bash
git clone https://github.com/GeoKM/fluxctl.git
cd fluxctl
python3 scripts/install_fluxctl.py --yes --greaseweazle --clone-greaseweazle --clone-hxcfe --build-hxcfe
.venv/bin/fluxctl doctor
```

On Windows PowerShell, use the Python launcher and Windows script paths:

> New Windows setup? Start with the
> [Windows prerequisites and installation guide](docs/windows-prerequisites.md)
> for required downloads, optional build tools, architecture selection, and
> verification steps.

```powershell
git clone https://github.com/GeoKM/fluxctl.git
cd fluxctl
py -3 scripts\install_fluxctl.py --yes --greaseweazle --clone-greaseweazle --clone-hxcfe --build-hxcfe --build-native
.venv\Scripts\fluxctl.exe doctor
.venv\Scripts\fluxctl.exe --help
.venv\Scripts\fluxctl-studio.exe
```

On Windows, Fluxctl itself needs Git and Python. Optional helper builds need
extra native build tools:

- Greaseweazle: install Microsoft C++ Build Tools 14.0 or newer with the
  "Desktop development with C++" workload.
- HxCFE: install a GNU Make/GCC environment such as MSYS2/MinGW64, or use a
  prebuilt `hxcfe.exe` and pass `--hxcfe C:\path\to\hxcfe.exe`.
- Rust native acceleration: install Rust from `https://rustup.rs` and Microsoft
  C++ Build Tools 14.0 or newer with the "Desktop development with C++"
  workload so the MSVC linker `link.exe` is available. `--build-native`
  detects the virtual environment's Python architecture and selects the matching
  Rust target. This matters on Windows ARM64, where an x64 Python may run under
  emulation and cannot load an ARM64 DLL.

This creates `.venv`, installs `fluxctl` and Fluxctl Studio, offers optional
Greaseweazle support, clones/builds optional HxCFE support, and prints the
installed command paths. Run `.venv/bin/fluxctl --help` to explore available
targets.

For a minimal manual install instead:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -e .
```

For an interactive source-checkout install that can also offer GUI and optional
Greaseweazle/HxCFE setup checks, run:

```bash
python3 scripts/install_fluxctl.py
```

## Supported operations
- **info**: inspect SCP headers and inferred geometry.
- **doctor**: check the local installation and optional helper integrations.
- **probe**: detect encoding, layout, and filesystem (where detectable) for SCP, WOZ, Apple II `.po`/`.do`/`.nib`, IMD, TRS-80 `.dsk`/`.dmk`, and flat images.
- **compare**: hash + byte diff two images; SCP inputs are decoded first.
- **roundtrip**: convert through an intermediate format and verify decoded-sector
  hashes after each leg.
- **qc**: generate quality control reports (JSON or text).
- **visualize**: render ASCII or SVG disk maps.
- **extract**: detect filesystems and extract files or raw sectors.
- **convert**: export to raw, IMD, ADF, D64, D71, D81, G64, Apple II PO/DO,
  and deterministic synthetic SCP images.
- **sectors/dump/patch**: per-track listing, hex dump, and simple patching helpers.
- **studio**: optional desktop GUI for guided and advanced workflows.

## Encodings and filesystems
- Encodings: MFM, FM, Commodore GCR, and Apple II 16-sector 6-and-2 GCR via plugin registry.
- Physical layouts include IBM PC FAT geometries, IBM XDF OS/2 media,
  Commodore/Amiga formats, CP/M machine formats, Tandy TRS-80 formats, RT-11
  RX01/RX02 RT-11 media, IBM DisplayWriter mixed-sector media, and Apple II
  35-track 140K media in WOZ, NIB, PO, DO, DSK/IMG, or decoded SCP form.
- Filesystems detected: FAT12, CBM DOS, Apple ProDOS, Apple DOS 3.3, CP/M
  (C64 CP/M 2.2, C128 CP/M 3.0, Osborne/Kaypro/Tandy variants), TRSDOS 1.3,
  LDOS/TRSDOS 6, NEWDOS/80, Amiga OFS/FFS, RT-11 (normal RX02 volumes and
  RX01/IBM 3740 Interchange labels), Displaywriter probe, read-only Seiko 8300
  EBCDIC catalog/dataset readers, and read-only Wang OIS installation-package
  catalogs; raw sector dumps always supported.
- Filesystem listing, extraction, export, and copy-only mutation support varies
  by format. See the
  [filesystem capability matrix](docs/filesystem_capabilities.md) for current
  limitations.

## Usage examples

### Human-readable inspection

```bash
fluxctl doctor
fluxctl info disk.scp
fluxctl probe disk.scp
fluxctl qc disk.scp --layout ibm_mfm_720k
fluxctl extract disk.img --list
```

### Machine-readable reports

Use explicit JSON options when the result will be consumed by another program
or retained as a regression artifact:

```bash
fluxctl doctor --json
fluxctl qc disk.scp --layout ibm_mfm_720k --json-out qc.json
fluxctl compare before.img after.img --json-out diff.json
fluxctl roundtrip disk.scp --layout amiga_mfm_880k --to adf \\
  --json-out roundtrip.json
fluxctl recover disk.scp --layout ibm_mfm_720k --policy best-effort \\
  --out repaired.img --manifest recovery.json
```

### Comparison, recovery, and conversion

```bash

# Compare two images (SCP decoded on the fly)
fluxctl compare a.scp b.img --json-out diff.json
fluxctl compare before.img after.img

# Verify conversion losslessness through decoded sector hashes. Round-trip
# checks compare reconstructed sector bytes, physical sector metadata, and
# readable filesystem file hashes, not raw flux timing bytes.
fluxctl roundtrip amiga.scp --layout amiga_mfm_880k --to adf --json-out roundtrip.json
fluxctl roundtrip disk.adf --to raw --back-to adf --work-dir /tmp/fluxctl-roundtrip

# The report separates data, logical geometry, and preservation equivalence.

# Recover competing SCP revolutions into a new image. The source is never
# modified; the decision manifest is written beside the repaired image.
fluxctl recover damaged.scp --layout ibm_mfm_720k --policy strict-crc \
  --out damaged-recovered.img

# Generate a calibrated SCP via Greaseweazle's selected format encoder. This
# is useful for hardware writing and sector-level round-trip checking, but it
# cannot recreate the original capture's analogue timing or protection data.
fluxctl synthesize-scp disk.img --format ibm.720 --out disk-synthesized.scp
fluxctl compare disk.img disk-synthesized.scp \
  --layout-a ibm_mfm_720k --layout-b ibm_mfm_720k

# Destructive hardware write: Greaseweazle verifies the write, Fluxctl then
# captures a new raw SCP and compares decoded sectors. Source images are never
# modified and the JSON manifest records both commands and results.
fluxctl write disk.img --format ibm.720 --layout ibm_mfm_720k \
  --readback-out disk-readback.scp --confirm-write

# Quality reports and maps
fluxctl qc disk.scp --json-out qc.json
fluxctl visualize disk.scp --format ascii --out map.txt

# Export / convert
fluxctl convert disk.scp --to raw --out disk.img
fluxctl convert disk.scp --to raw --out disk.img --layout ibm_mfm_720k
fluxctl convert disk.img --to imd --out disk.imd --layout ibm_mfm_720k
fluxctl convert trs80.dsk --to imd --out trs80.imd
fluxctl convert trs80.dmk --to imd --out trs80.imd
fluxctl convert c64.scp --to d64 --out disk.d64
fluxctl convert c64.scp --to g64 --out disk.g64 --layout commodore_gcr_1541_170k
fluxctl convert c128.scp --to d71 --out disk.d71 --layout commodore_gcr_1571_341k
fluxctl convert 1581.scp --to d81 --out disk.d81 --layout commodore_mfm_1581_800k
fluxctl convert disk.d81 --to raw --out disk.img
fluxctl convert disk.img --to raw --out copy.img
fluxctl convert disk.adf --to scp --out disk.scp
fluxctl convert disk.img --layout ibm_mfm_720k --to scp --out disk.scp
fluxctl roundtrip disk.adf --to scp --back-to adf
fluxctl roundtrip disk.img --layout ibm_mfm_720k --to scp --back-to raw

fluxctl extract disk.img --list
fluxctl extract disk.img --path FILE.TXT --out output.bin
fluxctl extract disk.scp --layout ibm_mfm_720k --path README.TXT --out readme.bin
```

For Commodore layouts, QC JSON/text reports and Studio map details also include
conservative CBM DOS command-channel codes inferred from decoded sectors. An
unrecovered expected sector is reported as `22 READ ERROR (data block not
present)`, and recovered data with a failed checksum is reported as `23 READ
ERROR (checksum error in data block)`. These codes describe the closest CBM
DOS read diagnosis; the original drive may have reported `20`/`21`/`24`/`27`
depending on whether the failure occurred in the header, sync, or byte
decoder. Write-protect, write-verify, long-block, and disk-ID errors cannot be
proven from a read-only image and are never guessed.

### Sector inspection and patching

```bash
# Per-track inspection
fluxctl sectors disk.scp --track 0 --head 0 --encoding mfm
fluxctl dump disk.scp --layout ibm_mfm_720k --track 0 --side 0 --sector 1

# Patch one full sector and export a raw image
fluxctl patch disk.scp --layout ibm_mfm_720k --write-sector 0:0:1:DEADBEEF... --out patched.img
```

Conversion targets are checked against the resolved layout, encoding, geometry,
and container semantics before an exporter runs. The CLI and Fluxctl Studio use
the same compatibility planner. A route is reported as `sector-lossless`,
`logically-equivalent`, or `lossy-but-useful`; incompatible targets are rejected
before an output file is created. A sector-equivalent conversion may still lose
physical flux timing, track encoding, weak bits, or copy-protection details.

### Safe output handling

Commands that create user-visible files refuse to overwrite an existing path
by default. `compare`, `qc`, `visualize`, `convert`, `roundtrip`, `extract`, and
`patch` accept `--force` when replacement is intentional. Fluxctl writes each
file to a temporary file in the destination directory and publishes it only
after the complete output has been flushed, so an interrupted write does not
leave a partially written destination.

Fluxctl also checks all known outputs, including provenance and patch-log
sidecars, before starting a command. An input image can never be used as its own
output path, even with `--force`.

### Health checks and troubleshooting

Use `fluxctl doctor` when a conversion, decode, or optional helper path behaves
unexpectedly. It reports:

- Python and fluxctl versions.
- Loaded layouts, decoders, exporters, and filesystem readers.
- Whether optional Rust native decoder acceleration is enabled, disabled, or not
  built yet.
- Whether the optional Greaseweazle Python package can be imported.
- Whether `hxcfe` is available on `PATH`, at a supplied `--hxcfe` path, or in a
  sibling `../HxCFloppyEmulator` checkout built by the installer.

Examples:
```bash
fluxctl doctor
fluxctl doctor --json
fluxctl doctor --hxcfe ~/src/HxCFloppyEmulator/HxCFloppyEmulator_cmdline/build/hxcfe
```

Warnings are informational for optional features. A missing native library only
means fluxctl will use the pure-Python decoder path. A failed `hxcfe` check only
matters when you explicitly want HxC-assisted hints.

## Fluxctl Studio GUI

Fluxctl Studio is an optional desktop interface for the same core operations as
the CLI. It is designed around two workflows:

- **Simple Mode**: open an image, run doctor/probe/QC, render a disk map, list
  filesystem files, inspect file/sector hex, export files, replace supported
  files into a new image copy, create common blank disk images, and convert
  common output formats with fewer choices.
- **Advanced Mode**: expose layout, encoding, track/head/sector, compare,
  sector listing, hex dump, conversion, and provenance inspection controls.

Install the GUI dependency and launch it:
```bash
.venv/bin/python -m pip install -e ".[gui]"
.venv/bin/fluxctl-studio
```

Studio calls the shared fluxctl application and domain operations directly; it
does not drive the CLI through subprocess command paths. This keeps the GUI and
CLI on the same conversion, filesystem, output-safety, provenance, and recovery
rules while allowing each frontend to present results in its own workflow.

Long-running Studio operations are shown in the Jobs & Logs tab with an elapsed
time indicator and a cancellation control. Cancellation is cooperative: an
operation that is already inside a non-interruptible decoder may finish in the
background, but its result is discarded. Starting a newer operation or opening
another image likewise prevents an older result from replacing current-screen
data. Window layout, mode, map view, format selections, and common browsing or
Greaseweazle settings are saved with the operating system's application settings.

For SCP inputs, `convert` auto-detects the likely layout when `--layout` is not
provided. Pass `--layout` when you want to force a specific interpretation or
when a damaged/ambiguous capture cannot be identified confidently.

Native `convert --to scp` uses the same compatibility planner as Studio. It
supports standard IBM-style FM/MFM, native Amiga MFM, Commodore GCR, and Apple
II 16-sector 6-and-2 GCR layouts. Dedicated containers such as ADF, D64, D71,
and D81 resolve their layouts automatically; ambiguous flat IMG files require
`--layout`. The result is deterministic logical flux suitable for conversion,
round-trip testing, emulation, and supported hardware-writing workflows. It is
not a preservation substitute for an original capture: analogue timing,
write-splice placement, weak bits, and protection-specific patterns are not
recreated. Specialised hard-sector, XDF, RX02, MMFM, and unsupported mixed-track
formats are rejected instead of being encoded with an incorrect generic track.

File replacement is intentionally conservative. Studio always writes a new
image copy instead of modifying the original image. Current replacement support
includes FAT12 files in flat `.img` images, root-level CBM DOS files in `.d64`
and `.d71`, nested CBM DOS 1581 files in `.d81`, and same-size Amiga OFS/FFS
files in `.adf`. FAT12 replacements may grow the selected file by allocating
free clusters; CBM and Amiga operations apply their format-specific allocation
and metadata rules. Replacement for other filesystems or image containers that
would require unsupported sector rewrites is rejected until a dedicated writer
exists.

Studio also supports FAT12 `.img` file manipulation into new image copies:
delete a file or empty directory, import a file, recursively import a directory
tree, and create an empty directory. FAT12 import and directory creation
currently require 8.3-compatible ASCII names and reject overwriting existing
entries.
CBM DOS `.d64` and `.d71` images support root-level file import, replacement,
and scratch/delete in a new image copy. Import uses ASCII names up to 16
characters; `.PRG`, `.SEQ`, and `.USR` suffixes select the CBM file type and
unknown suffixes default to PRG. REL mutation remains unavailable because it
requires side-sector allocation. CBM DOS 1581 `.d81` additionally supports
nested file operations, recursive directory import, directory creation, and
empty-directory deletion.
For CBM DOS sector hex and dump controls, Studio accepts Commodore logical track
numbers: the 1541/1571 BAM is entered as track 18, head 0, sector 0.

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

## License

Fluxctl is licensed under the GNU General Public License v3.0 or later
(`GPL-3.0-or-later`). See [LICENSE](LICENSE) for the full license text.

## Optional integrations

On macOS, optional helper builds need Apple's Command Line Tools for `clang`,
`make`, and system headers:
```
xcode-select --install
```
Full Xcode is usually not required.

### Greaseweazle-assisted PLL decoding
Fluxctl can fall back to Greaseweazle’s Amiga and IBM FM/MFM codecs for higher-fidelity PLL decoding. This is **optional**; when missing, fluxctl uses its own PLL/parser.

Steps:
1. Get Fluxctl's Greaseweazle support dependencies:
   ```
   .venv/bin/pip install -e ".[greaseweazle]"
   ```
2. Install the actual Greaseweazle Python package into the same venv. The
   source-checkout installer can do this automatically if a sibling checkout
   exists, or can clone it with `--clone-greaseweazle`; otherwise clone
   Greaseweazle alongside fluxctl and install it editable:
   ```
   git clone https://github.com/keirf/Greaseweazle.git ../greaseweazle
   .venv/bin/pip install -e ../greaseweazle
   ```
No configuration is required; fluxctl will auto-detect the import at runtime when present.

### Greaseweazle synthesis and verified disk writing

With the `gw` command available, `fluxctl synthesize-scp` creates a new SCP
using Greaseweazle's selected disk format encoder. This is **calibrated logical
flux**, not a preservation-grade recreation of a capture: it cannot retain weak
bits, original timing variation, non-standard gaps, or copy protection.

This Greaseweazle-backed command remains useful when its current disk-definition
catalog has a specialised encoder. The self-contained `fluxctl convert --to
scp` path instead uses Fluxctl's native exporter and participates directly in
the standard compatibility planner, provenance, atomic-output, and `roundtrip`
workflows.

`fluxctl write` is deliberately destructive and requires `--confirm-write`.
It leaves Greaseweazle's own verification enabled, then performs a separate raw
SCP read-back for the requested number of revolutions and compares decoded
sector data using the supplied Fluxctl `--layout`. It retains the read-back SCP
and a JSON manifest containing hashes, commands, Greaseweazle output, and the
comparison result. Use a specific Greaseweazle `--format`; format-free writes
are refused.

Fluxctl Studio exposes the same flow in the **Hardware** panel. Load and probe
an image, select a Greaseweazle format, then use **Synthesize SCP...** or
**Write Disk...**. Writing requires typing `WRITE` and choosing a read-back SCP
destination.

### HxC Floppy Emulator (hxcfe) hints / conversions
HxCFE can provide layout hints and ADF conversions for Amiga and other formats.

Steps:
1. Clone and build `hxcfe` (the CLI from HxCFloppyEmulator). The
   source-checkout installer can clone it with `--clone-hxcfe` and attempt the
   Makefile build with `--build-hxcfe`; manually:
   ```
   git clone https://github.com/jfdelnero/HxCFloppyEmulator.git ../HxCFloppyEmulator
   make -C ../HxCFloppyEmulator/build HxCFloppyEmulator_cmdline
   ```
2. Point fluxctl at the binary when running commands that accept `--hxcfe`, e.g.:
   ```
   fluxctl qc disk.scp --layout amiga_mfm_880k --hxcfe ~/src/HxCFloppyEmulator/HxCFloppyEmulator_cmdline/build/hxcfe
   fluxctl probe disk.scp --hxcfe /path/to/hxcfe
   ```
HxCFE is optional; when omitted, fluxctl uses its own detectors.

### Optional native decoder acceleration
Fluxctl can load an optional Rust native library for hot flux-to-bitcell decoder
loops. The Python implementation remains the fallback when the library is not
built.

Build the native library from the repository root:
```
cargo build --manifest-path native/fluxctl_native/Cargo.toml --release
```
On Windows, the safer source-install route is
`py -3 scripts\install_fluxctl.py --build-native`; it builds for the Python
process architecture. For a manual build, use the target printed by
`fluxctl doctor`, for example:

```powershell
rustup target add x86_64-pc-windows-msvc
cargo build --manifest-path native\fluxctl_native\Cargo.toml --release --target x86_64-pc-windows-msvc
```

If `cargo` is missing, install Rust first. The standard cross-platform path is:
```
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh
```
Then open a new shell, or run `source "$HOME/.cargo/env"`, and retry the
`cargo build` command. On Debian/Ubuntu packaged Rust can also be installed with
`sudo apt install cargo rustc`, but `rustup` usually provides a newer toolchain.
On Windows, install Rust from `https://rustup.rs` and Microsoft C++ Build Tools
14.0 or newer with the "Desktop development with C++" workload. If `cargo`
reports `link.exe` is missing, run the build from Developer PowerShell for
Visual Studio or a Native Tools command prompt so the MSVC linker is on `PATH`.
Use the x64 Native Tools prompt for `x86_64-pc-windows-msvc`, or the ARM64
Native Tools prompt for `aarch64-pc-windows-msvc`. If the linker reports
`LNK4272`, the prompt and Rust target do not match. For a native ARM64 build:
```
"C:\Program Files (x86)\Microsoft Visual Studio\18\BuildTools\VC\Auxiliary\Build\vcvarsall.bat" arm64
```

Fluxctl auto-detects the resulting library under `native/fluxctl_native/target`.
Set `FLUXCTL_NATIVE_PATH=/path/to/libfluxctl_native.dylib` (or `.so`/`.dll`) to
override the lookup path. Set `FLUXCTL_DISABLE_NATIVE=1` to force the pure
Python fallback.
If a built library still shows as unavailable, `fluxctl doctor` reports the
native load error. On Windows, errors such as "not a valid Win32 application" or
`LNK4272` usually mean the Python architecture, Rust target, and Visual Studio
Native Tools prompt do not all match. Do not use `platform.machine()` to choose
the DLL architecture on Windows ARM64: it may report the host (`ARM64`) for an
emulated x64 Python. Fluxctl uses Python's `win-amd64`/`win-arm64` platform tag
and reads the DLL's PE header directly.

## Contributor guide
See [AGENTS.md](AGENTS.md) for coding standards, workflows, and review expectations.
See [docs/packaging.md](docs/packaging.md) for wheel, source distribution, and
standalone GUI/CLI packaging notes.
See [docs/windows-prerequisites.md](docs/windows-prerequisites.md) for the
Windows software checklist and installation walkthrough.

## Provenance
Commands that create output files write provenance sidecars by default:

- `convert --out disk.img` writes `disk.img.provenance.json`.
- `qc --json-out qc.json` or `--text-out qc.txt` writes a sidecar for the first report path.
- `visualize --out map.txt` writes `map.txt.provenance.json`.
- `extract --out file.bin` writes `file.bin.provenance.json`.
- `compare --json-out diff.json` writes `diff.json.provenance.json`.
- `patch --out patched.img` writes `patched.img.provenance.json` and a separate patch log.

Use `--prov-out custom.provenance.json` on commands that support it to choose
the sidecar path. Terminal-only commands such as `info`, `probe`, `sectors`,
`dump`, and `extract --list` print results but do not create sidecars unless a
file output option is used. Provenance records capture tool version, inputs,
outputs, hashes, parameters, timestamps, and decoder/exporter identifiers so
artefacts can be verified later.
