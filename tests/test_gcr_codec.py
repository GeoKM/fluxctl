from fluxctl.encoding.gcr import (
    GCR_ENCODE_4TO5,
    decode_gcr_symbols_to_bytes,
    extract_gcr_symbols_from_bitstream,
)


def _encode_bytes_to_symbols(payload: bytes) -> list[int]:
    symbols: list[int] = []
    for byte in payload:
        hi = byte >> 4
        lo = byte & 0x0F
        symbols.extend([GCR_ENCODE_4TO5[hi], GCR_ENCODE_4TO5[lo]])
    return symbols


def test_gcr_round_trip_symbols_to_bytes():
    payload = bytes([0x00, 0xFF, 0x12, 0xAB, 0x5C])
    symbols = _encode_bytes_to_symbols(payload)
    bits: list[int] = []
    for symbol in symbols:
        bits.extend([(symbol >> shift) & 1 for shift in range(4, -1, -1)])
    extracted = extract_gcr_symbols_from_bitstream(bits, 0, len(symbols))
    decoded, errors = decode_gcr_symbols_to_bytes(extracted)
    assert decoded == payload
    assert errors == 0


def test_gcr_invalid_symbol_counts_error():
    # 0b00000 is not a valid GCR code
    symbols = [0b01010, 0b00000]
    decoded, errors = decode_gcr_symbols_to_bytes(symbols)
    assert decoded == bytes([0x00])
    assert errors == 1
