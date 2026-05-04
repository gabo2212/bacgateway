from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Optional

from .ieee802154 import strip_tap
from .pcapng import PcapNgReader


@dataclass(frozen=True)
class TxTemplate:
    device_short: int
    mac_frame: bytes
    nwk_off: int
    aps_off: int
    app_off: int
    rest_off: int
    rest_len: int
    cmd_id: int
    direction: Literal["gw->dev"]


@dataclass(frozen=True)
class ByteDiffRange:
    start: int
    end: int
    before: bytes
    after: bytes


@dataclass(frozen=True)
class _MacWithOffsets:
    pan_id: Optional[int]
    src_short: Optional[int]
    dst_short: Optional[int]
    payload_off: int
    payload: bytes


@dataclass(frozen=True)
class _NwkWithOffsets:
    src: int
    dst: int
    payload_off: int
    payload: bytes


@dataclass(frozen=True)
class _ApsWithOffsets:
    profile_id: int
    cluster_id: int
    src_ep: int
    dst_ep: int
    payload_off: int
    payload: bytes


@dataclass(frozen=True)
class _VendorWithOffsets:
    cmd_id: int
    cmd_off: int
    rest_off: int
    rest_len: int


def _parse_mac_with_offsets(data: bytes) -> Optional[_MacWithOffsets]:
    if len(data) < 2:
        return None
    frame_control = int.from_bytes(data[0:2], "little")
    frame_type = frame_control & 0x7
    if frame_type != 0x1:
        return None
    if (frame_control >> 3) & 0x1:
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
    src_short: Optional[int] = None

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
            offset += 8
        else:
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
            offset += 8
        else:
            return None

    pan_id = dst_pan if dst_pan is not None else src_pan
    return _MacWithOffsets(
        pan_id=pan_id,
        src_short=src_short,
        dst_short=dst_short,
        payload_off=offset,
        payload=data[offset:],
    )


def _parse_nwk_with_offsets(data: bytes) -> Optional[_NwkWithOffsets]:
    if len(data) < 8:
        return None
    frame_control = int.from_bytes(data[0:2], "little")
    if (frame_control & 0x3) != 0x0:
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
    offset += 2

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
        relay_len = relay_count * 2
        if len(data) < offset + relay_len:
            return None
        offset += relay_len

    if security_enabled:
        return None
    if offset > len(data):
        return None

    return _NwkWithOffsets(src=src, dst=dst, payload_off=offset, payload=data[offset:])


def _parse_aps_with_offsets(data: bytes) -> Optional[_ApsWithOffsets]:
    if len(data) < 8:
        return None
    frame_control = data[0]
    frame_type = frame_control & 0x3
    delivery_mode = (frame_control >> 2) & 0x3
    security_enabled = (frame_control >> 5) & 0x1
    extended_header = (frame_control >> 7) & 0x1
    if frame_type != 0x0 or delivery_mode != 0x0:
        return None
    if security_enabled or extended_header:
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
    offset += 1
    if offset > len(data):
        return None
    return _ApsWithOffsets(
        profile_id=profile_id,
        cluster_id=cluster_id,
        src_ep=src_ep,
        dst_ep=dst_ep,
        payload_off=offset,
        payload=data[offset:],
    )


def _parse_vendor_with_offsets(data: bytes) -> Optional[_VendorWithOffsets]:
    if len(data) < 3:
        return None
    offset = 0
    frame_control = data[offset]
    offset += 1
    if frame_control & 0x04:
        if len(data) < offset + 2:
            return None
        offset += 2
    if len(data) < offset + 2:
        return None
    offset += 1
    cmd_off = offset
    cmd_id = data[offset]
    offset += 1
    return _VendorWithOffsets(cmd_id=cmd_id, cmd_off=cmd_off, rest_off=offset, rest_len=len(data) - offset)


