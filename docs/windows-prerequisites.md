# Windows prerequisites

This page describes what to install before running Fluxctl from a source
checkout. The short version is:

- **Fluxctl CLI and Fluxctl Studio:** Git and Python 3.11 or newer.
- **Greaseweazle integration:** Microsoft C++ Build Tools.
- **Rust native acceleration:** Rust and Microsoft C++ Build Tools.
- **HxCFE integration:** MSYS2/MinGW or a prebuilt `hxcfe.exe`.

Greaseweazle, HxCFE, and Rust acceleration are optional. Fluxctl and Studio
remain usable without them.

## 1. Required software

### Git for Windows

Install Git from <https://git-scm.com/install/windows>. The standard installer
defaults are suitable. Alternatively, install it from PowerShell:

```powershell
winget install --id Git.Git -e --source winget
```

Open a new PowerShell window and verify:

```powershell
git --version
```

### Python 3.11 or newer

Install a 64-bit Python from <https://www.python.org/downloads/windows/>. The
Python install manager and the regular x64 or ARM64 installers are supported.
Make sure the `py` command is available, then verify:

```powershell
py -3 --version
py -3 -c "import sysconfig; print(sysconfig.get_platform())"
```

The second command reports the Python **process** architecture:

- `win-amd64` means x64 Python.
- `win-arm64` means native ARM64 Python.

On Windows ARM64, x64 Python can run under emulation. This is supported, but all
native DLLs loaded by Python must also be x64.

## 2. Optional native build tools

### Microsoft C++ Build Tools

Install the Microsoft C++ Build Tools from
<https://visualstudio.microsoft.com/visual-cpp-build-tools/>. In the Visual
Studio Installer, select:

- **Desktop development with C++**
- The current MSVC C++ build tools
- A Windows SDK
- ARM64 C++ tools as well, if using native ARM64 Python

These tools are needed to build the Greaseweazle C extension and the optional
Rust DLL. After installation, use the Native Tools prompt matching Python:

- `win-amd64`: **x64 Native Tools Command Prompt for VS**
- `win-arm64`: **ARM64 Native Tools Command Prompt for VS**

Verify that the Microsoft linker is available:

```cmd
where link
```

### Rust

Install Rust using the official installer at <https://rustup.rs/>. Open a new
Native Tools prompt and verify:

```cmd
rustc -V
cargo -V
rustup show
```

The Fluxctl installer can select and install the Rust target matching the
virtual environment's Python automatically. Use `--build-native` as shown
below. Do not select a target using `platform.machine()` on Windows ARM64,
because it can report the host architecture rather than the emulated Python
process architecture.

### HxCFE build tools

HxCFE is optional and uses GNU Make/GCC-style build files. Either:

- Install MSYS2 from <https://www.msys2.org/>, open an MSYS2 MinGW64 shell, and
  install `base-devel`, the MinGW toolchain, Git, and Make; or
- Obtain a trusted prebuilt `hxcfe.exe` and pass its path to Fluxctl with
  `--hxcfe C:\path\to\hxcfe.exe`.

The source-checkout installer prints the exact MSYS2 commands when
`--build-hxcfe` is requested but Make is unavailable.

## 3. Install Fluxctl

From PowerShell:

```powershell
git clone https://github.com/GeoKM/fluxctl.git
cd fluxctl
py -3 scripts\install_fluxctl.py --yes --greaseweazle --clone-greaseweazle --clone-hxcfe --build-hxcfe --build-native
```

For only Fluxctl CLI and Studio, without optional integrations:

```powershell
py -3 scripts\install_fluxctl.py --yes --no-greaseweazle
```

The installer creates `.venv` inside the checkout and does not modify the
system Python installation.

## 4. Verify the installation

```powershell
.venv\Scripts\fluxctl.exe doctor
.venv\Scripts\fluxctl.exe --help
.venv\Scripts\fluxctl-studio.exe
```

`doctor` should report the core Fluxctl checks as `OK`. Warnings for
Greaseweazle, HxCFE, or native acceleration only mean that the corresponding
optional feature is unavailable.

If native acceleration is not loadable, `doctor` reports the Python process
architecture, DLL architecture, expected Rust target, candidate DLL paths, and
the Windows loader error. Follow the target-specific command in its suggestion.

## 5. Common Windows problems

- **`python` is not recognized:** use `py -3`, or reinstall Python with the
  Python install manager.
- **`link.exe` is missing:** install the Desktop development with C++ workload
  and run from the matching Native Tools prompt.
- **DLL architecture mismatch:** rebuild with `--build-native`. An x64 Python
  cannot load an ARM64 DLL, even on an ARM64 computer.
- **Missing VC runtime:** install the current Microsoft Visual C++
  Redistributable from
  <https://visualstudio.microsoft.com/downloads/#microsoft-visual-c-redistributable-for-visual-studio-2022>.
- **HxCFE cannot build:** run the build in an MSYS2 MinGW shell, or use a
  prebuilt `hxcfe.exe`.

For creating distributable executables or an installer, see
[Packaging and distribution](packaging.md#standalone-builds).
