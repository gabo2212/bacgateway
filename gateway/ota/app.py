from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Literal, Optional

from .zigbee import ApsFrame

LOGGER = logging.getLogger(__name__)

Direction = Literal["gw->dev", "dev->gw"]
Kind = Literal["identify_req", "identify_rsp", "analog_x10", "enum", "ack", "unknown"]
Value = float | int

PREFIX_TO_DEVICE_SHORT: dict[int, int] = {
    0x08: 0x143E,
    0x0A: 0x0001,
}

PREFIX_TO_WRITE_TO_REPORT: dict[int, dict[int, int]] = {
    0x08: {
        0x35: 0xC4,
        0x36: 0xC7,
        0x37: 0xCA,
        0x3C: 0xCD,
        0x3D: 0xD1,
        0x3E: 0xD3,
        0x3F: 0xD7,
        0x40: 0xDD,
    },
    0x0A: {
        0x29: 0xC2,
        0x2A: 0xCA,
        0x2B: 0xD3,
        0x2C: 0xDD,
        0x2D: 0xEB,
    },
}

ENUM_CODE_SETS: dict[int, set[int]] = {
    0x08: {0x3E, 0x3F, 0x40, 0xD3, 0xD7, 0xDD},
    0x0A: {0x2D, 0xEB},
}


def _log_debug(event: str, **fields: object) -> None:
    if LOGGER.isEnabledFor(logging.DEBUG):
        LOGGER.debug(event, extra={"event": event, **fields})


@dataclass(frozen=True)
class OtaMsg:
    t_rel: float
    direction: Direction
    device_short: int
    profile_id: int
    cluster_id: int
    src_ep: int
    dst_ep: int
    cmd_id: int
    prefix: Optional[int]
    code: Optional[int]
    kind: Kind
    value: Optional[Value]
    raw_rest_hex: str


def decode_cmd2_rest(rest: bytes) -> tuple[Optional[int], Optional[int], Kind, Optional[Value]]:
    prefix = rest[0] if len(rest) >= 1 else None
    code = rest[1] if len(rest) >= 2 else None
    if len(rest) != 4:
        return prefix, code, "unknown", None
    prefix = rest[0]
    code = rest[1]
    enum_codes = ENUM_CODE_SETS.get(prefix, set())
    if code in enum_codes:
        return prefix, code, "enum", int(rest[3])
    value = int.from_bytes(rest[2:4], "big") / 10.0
    return prefix, code, "analog_x10", value


def decode_cmd3_rest(rest: bytes) -> tuple[Optional[int], Optional[int], Kind, Optional[Value]]:
    prefix = rest[0] if len(rest) >= 1 else None
    code = rest[1] if len(rest) >= 2 else None
    if len(rest) == 3 and rest[2] == 0x00:
        return prefix, code, "ack", None
    return prefix, code, "unknown", None


def parse_ota_msg(
    t_rel: float, nwk_src_short: int, nwk_dst_short: int, aps_frame: ApsFrame
) -> Optional[OtaMsg]:
    payload = aps_frame.payload
    if len(payload) < 3:
        return None

    offset = 0
    frame_control = payload[offset]
    offset += 1
    if frame_control & 0x04:
        if len(payload) < offset + 2:
            return None
        offset += 2  # manufacturer code
    if len(payload) < offset + 2:
        return None
    offset += 1  # transaction seq
    cmd_id = payload[offset]
    offset += 1
    rest = payload[offset:]

    if nwk_src_short == 0x0000 and nwk_dst_short != 0x0000:
        direction: Direction = "gw->dev"
        device_short = nwk_dst_short
    elif nwk_dst_short == 0x0000 and nwk_src_short != 0x0000:
        direction = "dev->gw"
        device_short = nwk_src_short
    else:
        direction = "dev->gw"
        device_short = nwk_src_short

    prefix: Optional[int] = None
    code: Optional[int] = None
    kind: Kind = "unknown"
    value: Optional[Value] = None

    if cmd_id == 0x00:
        kind = "identify_req"
    elif cmd_id == 0x01:
        kind = "identify_rsp"
    elif cmd_id == 0x02:
        prefix, code, kind, value = decode_cmd2_rest(rest)
    elif cmd_id == 0x03:
        prefix, code, kind, value = decode_cmd3_rest(rest)
    else:
        _log_debug("ota.cmd_unsupported", cmd_id=cmd_id)
        return None

    return OtaMsg(
        t_rel=t_rel,
        direction=direction,
        device_short=device_short,
        profile_id=aps_frame.profile_id,
        cluster_id=aps_frame.cluster_id,
        src_ep=aps_frame.src_ep,
        dst_ep=aps_frame.dst_ep,
        cmd_id=cmd_id,
        prefix=prefix,
        code=code,
        kind=kind,
        value=value,
        raw_rest_hex=rest.hex(),
    )