def extract_templates_from_pcap(
    pcap_path: Path,
    *,
    pan: int = 0x00D2,
    profile: int = 0xC1E4,
    cluster: int = 0x0002,
    gw_short: int = 0x0000,
) -> list[TxTemplate]:
    templates: list[TxTemplate] = []
    reader = PcapNgReader(str(pcap_path))
    for packet in reader.packets():
        tapped = strip_tap(packet.data)
        if tapped is None:
            continue
        mac = _parse_mac_with_offsets(tapped)
        if mac is None:
            continue
        if mac.pan_id != pan:
            continue

        effective_mac = tapped
        nwk = _parse_nwk_with_offsets(mac.payload)
        if nwk is None and len(mac.payload) >= 2:
            trimmed = mac.payload[:-2]
            nwk = _parse_nwk_with_offsets(trimmed)
            if nwk is not None:
                effective_mac = tapped[:-2]
        if nwk is None:
            continue
        if nwk.src != gw_short or nwk.dst == gw_short:
            continue

        aps = _parse_aps_with_offsets(nwk.payload)
        if aps is None:
            continue
        if aps.profile_id != profile or aps.cluster_id != cluster:
            continue

        vendor = _parse_vendor_with_offsets(aps.payload)
        if vendor is None:
            continue
        if vendor.cmd_id not in (0x02, 0x03):
            continue

        nwk_off = mac.payload_off
        aps_off = nwk_off + nwk.payload_off
        app_off = aps_off + aps.payload_off
        rest_off = app_off + vendor.rest_off
        if rest_off + vendor.rest_len > len(effective_mac):
            continue
        templates.append(
            TxTemplate(
                device_short=nwk.dst,
                mac_frame=effective_mac,
                nwk_off=nwk_off,
                aps_off=aps_off,
                app_off=app_off,
                rest_off=rest_off,
                rest_len=vendor.rest_len,
                cmd_id=vendor.cmd_id,
                direction="gw->dev",
            )
        )
    return templates


def build_from_template(t: TxTemplate, *, cmd_id: int, rest: bytes) -> bytes:
    if t.rest_off <= 0:
        raise ValueError("template rest_off must be >= 1")
    cmd_off = t.rest_off - 1
    if cmd_off >= len(t.mac_frame):
        raise ValueError("template cmd offset out of range")
    if t.rest_off + t.rest_len > len(t.mac_frame):
        raise ValueError("template rest range out of bounds")
    if cmd_id < 0 or cmd_id > 0xFF:
        raise ValueError("cmd_id must fit in one byte")

    head = t.mac_frame[:cmd_off] + bytes([cmd_id])
    tail = t.mac_frame[t.rest_off + t.rest_len :]
    return head + rest + tail


def diff_byte_ranges(before: bytes, after: bytes) -> list[ByteDiffRange]:
    diffs: list[ByteDiffRange] = []
    min_len = min(len(before), len(after))
    idx = 0
    while idx < min_len:
        if before[idx] == after[idx]:
            idx += 1
            continue
        start = idx
        while idx < min_len and before[idx] != after[idx]:
            idx += 1
        diffs.append(ByteDiffRange(start=start, end=idx, before=before[start:idx], after=after[start:idx]))
    if len(before) != len(after):
        start = min_len
        diffs.append(ByteDiffRange(start=start, end=max(len(before), len(after)), before=before[start:], after=after[start:]))
    return diffs


def diff_offsets(before: bytes, after: bytes) -> list[int]:
    result: list[int] = []
    max_len = max(len(before), len(after))
    for idx in range(max_len):
        b = before[idx] if idx < len(before) else None
        a = after[idx] if idx < len(after) else None
        if b != a:
            result.append(idx)
    return result


def infer_volatile_offsets(frames: list[bytes]) -> list[int]:
    if len(frames) < 2:
        return []
    baseline = frames[0]
    offsets: set[int] = set()
    for frame in frames[1:]:
        offsets.update(diff_offsets(baseline, frame))
    return sorted(offsets)
