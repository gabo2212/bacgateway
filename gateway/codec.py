"""Canonical raw frame layer for the VWG radio protocol.

Provides constants, ParsedFrame, build_frame, parse_frame,
and shared payload parsers (identify, network-config).
No pyserial dependency; import-safe everywhere.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

START_REQUEST = 0x40
START_RESPONSE = 0x3C

CMD_READ_REQUEST = 0
CMD_READ_RESPONSE = 1
CMD_WRITE_REQUEST = 2
CMD_WRITE_RESPONSE = 3

RF_MODULE_START_NETWORK = 0x0F00
RF_MODULE_CONFIGURE_NETWORK = 0x0F01
RF_MODULE_DUPLICATE_COMM = 0x0F02
RF_MODULE_IDENTIFY = 0x0F03

STATUS_LABELS: dict[int, str] = {
    0: "No error",
    1: "Network parameters not set",
    2: "Object not supported",
    3: "Out of memory",
    4: "Parameters out of range",
    5: "Invalid COMM address",
    6: "Invalid Command Type",
    7: "Thermostat not identified",
    8: "Thermostat not added to network",
    9: "Thermostat address not registered",
}

LINK_QUALITY_LOOKUP: list[int] = [
    5, 8, 10, 14, 21, 25, 29, 33, 36, 38, 40, 42, 44, 46, 48, 49, 50, 52,
    53, 54, 55, 56, 57, 58, 59, 60, 61, 62, 62, 63, 64, 65, 65, 66, 67, 67,
    68, 68, 69, 70, 70, 71, 71, 72, 72, 73, 73, 74, 74, 74, 75, 75, 76, 76,
    77, 77, 77, 78, 78, 78, 79, 79, 80, 80, 80, 81, 81, 81, 82, 82, 82, 82,
    83, 83, 83, 84, 84, 84, 84, 85, 85, 85, 86, 86, 86, 86, 87, 87, 87, 87,
    88, 88, 88, 88, 88, 89, 89, 89, 89, 90, 90, 90, 90, 90, 91, 91, 91, 91,
    91, 92, 92, 92, 92, 92, 93, 93, 93, 93, 93, 94, 94, 94, 94, 94, 94, 95,
    95, 95, 95, 95, 95, 96, 96, 96, 96, 96, 96, 97, 97, 97, 97, 97, 97, 97,
    98, 98, 98, 98, 98, 98, 99, 99, 99, 99, 99, 99, 99, 100,
]

# ---------------------------------------------------------------------------
# ParsedFrame
# ---------------------------------------------------------------------------


@dataclass
class ParsedFrame:
    raw: bytes
    msg_type: int
    cmd_type: int
    comm_addr: int
    trans_seq: int
    status: Optional[int]
    payload: bytes
    link_quality_raw: Optional[int]
    link_quality: Optional[int]
    crc_ok: bool
    length_ok: bool


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def status_label(code: Optional[int]) -> Optional[str]:
    """Return human-readable status label or None."""
    if code is None:
        return None
    return STATUS_LABELS.get(code, f"Unspecified error: {code}")


def link_quality(raw: Optional[int]) -> Optional[int]:
    """Map raw LQ byte to percentage via lookup table."""
    if raw is None:
        return None
    idx = min(raw, len(LINK_QUALITY_LOOKUP) - 1)
    return LINK_QUALITY_LOOKUP[idx]


# ---------------------------------------------------------------------------
# Frame builder
# ---------------------------------------------------------------------------


def build_frame(
    msg_type: int,
    cmd_type: int,
    comm_addr: int,
    trans_seq: int,
    payload: bytes = b"",
) -> bytes:
    """Build a host-to-radio (0x40) frame with correct LEN and CRC."""
    frame = bytearray()
    frame.append(START_REQUEST)
    frame.append(0x00)
    frame.extend(msg_type.to_bytes(2, "big"))
    frame.append(cmd_type & 0xFF)
    frame.append(comm_addr & 0xFF)
    frame.append(trans_seq & 0xFF)
    frame.extend(payload)
    frame.append(sum(frame[2:]) & 0xFF)
    frame[1] = len(frame) - 2
    return bytes(frame)


# ---------------------------------------------------------------------------
# Frame parser
# ---------------------------------------------------------------------------


def parse_frame(frame: bytes) -> ParsedFrame:
    """Parse a raw radio frame (0x3C or 0x40) into a ParsedFrame.

    Raises ValueError for frames that are too short (< 7 bytes) or have an
    unexpected start byte — cases where a ParsedFrame cannot be constructed.
    For structurally valid frames with a bad LEN or CRC the flags
    length_ok / crc_ok are set to False instead of raising.
    """
    if len(frame) < 7:
        raise ValueError(f"Frame too short: {len(frame)} bytes")
    start = frame[0]
    if start not in (START_REQUEST, START_RESPONSE):
        raise ValueError(f"Unexpected start byte: 0x{start:02X}")

    length_ok = frame[1] == len(frame) - 2

    msg_type = (frame[2] << 8) | frame[3]
    cmd_type = frame[4]
    comm_addr = frame[5]
    trans_seq = frame[6]

    idx = 7
    status: Optional[int] = None
    if cmd_type in (CMD_READ_RESPONSE, CMD_WRITE_RESPONSE):
        if idx < len(frame) - 1:  # must have at least status + crc
            status = frame[idx]
            idx += 1

    if start == START_RESPONSE:
        lq_raw: Optional[int] = frame[-2] if len(frame) >= idx + 2 else None
        payload_end = len(frame) - 2
    else:
        lq_raw = None
        payload_end = len(frame) - 1

    payload = frame[idx:payload_end]
    crc_ok = (sum(frame[2:-1]) & 0xFF) == frame[-1]

    return ParsedFrame(
        raw=frame,
        msg_type=msg_type,
        cmd_type=cmd_type,
        comm_addr=comm_addr,
        trans_seq=trans_seq,
        status=status,
        payload=payload,
        link_quality_raw=lq_raw,
        link_quality=link_quality(lq_raw),
        crc_ok=crc_ok,
        length_ok=length_ok,
    )


# ---------------------------------------------------------------------------
# Payload parsers
# ---------------------------------------------------------------------------


def parse_identify_payload(payload: bytes) -> dict[str, Any]:
    """Parse a 13-byte RF_MODULE_IDENTIFY response payload."""
    if len(payload) != 13:
        raise ValueError(f"identify payload must be 13 bytes, got {len(payload)}")
    firmware_maj = payload[0]
    firmware_min = payload[1]
    zigbee_addr = int.from_bytes(payload[2:4], "big")
    ieee_addr = payload[4:12].hex().upper()
    chip_rev = payload[12]
    return {
        "firmware_maj": firmware_maj,
        "firmware_min": firmware_min,
        "zigbee_addr": f"0x{zigbee_addr:04X}",
        "ieee_addr": f"0x{ieee_addr}",
        "chip_rev": chip_rev,
    }


def parse_network_config_payload(payload: bytes) -> dict[str, Any]:
    """Parse a 3-byte RF_MODULE_CONFIGURE_NETWORK response payload."""
    if len(payload) != 3:
        raise ValueError(f"network config payload must be 3 bytes, got {len(payload)}")
    pan_id = int.from_bytes(payload[0:2], "big")
    channel = payload[2]
    return {"pan_id": pan_id, "channel": channel}

