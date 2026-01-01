# Plugin development

1. Implement the protocol (Decoder, Filesystem, or Exporter).
2. Register with `registry.register_*` in `fluxctl.plugins`.
3. Provide metadata via `PluginInfo`.

Builtin plugins live under `src/fluxctl/decoding`, `filesystems`, and `exporters`.
