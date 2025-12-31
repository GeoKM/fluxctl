"""Custom exception hierarchy for fluxctl.

The MVP previously relied on generic exceptions which made it difficult for
the CLI to present user-friendly diagnostics.  Introducing a dedicated set of
exceptions allows commands to catch predictable failures (e.g. malformed SCP
files or unknown layouts) and exit cleanly while still preserving traceability
in logs.
"""
from __future__ import annotations


class FluxctlError(Exception):
    """Base class for fluxctl-specific errors."""


class SCPFormatError(FluxctlError):
    """Raised when an SCP image does not conform to the expected structure."""


class LayoutNotFoundError(FluxctlError):
    """Raised when a requested disk layout identifier is unavailable."""


class FluxDecodeError(FluxctlError):
    """Raised when flux decoding fails or produces unusable data."""


class ExportError(FluxctlError):
    """Raised when an exporter cannot produce the requested output."""


class FilesystemError(FluxctlError):
    """Raised when filesystem probing or extraction encounters an error."""

