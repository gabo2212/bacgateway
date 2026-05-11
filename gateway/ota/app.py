from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Optional

from .pointmap import PointMap
from .zigbee import ApsFrame

LOGGER = logging.getLogger(__name__)

Direction = Literal["gw->dev", "dev->gw"]
Kind = Literal["identify_req", "identify_rsp", "analog_x10", "enum", "ack", "unknown", "u8"]
Value = float | int


def _log_debug(event: str, **fields: object) -> None:
    if LOGGER.isEnabledFor(logging.DEBUG):
        LOGGER.debug(event, extra={"event": event, **fields})

_DEFAULT_POINTMAP: Optional[PointMap] = None


def _get_pointmap(override: Optional[PointMap] = None) -> PointMap:
    if override is not None:
        return override
    global _DEFAULT_POINTMAP
    if _DEFAULT_POINTMAP is None:
        path = Path(__file__).with_name("pointmap.yaml")
        from .pointmap import load_pointmap
        _DEFAULT_POINTMAP = load_pointmap(path)
    return _DEFAULT_POINTMAP


def normalize_code(prefix: int | None, code: int | None, point_map: Optional[PointMap] = None) -> tuple[int | None, int | None]:
    if prefix is None or code is None:
        return prefix, code
    point_map = _get_pointmap(point_map)
    return prefix, point_map.canonicalize(prefix, code)


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
    rest_len: int
    ack_extra_hex: Optional[str]
    label: Optional[str]
    reason: Optional[str]


def decode_cmd2_rest(
    rest: bytes, point_map: Optional[PointMap] = None
) -> tuple[Optional[int], Optional[int], Kind, Optional[Value]]:
    point_map = _get_pointmap(point_map)
    prefix = rest[0] if len(rest) >= 1 else None
    code = rest[1] if len(rest) >= 2 else None
    if prefix is not None and code is not None:
        code = point_map.canonicalize(prefix, code)
    if len(rest) == 3:
        return prefix, code, "u8", int(rest[2])
    if len(rest) != 4:
        return prefix, code, "unknown", None
    if prefix is not None and code is not None and point_map.is_enum(prefix, code):
        return prefix, code, "enum", int(rest[3])
    value = int.from_bytes(rest[2:4], "big") / 10.0
    return prefix, code, "analog_x10", value


def decode_cmd3_rest(
    rest: bytes, point_map: Optional[PointMap] = None
) -> tuple[
    Optional[int],
    Optional[int],
    Kind,
    Optional[Value],
    Optional[str],
    Optional[str],
]:
    point_map = _get_pointmap(point_map)
    prefix = rest[0] if len(rest) >= 1 else None
    code = rest[1] if len(rest) >= 2 else None
    if prefix is not None and code is not None:
        code = point_map.canonicalize(prefix, code)
    if len(rest) < 3:
        return prefix, code, "unknown", None, None, "short_rest"
    if rest[2] == 0x00:
        extra_bytes = rest[3:]
        if extra_bytes[:1] == b"\x00":
            extra_bytes = extra_bytes[1:]
        extra = extra_bytes.hex() if extra_bytes else None
        return prefix, code, "ack", None, extra, None
    return prefix, code, "unknown", None, None, None


def parse_ota_msg(
    t_rel: float,
    nwk_src_short: int,
    nwk_dst_short: int,
    aps_frame: ApsFrame,
    point_map: Optional[PointMap] = None,
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
    ack_extra_hex: Optional[str] = None
    reason: Optional[str] = None
    point_map = _get_pointmap(point_map)

    if cmd_id == 0x00:
        kind = "identify_req"
    elif cmd_id == 0x01:
        kind = "identify_rsp"
    elif cmd_id == 0x02:
        prefix, code, kind, value = decode_cmd2_rest(rest, point_map)
    elif cmd_id == 0x03:
        prefix, code, kind, value, ack_extra_hex, reason = decode_cmd3_rest(
            rest, point_map
        )
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
        rest_len=len(rest),
        ack_extra_hex=ack_extra_hex,
        label=point_map.label_for(prefix, code, kind) if prefix is not None and code is not None else None,
        reason=reason,
    )
