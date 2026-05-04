"""
gateway/jace_types.py — Runtime types for JACE Ethernet discovery and point reads.

Kept separate from gateway/model.py (which holds config-loading / thermostat config
types).  These dataclasses describe *runtime* observations: what was discovered on
the wire and what a point read returned.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class JacePointReadResult:
    """Result of reading a single point from a JACE device.

    Attributes:
        value:      Numeric or string value returned by the JACE, or None if
                    the read failed / the point has no value yet.
        timestamp:  Time the read completed (``time.monotonic()`` or epoch).
        quality:    Coarse quality indicator.  One of:
                    ``"ok"`` — value is fresh and trusted,
                    ``"stale"`` — value is cached but not recently confirmed,
                    ``"error"`` — last read attempt failed (see last_error),
                    ``"unknown"`` — quality not yet determined.
        last_error: Human-readable error string from the most recent failed
                    read attempt, or None if the last read succeeded.
    """

    value: float | str | None
    timestamp: float
    quality: str  # "ok" | "stale" | "error" | "unknown"
    last_error: str | None


@dataclass(frozen=True)
class JaceDiscoveryRecord:
    """Summary of what was observed when probing a JACE over Ethernet.

    Highest-value fields for Phase A3a are ``host``, ``port``,
    ``protocol_hint``, ``device_refs``, and ``timestamp``.
    ``firmware_version`` and ``model`` are optional nice-to-haves; their
    absence must not block implementation.

    Attributes:
        host:             IP or hostname of the JACE (e.g. ``"192.168.15.12"``).
        port:             HTTP port used (e.g. ``80``).
        protocol_hint:    Login/protocol scheme detected during probing
                          (e.g. ``"cookieDigest"``, ``"scram"``, or ``None``
                          if unknown).
        firmware_version: JACE firmware string if discoverable, else None.
        model:            JACE model string if discoverable, else None.
        device_refs:      Opaque device reference strings visible on this JACE
                          (e.g. ``("Device2",)``).
        timestamp:        Time the discovery record was created.
    """

    host: str
    port: int
    protocol_hint: str | None
    firmware_version: str | None
    model: str | None
    device_refs: tuple[str, ...]
    timestamp: float

