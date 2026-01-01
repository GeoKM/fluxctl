from pathlib import Path

from fluxctl.decoding.mfm import mfm_decoder
from fluxctl.scp import parse_scp

FIXTURE_PATH = Path(
    "tests/fixtures/5.25inch/IBM/IBM-Generic-SSDD-MFM-IBMPC-180K.scp"
)


def test_mfm_decoder_returns_bits() -> None:
    image = parse_scp(FIXTURE_PATH)
    assert image.tracks, "Expected at least one track from fixture"
    rev = image.tracks[0].revolutions[0]
    bitstream = mfm_decoder.decode_revolution(rev)
    assert bitstream.bits, "Decoder should yield a non-empty bitstream"
    assert 0 in bitstream.bits and 1 in bitstream.bits
