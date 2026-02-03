from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

LOGGER = logging.getLogger(__name__)


def _log_debug(event: str, **fields: object) -> None:
    if LOGGER.isEnabledFor(logging.DEBUG):
        LOGGER.debug(event, extra={"event": event, **fields})


@dataclass(frozen=True)
class MacFrame:
    pan_id: Optional[int]
    src_short: Optional[int]
    dst_short: Optional[int]
    src_ext: Optional[int]
    dst_ext: Optional[int]
    payload: bytes


def strip_tap(data: bytes, tap_len: int = 28) -> Optional[bytes]:
    if len(data) < tap_len:
        return None
    return data[tap_len:]


def parse_mac_frame(data: bytes) -> Optional[MacFrame]:
    if len(data) < 2:
        return None
    frame_control = int.from_bytes(data[0:2], "little")
    frame_type = frame_control & 0x7
    if frame_type != 0x1:
        return None
    if (frame_control >> 3) & 0x1:
        _log_debug("mac.security_enabled")
        return None

    pan_compression = (frame_control >> 6) & 0x1
    seq_suppressed = (frame_control >> 8) & 0x1
    dst_mode = (frame_control >> 10) & 0x3
    src_mode = (frame_control >> 14) & 0x3

    offset = 2
    if not seq_suppressed:
        if len(data) < offset + 1:
            return None
        offset += 1

    dst_pan: Optional[int] = None
    src_pan: Optional[int] = None
    dst_short: Optional[int] = None
    dst_ext: Optional[int] = None
    src_short: Optional[int] = None
    src_ext: Optional[int] = None

    if dst_mode != 0:
        if len(data) < offset + 2:
            return None
        dst_pan = int.from_bytes(data[offset : offset + 2], "little")
        offset += 2
        if dst_mode == 2:
            if len(data) < offset + 2:
                return None
            dst_short = int.from_bytes(data[offset : offset + 2], "little")
            offset += 2
        elif dst_mode == 3:
            if len(data) < offset + 8:
                return None
            dst_ext = int.from_bytes(data[offset : offset + 8], "little")
            offset += 8
        else:
            _log_debug("mac.dst_mode_unsupported", mode=dst_mode)
            return None

    if src_mode != 0:
        if pan_compression and dst_mode != 0:
            src_pan = dst_pan
        else:
            if len(data) < offset + 2:
                return None
            src_pan = int.from_bytes(data[offset : offset + 2], "little")
            offset += 2
        if src_mode == 2:
            if len(data) < offset + 2:
                return None
            src_short = int.from_bytes(data[offset : offset + 2], "little")
            offset += 2
        elif src_mode == 3:
            if len(data) < offset + 8:
                return None
            src_ext = int.from_bytes(data[offset : offset + 8], "little")
            offset += 8
        else:
            _log_debug("mac.src_mode_unsupported", mode=src_mode)
            return None

    pan_id = dst_pan if dst_pan is not None else src_pan
    payload = data[offset:]
    return MacFrame(
        pan_id=pan_id,
        src_short=src_short,
        dst_short=dst_short,
        src_ext=src_ext,
        dst_ext=dst_ext,
        payload=payload,
    )
