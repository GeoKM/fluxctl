import pytest

from fluxctl.native import gcr_intervals_to_bits, is_native_available, mfm_intervals_to_bits


pytestmark = pytest.mark.skipif(
    not is_native_available(), reason="optional native library is not built"
)


def test_native_mfm_intervals_to_bits_matches_python_shape() -> None:
    bits = mfm_intervals_to_bits([4000, 8000, 12000], 4000.0, 64)

    assert bits == bytes([1, 0, 1, 0, 0, 1])


def test_native_gcr_intervals_to_bits_matches_python_shape() -> None:
    bits = gcr_intervals_to_bits([4000, 8000, 4000], 4000.0)

    assert bits == bytes([1, 0, 1, 1])
