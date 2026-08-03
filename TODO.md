# Fluxctl TODO

## Conversion Fidelity

- Add an SCP exporter so sector images can be written back into synthetic flux
  containers. This should support round-trip validation such as
  `fluxctl roundtrip disk.adf --to scp --back-to adf` and
  `fluxctl roundtrip disk.img --layout ibm_mfm_720k --to scp --back-to raw`,
  plus Commodore 1581 validation such as
  `fluxctl roundtrip disk.d81 --to scp --back-to raw`.
  The success criterion is decoded sector equality, not byte-identical flux
  timing compared with an original hardware capture.
