# Packaging and Distribution

Fluxctl ships two command entry points from the same Python package:

- `fluxctl` for CLI workflows.
- `fluxctl-studio` for the optional PySide6 GUI.

## Recommended Release Layers

1. Publish Python wheels and source distributions for technical users.
2. Build platform-specific standalone bundles for users who do not want to
   manage Python directly.
3. Keep Greaseweazle and HxCFE optional and discoverable through `fluxctl doctor`.

## Source Checkout Installer

For testers using a checkout, run:

```bash
python3 scripts/install_fluxctl.py
```

The installer creates `.venv`, offers GUI dependencies, offers Greaseweazle
support dependencies, checks for `hxcfe`, and prints the resulting command
paths. It never modifies system Python.

On Debian/Ubuntu minimal installs, Python venv support may be split into OS
packages. If the installer reports that pip or ensurepip is unavailable, run:

```bash
sudo apt install python3-venv python3-pip
python3 scripts/install_fluxctl.py --recreate-venv
```

Non-interactive examples:

```bash
python3 scripts/install_fluxctl.py --yes --gui
python3 scripts/install_fluxctl.py --gui --greaseweazle --hxcfe /path/to/hxcfe
python3 scripts/install_fluxctl.py --gui --greaseweazle --clone-greaseweazle
python3 scripts/install_fluxctl.py --clone-hxcfe --build-hxcfe
python3 scripts/install_fluxctl.py --no-gui --no-greaseweazle
```

Greaseweazle support has two levels:

- `fluxctl[greaseweazle]` installs Fluxctl's Python support dependencies only.
- If a sibling `../greaseweazle` or `../Greaseweazle` checkout exists, the
  installer can install the actual Greaseweazle package editable.
- Use `--clone-greaseweazle` to let the installer clone
  `https://github.com/keirf/Greaseweazle.git` into `../greaseweazle` first.

If `fluxctl doctor` still reports `greaseweazle: optional package not
importable` after installing `.[greaseweazle]`, install Greaseweazle itself into
the same venv:

```bash
git clone https://github.com/keirf/Greaseweazle.git ../greaseweazle
.venv/bin/python -m pip install -e ../greaseweazle
```

HxCFE remains an external optional binary. The installer checks `--hxcfe`,
`PATH`, and a sibling `../HxCFloppyEmulator` checkout for a built `hxcfe`.
Use `--clone-hxcfe` to clone `https://github.com/jfdelnero/HxCFloppyEmulator.git`
into `../HxCFloppyEmulator`, and `--build-hxcfe` to run `make` in the first
known HxCFE build directory.

Manual HxCFE setup:

```bash
git clone https://github.com/jfdelnero/HxCFloppyEmulator.git ../HxCFloppyEmulator
make -C ../HxCFloppyEmulator/HxCFloppyEmulator_cmdline/build
```

## Python Package Artifacts

Install packaging tools:

```bash
python -m pip install -e ".[packaging]"
```

Build wheel and source distribution:

```bash
python scripts/build_packages.py
```

Artifacts are written under `dist/`.

Users can install from a wheel:

```bash
python -m pip install "fluxctl-<version>-py3-none-any.whl"
python -m pip install "fluxctl[gui]"
```

## Standalone Builds

Standalone builds are platform-specific and should be produced on each target
OS:

```bash
python -m pip install -e ".[gui,packaging]"
python scripts/build_packages.py --no-python --standalone-cli --standalone-gui
```

Outputs are written under `dist/` by PyInstaller.

Notes:

- macOS `.app` bundles should eventually be signed and notarized before public
  distribution.
- Windows `.exe` artifacts should be built on Windows.
- Linux artifacts should be built on a conservative Linux base image.
- The optional Rust native decoder library is not yet bundled into wheels or
  standalone apps. Fluxctl falls back to the Python decoder when the native
  library is absent.

## Optional Integration Policy

Packaging should not make Greaseweazle, HxCFE, or Rust native acceleration hard
requirements. Instead:

- `fluxctl doctor` reports what is available.
- Installer/build docs explain how to add optional helpers.
- GUI workflows should remain usable without optional helpers.
