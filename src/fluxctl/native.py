"""Optional native acceleration for decoder hot paths."""
from __future__ import annotations

from array import array
import ctypes
import os
from pathlib import Path
import platform
from typing import Optional, Sequence


class _NativeBuffer(ctypes.Structure):
    _fields_ = [
        ("ptr", ctypes.c_void_p),
        ("len", ctypes.c_size_t),
        ("cap", ctypes.c_size_t),
    ]


class _NativeU32Buffer(ctypes.Structure):
    _fields_ = [
        ("ptr", ctypes.c_void_p),
        ("len", ctypes.c_size_t),
        ("cap", ctypes.c_size_t),
    ]


_LIB = None
_LOAD_ATTEMPTED = False


def _library_filename() -> str:
    system = platform.system()
    if system == "Darwin":
        return "libfluxctl_native.dylib"
    if system == "Windows":
        return "fluxctl_native.dll"
    return "libfluxctl_native.so"


def _candidate_paths() -> list[Path]:
    env_path = os.environ.get("FLUXCTL_NATIVE_PATH")
    paths = [Path(env_path)] if env_path else []
    root = Path(__file__).resolve().parents[2]
    filename = _library_filename()
    paths.extend(
        [
            root / "native" / "fluxctl_native" / "target" / "release" / filename,
            root / "native" / "fluxctl_native" / "target" / "debug" / filename,
        ]
    )
    return paths


def _load_library():
    global _LIB, _LOAD_ATTEMPTED
    if os.environ.get("FLUXCTL_DISABLE_NATIVE") == "1":
        return None
    if _LOAD_ATTEMPTED:
        return _LIB
    _LOAD_ATTEMPTED = True

    for path in _candidate_paths():
        if not path.exists():
            continue
        try:
            lib = ctypes.CDLL(str(path))
        except OSError:
            continue
        lib.fluxctl_free_buffer.argtypes = [
            ctypes.c_void_p,
            ctypes.c_size_t,
            ctypes.c_size_t,
        ]
        lib.fluxctl_free_buffer.restype = None
        lib.fluxctl_free_u32_buffer.argtypes = [
            ctypes.c_void_p,
            ctypes.c_size_t,
            ctypes.c_size_t,
        ]
        lib.fluxctl_free_u32_buffer.restype = None
        lib.fluxctl_parse_scp_flux_bytes.argtypes = [
            ctypes.POINTER(ctypes.c_uint8),
            ctypes.c_size_t,
            ctypes.c_double,
            ctypes.POINTER(_NativeU32Buffer),
        ]
        lib.fluxctl_parse_scp_flux_bytes.restype = ctypes.c_int
        lib.fluxctl_mfm_intervals_to_bits.argtypes = [
            ctypes.POINTER(ctypes.c_uint32),
            ctypes.c_size_t,
            ctypes.c_double,
            ctypes.c_size_t,
            ctypes.POINTER(_NativeBuffer),
        ]
        lib.fluxctl_mfm_intervals_to_bits.restype = ctypes.c_int
        lib.fluxctl_mfm_decode_best.argtypes = [
            ctypes.POINTER(ctypes.c_uint32),
            ctypes.c_size_t,
            ctypes.POINTER(ctypes.c_double),
            ctypes.c_size_t,
            ctypes.c_size_t,
            ctypes.POINTER(_NativeBuffer),
            ctypes.POINTER(ctypes.c_double),
            ctypes.POINTER(ctypes.c_size_t),
        ]
        lib.fluxctl_mfm_decode_best.restype = ctypes.c_int
        lib.fluxctl_gcr_intervals_to_bits.argtypes = [
            ctypes.POINTER(ctypes.c_uint32),
            ctypes.c_size_t,
            ctypes.c_double,
            ctypes.c_double,
            ctypes.POINTER(_NativeBuffer),
        ]
        lib.fluxctl_gcr_intervals_to_bits.restype = ctypes.c_int
        _LIB = lib
        return _LIB
    return None


def is_native_available() -> bool:
    """Return whether the optional native library can be loaded."""

    return _load_library() is not None


def _interval_array(intervals_ns: Sequence[int]) -> array:
    if isinstance(intervals_ns, array) and intervals_ns.typecode == "I":
        return intervals_ns
    return array("I", (max(0, int(value)) for value in intervals_ns))


