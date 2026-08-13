"""DEC RX02 encoding registration.

RX02 decoding is performed by the sector reconstructor because the format
combines FM ID fields with modified-MFM data fields.  The registry entry keeps
the format available to layout detection without pretending it is ordinary
MFM bitstream decoding.
"""
from __future__ import annotations

from ..exceptions import FluxDecodeError
from ..plugins import PluginInfo, registry


class RX02Decoder:
    encoding = "dec_rx02"

    def decode_revolution(self, rev):
        raise FluxDecodeError("RX02 requires mixed FM/MMFM sector reconstruction")


rx02_decoder = RX02Decoder()
registry.register_encoding(
    "dec_rx02",
    PluginInfo(
        name="DEC RX02 FM/MMFM Decoder",
        version="0.1",
        entry=rx02_decoder,
        description="DEC RX02 mixed FM header and modified-MFM data decoder",
    ),
)
