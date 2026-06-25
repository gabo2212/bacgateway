from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

@dataclass(frozen=True)
class OtaDeviceIdentity:
    eui64: Optional[str] = None
    short_addr: Optional[int] = None
    device_label: Optional[str] = None

@dataclass(frozen=True)
class OtaPointEvent:
    identity: OtaDeviceIdentity
    prefix: int
    code: int
    canonical_point: str
    kind: str
    value: float | int
    enum_label: Optional[str] = None
    timestamp: Optional[float] = None
    source: Optional[str] = None
    channel: Optional[int] = None
    rssi_dbm: Optional[int] = None
    lqi: Optional[int] = None

@dataclass(frozen=True)
class OtaAckEvent:
    identity: OtaDeviceIdentity
    prefix: int
    code: int
    canonical_point: str
    extra_hex: Optional[str] = None
    timestamp: Optional[float] = None
    source: Optional[str] = None
    channel: Optional[int] = None
    rssi_dbm: Optional[int] = None
    lqi: Optional[int] = None

@dataclass(frozen=True)
class OtaUnknownEvent:
    identity: OtaDeviceIdentity
    prefix: Optional[int]
    code: Optional[int]
    raw_rest_hex: str
    reason: Optional[str] = None
    timestamp: Optional[float] = None
    source: Optional[str] = None
    channel: Optional[int] = None
    rssi_dbm: Optional[int] = None
    lqi: Optional[int] = None