def _take_buffer(lib, buffer: _NativeBuffer) -> bytes:
    if not buffer.ptr or buffer.len == 0:
        return b""
    try:
        return ctypes.string_at(buffer.ptr, buffer.len)
    finally:
        lib.fluxctl_free_buffer(buffer.ptr, buffer.len, buffer.cap)


def _take_u32_buffer(lib, buffer: _NativeU32Buffer) -> array:
    intervals = array("I")
    if not buffer.ptr or buffer.len == 0:
        return intervals
    try:
        data = ctypes.string_at(buffer.ptr, buffer.len * ctypes.sizeof(ctypes.c_uint32))
        intervals.frombytes(data)
        return intervals
    finally:
        lib.fluxctl_free_u32_buffer(buffer.ptr, buffer.len, buffer.cap)


def parse_scp_flux_bytes(flux_bytes: bytes, timebase_ns: float) -> Optional[array]:
    """Return parsed SCP flux intervals, or ``None`` without native support."""

    lib = _load_library()
    if lib is None:
        return None
    buffer = _NativeU32Buffer()
    payload = ctypes.c_char_p(flux_bytes)
    status = lib.fluxctl_parse_scp_flux_bytes(
        ctypes.cast(payload, ctypes.POINTER(ctypes.c_uint8)),
        len(flux_bytes),
        float(timebase_ns),
        ctypes.byref(buffer),
    )
    if status != 0:
        return None
    return _take_u32_buffer(lib, buffer)


def mfm_intervals_to_bits(
    intervals_ns: Sequence[int], cell_ns: float, max_cells: int
) -> Optional[bytes]:
    """Return native MFM bitcells, or ``None`` when native support is unavailable."""

    lib = _load_library()
    if lib is None:
        return None
    intervals = _interval_array(intervals_ns)
    buffer = _NativeBuffer()
    status = lib.fluxctl_mfm_intervals_to_bits(
        ctypes.cast(intervals.buffer_info()[0], ctypes.POINTER(ctypes.c_uint32)),
        len(intervals),
        float(cell_ns),
        int(max_cells),
        ctypes.byref(buffer),
    )
    if status != 0:
        return None
    return _take_buffer(lib, buffer)


def mfm_decode_best(
    intervals_ns: Sequence[int], candidates: Sequence[float], max_cells: int
) -> Optional[tuple[bytes, float, int]]:
    """Return the best native MFM bitstream, PLL score, and sync count."""

    lib = _load_library()
    if lib is None:
        return None
    intervals = _interval_array(intervals_ns)
    candidate_array = (ctypes.c_double * len(candidates))(*[float(value) for value in candidates])
    buffer = _NativeBuffer()
    pll_lock = ctypes.c_double()
    sync_count = ctypes.c_size_t()
    status = lib.fluxctl_mfm_decode_best(
        ctypes.cast(intervals.buffer_info()[0], ctypes.POINTER(ctypes.c_uint32)),
        len(intervals),
        candidate_array,
        len(candidate_array),
        int(max_cells),
        ctypes.byref(buffer),
        ctypes.byref(pll_lock),
        ctypes.byref(sync_count),
    )
    if status != 0:
        return None
    return _take_buffer(lib, buffer), float(pll_lock.value), int(sync_count.value)


def gcr_intervals_to_bits(
    intervals_ns: Sequence[int], cell_ns: float, lowpass_ns: float = 2000.0
) -> Optional[bytes]:
    """Return native GCR bitcells, or ``None`` when native support is unavailable."""

    lib = _load_library()
    if lib is None:
        return None
    intervals = _interval_array(intervals_ns)
    buffer = _NativeBuffer()
    status = lib.fluxctl_gcr_intervals_to_bits(
        ctypes.cast(intervals.buffer_info()[0], ctypes.POINTER(ctypes.c_uint32)),
        len(intervals),
        float(cell_ns),
        float(lowpass_ns),
        ctypes.byref(buffer),
    )
    if status != 0:
        return None
    return _take_buffer(lib, buffer)


__all__ = [
    "gcr_intervals_to_bits",
    "is_native_available",
    "mfm_decode_best",
    "mfm_intervals_to_bits",
    "parse_scp_flux_bytes",
]
