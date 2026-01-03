from fluxctl.decoding.gcr import GCRDecoder, cell_ns_for_1541_track
from fluxctl.models import RevolutionFlux


def test_gcr_decoder_emits_bits():
    decoder = GCRDecoder()
    # intervals chosen so that each bit cell maps to a single 1, forming known codes
    rev = RevolutionFlux(index=0, interval_ns=[4000] * 25)
    bitstream = decoder.decode_revolution(rev)
    assert bitstream.bits, "decoder should emit bits"
    assert bitstream.metrics.confidence > 0.0


def test_gcr_decoder_applies_speed_zones() -> None:
    assert cell_ns_for_1541_track(1) == 3250.0
    assert cell_ns_for_1541_track(17) == 3250.0
    assert cell_ns_for_1541_track(18) == 3500.0
    assert cell_ns_for_1541_track(24) == 3500.0
    assert cell_ns_for_1541_track(25) == 3750.0
    assert cell_ns_for_1541_track(30) == 3750.0
    assert cell_ns_for_1541_track(31) == 4000.0

    decoder = GCRDecoder()
    decoder.set_track(0)
    assert decoder.cell_ns == 3250.0
    decoder.set_track(30)
    assert decoder.cell_ns == 4000.0
