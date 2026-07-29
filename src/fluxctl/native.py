"""Optional native acceleration for decoder hot paths."""
from __future__ import annotations

from array import array
import ctypes
import os
from pathlib import Path
import platform
import struct
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
_LOAD_ERRORS: list[str] = []


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


def native_candidate_paths() -> list[Path]:
    """Return native library paths fluxctl will try in lookup order."""

    return _candidate_paths()


def _load_library():
    global _LIB, _LOAD_ATTEMPTED, _LOAD_ERRORS
    if os.environ.get("FLUXCTL_DISABLE_NATIVE") == "1":
        _LOAD_ERRORS = ["disabled by FLUXCTL_DISABLE_NATIVE=1"]
        return None
    if _LOAD_ATTEMPTED:
        return _LIB
    _LOAD_ATTEMPTED = True
    _LOAD_ERRORS = []

    for path in _candidate_paths():
        if not path.exists():
            _LOAD_ERRORS.append(f"{path}: not found")
            continue
        try:
            lib = ctypes.CDLL(str(path))
        except OSError as exc:
            _LOAD_ERRORS.append(f"{path}: {exc}")
            continue
        try:
            _configure_library(lib)
        except AttributeError as exc:
            _LOAD_ERRORS.append(f"{path}: missing native symbol {exc}")
            continue
        _LIB = lib
        _LOAD_ERRORS = []
        return _LIB
    return None


def _configure_library(lib) -> None:
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
    lib.fluxctl_mfm_decode_auto.argtypes = [
        ctypes.POINTER(ctypes.c_uint32),
        ctypes.c_size_t,
        ctypes.c_double,
        ctypes.c_bool,
        ctypes.c_size_t,
        ctypes.POINTER(_NativeBuffer),
        ctypes.POINTER(ctypes.c_double),
        ctypes.POINTER(ctypes.c_size_t),
    ]
    lib.fluxctl_mfm_decode_auto.restype = ctypes.c_int
    lib.fluxctl_mfm_reconstruct_track.argtypes = [
        ctypes.POINTER(ctypes.c_uint8),
        ctypes.c_size_t,
        ctypes.c_size_t,
        ctypes.POINTER(_NativeBuffer),
        ctypes.POINTER(ctypes.c_size_t),
    ]
    lib.fluxctl_mfm_reconstruct_track.restype = ctypes.c_int
    lib.fluxctl_gcr_intervals_to_bits.argtypes = [
        ctypes.POINTER(ctypes.c_uint32),
        ctypes.c_size_t,
        ctypes.c_double,
        ctypes.c_double,
        ctypes.POINTER(_NativeBuffer),
    ]
    lib.fluxctl_gcr_intervals_to_bits.restype = ctypes.c_int
    lib.fluxctl_gcr_estimate_confidence.argtypes = [
        ctypes.POINTER(ctypes.c_uint8),
        ctypes.c_size_t,
        ctypes.POINTER(ctypes.c_double),
    ]
    lib.fluxctl_gcr_estimate_confidence.restype = ctypes.c_int


def is_native_available() -> bool:
    """Return whether the optional native library can be loaded."""

    return _load_library() is not None


def native_load_errors() -> list[str]:
    """Return diagnostics from the most recent native library load attempt."""

    _load_library()
    return list(_LOAD_ERRORS)


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


def mfm_decode_auto(
    intervals_ns: Sequence[int], default_cell_ns: float, auto_cell: bool, max_cells: int
) -> Optional[tuple[bytes, float, int]]:
    """Return the best native MFM bitstream with native cell-candidate estimation."""

    lib = _load_library()
    if lib is None:
        return None
    intervals = _interval_array(intervals_ns)
    buffer = _NativeBuffer()
    pll_lock = ctypes.c_double()
    sync_count = ctypes.c_size_t()
    status = lib.fluxctl_mfm_decode_auto(
        ctypes.cast(intervals.buffer_info()[0], ctypes.POINTER(ctypes.c_uint32)),
        len(intervals),
        float(default_cell_ns),
        bool(auto_cell),
        int(max_cells),
        ctypes.byref(buffer),
        ctypes.byref(pll_lock),
        ctypes.byref(sync_count),
    )
    if status != 0:
        return None
    return _take_buffer(lib, buffer), float(pll_lock.value), int(sync_count.value)


def mfm_reconstruct_track(
    bits: Sequence[int], expected_sectors: Optional[int] = None
) -> Optional[tuple[list[tuple[int, int, int, int, bytes, bool, bool]], int]]:
    """Return native MFM sector records and weak count, or ``None`` without native support."""

    lib = _load_library()
    if lib is None:
        return None
    if isinstance(bits, bytes):
        payload_bytes = bits
    else:
        payload_bytes = bytes(bits)

    buffer = _NativeBuffer()
    weak = ctypes.c_size_t()
    payload = ctypes.c_char_p(payload_bytes)
    status = lib.fluxctl_mfm_reconstruct_track(
        ctypes.cast(payload, ctypes.POINTER(ctypes.c_uint8)),
        len(payload_bytes),
        int(expected_sectors or 0),
        ctypes.byref(buffer),
        ctypes.byref(weak),
    )
    if status != 0:
        return None

    raw_records = _take_buffer(lib, buffer)
    records: list[tuple[int, int, int, int, bytes, bool, bool]] = []
    offset = 0
    header_size = struct.calcsize("<BBBBBI")
    while offset + header_size <= len(raw_records):
        c, h, r, n, flags, data_len = struct.unpack_from("<BBBBBI", raw_records, offset)
        offset += header_size
        end = offset + data_len
        if end > len(raw_records):
            return None
        records.append((c, h, r, n, raw_records[offset:end], bool(flags & 0x01), bool(flags & 0x02)))
        offset = end
    if offset != len(raw_records):
        return None
    return records, int(weak.value)


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


def gcr_estimate_confidence(bits: Sequence[int]) -> Optional[float]:
    """Return native GCR symbol confidence, or ``None`` without native support."""

    lib = _load_library()
    if lib is None:
        return None
    if isinstance(bits, bytes):
        payload = ctypes.c_char_p(bits)
        payload_len = len(bits)
    else:
        payload_bytes = bytes(bits)
        payload = ctypes.c_char_p(payload_bytes)
        payload_len = len(payload_bytes)
    confidence = ctypes.c_double()
    status = lib.fluxctl_gcr_estimate_confidence(
        ctypes.cast(payload, ctypes.POINTER(ctypes.c_uint8)),
        payload_len,
        ctypes.byref(confidence),
    )
    if status != 0:
        return None
    return float(confidence.value)


__all__ = [
    "gcr_estimate_confidence",
    "gcr_intervals_to_bits",
    "is_native_available",
    "mfm_decode_auto",
    "mfm_decode_best",
    "mfm_intervals_to_bits",
    "mfm_reconstruct_track",
    "native_load_errors",
    "parse_scp_flux_bytes",
]
