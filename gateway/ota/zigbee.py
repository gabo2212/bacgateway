from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

LOGGER = logging.getLogger(__name__)


def _log_debug(event: str, **fields: object) -> None:
    if LOGGER.isEnabledFor(logging.DEBUG):
        LOGGER.debug(event, extra={"event": event, **fields})


@dataclass(frozen=True)
class NwkFrame:
    src: int
    dst: int
    payload: bytes


@dataclass(frozen=True)
class ApsFrame:
    profile_id: int
    cluster_id: int
    src_ep: int
    dst_ep: int
    payload: bytes


def parse_nwk_frame(data: bytes) -> Optional[NwkFrame]:
    if len(data) < 8:
        return None
    frame_control = int.from_bytes(data[0:2], "little")
    frame_type = frame_control & 0x3
    if frame_type != 0x0:
        return None

    security_enabled = (frame_control >> 9) & 0x1
    source_route = (frame_control >> 10) & 0x1
    dest_ieee = (frame_control >> 11) & 0x1
    src_ieee = (frame_control >> 12) & 0x1

    offset = 2
    if len(data) < offset + 4:
        return None
    dst = int.from_bytes(data[offset : offset + 2], "little")
    offset += 2
    src = int.from_bytes(data[offset : offset + 2], "little")
    offset += 2
    if len(data) < offset + 2:
        return None
    offset += 2  # radius + sequence

    if dest_ieee:
        if len(data) < offset + 8:
            return None
        offset += 8
    if src_ieee:
        if len(data) < offset + 8:
            return None
        offset += 8
    if source_route:
        if len(data) < offset + 2:
            return None
        relay_count = data[offset]
        offset += 2
        relay_bytes = relay_count * 2
        if len(data) < offset + relay_bytes:
            return None
        offset += relay_bytes

    if security_enabled:
        _log_debug("nwk.security_enabled")
        return None
    if offset > len(data):
        return None

    return NwkFrame(src=src, dst=dst, payload=data[offset:])


def parse_aps_frame(data: bytes) -> Optional[ApsFrame]:
    if len(data) < 8:
        return None
    frame_control = data[0]
    frame_type = frame_control & 0x3
    delivery_mode = (frame_control >> 2) & 0x3
    security_enabled = (frame_control >> 5) & 0x1
    extended_header = (frame_control >> 7) & 0x1

    if frame_type != 0x0 or delivery_mode != 0x0:
        _log_debug(
            "aps.unsupported_mode",
            frame_type=frame_type,
            delivery_mode=delivery_mode,
        )
        return None
    if security_enabled or extended_header:
        _log_debug("aps.flags_unsupported", security=security_enabled, extended=extended_header)
        return None

    offset = 1
    dst_ep = data[offset]
    offset += 1
    cluster_id = int.from_bytes(data[offset : offset + 2], "little")
    offset += 2
    profile_id = int.from_bytes(data[offset : offset + 2], "little")
    offset += 2
    src_ep = data[offset]
    offset += 1
    offset += 1  # APS counter
    if offset > len(data):
        return None
    return ApsFrame(
        profile_id=profile_id,
        cluster_id=cluster_id,
        src_ep=src_ep,
        dst_ep=dst_ep,
        payload=data[offset:],
    )
