from fluxctl.decoding.gcr import GCRDecoder
from fluxctl.models import RevolutionFlux


def test_gcr_decoder_emits_bits():
    decoder = GCRDecoder()
    # intervals chosen so that each bit cell maps to a single 1, forming known codes
    rev = RevolutionFlux(index=0, interval_ns=[4000] * 25)
    bitstream = decoder.decode_revolution(rev)
    assert bitstream.bits, "decoder should emit bits"
    assert bitstream.metrics.confidence > 0.0
