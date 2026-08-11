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

The easiest checkout install is:

```bash
git clone https://github.com/GeoKM/fluxctl.git
cd fluxctl
python3 scripts/install_fluxctl.py --yes --greaseweazle --clone-greaseweazle --clone-hxcfe --build-hxcfe
.venv/bin/fluxctl doctor
```

On Windows PowerShell, use the Python launcher and Windows script paths:

See [Windows prerequisites](windows-prerequisites.md) for a complete required
and optional software checklist, official download links, architecture
selection, and verification commands.

```powershell
git clone https://github.com/GeoKM/fluxctl.git
cd fluxctl
py -3 scripts\install_fluxctl.py --yes --greaseweazle --clone-greaseweazle --clone-hxcfe --build-hxcfe --build-native
.venv\Scripts\fluxctl.exe doctor
.venv\Scripts\fluxctl.exe --help
.venv\Scripts\fluxctl-studio.exe
```

On Windows, Git and Python are enough for Fluxctl itself. Optional helper builds
need extra native build tools:

- Greaseweazle builds a C extension. Install Microsoft C++ Build Tools 14.0 or
  newer with the "Desktop development with C++" workload:
  `https://visualstudio.microsoft.com/visual-cpp-build-tools/`
- HxCFE uses GNU Make/GCC-style Makefiles. Build it from an MSYS2/MinGW64 shell
  after installing build tools, for example:
  `pacman -S --needed base-devel mingw-w64-x86_64-toolchain git make`
- If you already have a prebuilt `hxcfe.exe`, skip `--build-hxcfe` and pass
  `--hxcfe C:\path\to\hxcfe.exe`.
- Rust native acceleration is optional but needs Rust plus the MSVC linker.
  Install Rust from `https://rustup.rs` and Microsoft C++ Build Tools 14.0 or
  newer with the "Desktop development with C++" workload. Pass `--build-native`
  so the installer selects the Rust target matching the virtual environment's
  Python process. An x64 Python on Windows ARM64 requires an x64 DLL.

For testers already inside a checkout, run:

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
known HxCFE build directory. The preferred build command uses the top-level
Makefile target so HxCFE's supporting libraries are built first.

Manual HxCFE setup:

```bash
git clone https://github.com/jfdelnero/HxCFloppyEmulator.git ../HxCFloppyEmulator
make -C ../HxCFloppyEmulator/build HxCFloppyEmulator_cmdline
```

On macOS, HxCFE and Greaseweazle native builds need Apple's Command Line Tools
for `clang`, `make`, and system headers:

```bash
xcode-select --install
```

Full Xcode is usually not required.

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

For a shareable Windows build, run those commands in a clean x64 or ARM64
virtual environment on the matching Windows architecture, test both generated
executables on a machine without the source checkout, and distribute the
resulting CLI executable and Studio bundle together in a versioned ZIP. Include
the matching VC runtime or document the Microsoft Visual C++ Redistributable
prerequisite. Code-sign the executables (and any bundled
`fluxctl_native.dll`) before wider distribution. A conventional installer can
wrap the same `dist/` payload with WiX, Inno Setup, or MSIX and add Start Menu
shortcuts for Studio plus an optional PATH entry for the CLI.

Notes:

- macOS `.app` bundles should eventually be signed and notarized before public
  distribution.
- Windows `.exe` artifacts should be built on Windows.
- Linux artifacts should be built on a conservative Linux base image.
- The optional Rust native decoder library is not yet bundled into wheels or
  standalone apps. Fluxctl falls back to the Python decoder when the native
  library is absent.

## Rust Native Acceleration

If `fluxctl doctor` reports that native acceleration is not built or not
loadable, build the Rust library from the checkout root:

```bash
cargo build --manifest-path native/fluxctl_native/Cargo.toml --release
```

On Windows, prefer the architecture-aware installer:

```powershell
py -3 scripts\install_fluxctl.py --build-native
```

For a manual cross-target build, use the exact target reported by
`fluxctl doctor`, for example:

```powershell
rustup target add x86_64-pc-windows-msvc
cargo build --manifest-path native\fluxctl_native\Cargo.toml --release --target x86_64-pc-windows-msvc
```

If `cargo` is missing, install Rust first. The standard cross-platform installer
is:

```bash
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh
```

Then open a new shell, or run `source "$HOME/.cargo/env"`, and retry the
`cargo build` command. On Debian/Ubuntu, `sudo apt install cargo rustc` also
works, though the distro toolchain may be older than the current `rustup`
toolchain.
On Windows, install Rust from `https://rustup.rs` and Microsoft C++ Build Tools
14.0 or newer with the "Desktop development with C++" workload. If `cargo`
reports `link.exe` is missing, run the build from Developer PowerShell for
Visual Studio or a Native Tools command prompt so the MSVC linker is on `PATH`.
Use the x64 Native Tools prompt for `x86_64-pc-windows-msvc`, and the ARM64
Native Tools prompt for `aarch64-pc-windows-msvc`. `LNK4272` means the selected
prompt and target do not match. To initialize a native ARM64 toolchain:

```cmd
"C:\Program Files (x86)\Microsoft Visual Studio\18\BuildTools\VC\Auxiliary\Build\vcvarsall.bat" arm64
```

If a built library still shows as unavailable, run `fluxctl doctor` again. It
reports the native load error for the candidate DLL, shared object, or dylib.
On Windows, errors such as "not a valid Win32 application" usually mean Python
and the Rust DLL were built for different architectures. On Windows ARM64,
`platform.machine()` can report the ARM64 host even when Python itself is x64.
Fluxctl therefore uses the Python packaging platform tag and reads the DLL PE
machine type; the doctor output names both architectures and the required Rust
target.

## Optional Integration Policy

Packaging should not make Greaseweazle, HxCFE, or Rust native acceleration hard
requirements. Instead:

- `fluxctl doctor` reports what is available.
- Installer/build docs explain how to add optional helpers.
- GUI workflows should remain usable without optional helpers.
