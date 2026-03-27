#!/usr/bin/env python3
from __future__ import annotations

import csv
import json
import logging
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Optional

# Raw frame helpers are now canonical in gateway.codec; delegate to avoid duplication.
from gateway import codec as _gcodec

ROOT = Path(__file__).resolve().parents[1]
POINTS_CSV = ROOT / "spec" / "points_catalog.csv"
ENUMS_YAML = ROOT / "spec" / "enums.yaml"

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class PointSpec:
    addr: int
    logical_name: str
    rw: str
    value_type: str
    scale: float
    offset: float
    units: str
    min_raw: float | None
    max_raw: float | None
    enum_name: str
    notes: str


def _parse_offset(notes: str) -> float:
    match = re.search(r"offset=([-+]?[0-9]*\.?[0-9]+)", notes or "")
    if not match:
        return 0.0
    try:
        return float(match.group(1))
    except ValueError:
        return 0.0


def _parse_float(text: str) -> float | None:
    if text is None or text == "":
        return None
    try:
        return float(text)
    except ValueError:
        return None


@lru_cache(maxsize=1)
def load_points_catalog() -> dict[int, list[PointSpec]]:
    specs: dict[int, list[PointSpec]] = {}
    if not POINTS_CSV.exists():
        raise FileNotFoundError(f"Missing {POINTS_CSV}")
    with POINTS_CSV.open("r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            addr_text = (row.get("point_addr_hex") or "").strip()
            if not addr_text:
                continue
            addr = int(addr_text, 16) if addr_text.startswith("0x") else int(addr_text)
            scale_text = (row.get("scale_divisor") or "").strip()
            scale = float(scale_text) if scale_text else 1.0
            notes = row.get("notes", "") or ""
            offset = _parse_offset(notes)
            spec = PointSpec(
                addr=addr,
                logical_name=row.get("logical_name", "") or "",
                rw=row.get("rw", "") or "",
                value_type=row.get("value_type", "") or "",
                scale=scale,
                offset=offset,
                units=row.get("units", "") or "",
                min_raw=_parse_float((row.get("min_raw") or "").strip()),
                max_raw=_parse_float((row.get("max_raw") or "").strip()),
                enum_name=row.get("enum_name", "") or "",
                notes=notes,
            )
            specs.setdefault(addr, []).append(spec)
    return specs


@lru_cache(maxsize=1)
def load_enums() -> tuple[dict[str, dict[int, str]], dict[str, dict[str, int]]]:
    if not ENUMS_YAML.exists():
        raise FileNotFoundError(f"Missing {ENUMS_YAML}")
    data = json.loads(ENUMS_YAML.read_text(encoding="utf-8"))
    enums: dict[str, dict[int, str]] = {}
    reverse: dict[str, dict[str, int]] = {}
    for name, payload in (data.get("enums") or {}).items():
        mapping = payload.get("mapping", {}) if isinstance(payload, dict) else {}
        int_map: dict[int, str] = {}
        rev_map: dict[str, int] = {}
        for key, label in mapping.items():
            try:
                num = int(key)
            except ValueError:
                continue
            int_map[num] = label
            rev_map[label] = num
        enums[name] = int_map
        reverse[name] = rev_map
    return enums, reverse


def resolve_point_spec(
    addr: int, logical_name: Optional[str] = None
) -> Optional[PointSpec]:
    specs = load_points_catalog().get(addr)
    if not specs:
        return None
    if logical_name:
        for spec in specs:
            if spec.logical_name.lower() == logical_name.lower():
                return spec
    return _choose_spec(addr)


def _choose_spec(addr: int) -> PointSpec | None:
    specs = load_points_catalog().get(addr)
    if not specs:
        return None
    chosen = sorted(specs, key=lambda s: (0 if s.rw == "RW" else 1, s.logical_name))[0]
    if len(specs) > 1:
        LOGGER.warning("Multiple specs for 0x%04X; using %s", addr, chosen.logical_name)
    return chosen


def _to_signed(raw: int) -> int:
    return raw - 0x10000 if raw & 0x8000 else raw


def build_read_point(comm_addr: int, point_addr: int, trans_seq: int) -> bytes:
    """Build a READ_REQUEST frame for a point. Delegates to gateway.codec."""
    return _gcodec.build_frame(
        point_addr, _gcodec.CMD_READ_REQUEST, comm_addr, trans_seq
    )


def build_write_point(
    comm_addr: int, point_addr: int, value: Any, trans_seq: int
) -> bytes:
    """Build a WRITE_REQUEST frame for a point. Delegates to gateway.codec."""
    payload = encode_point_value(point_addr, value)
    return _gcodec.build_frame(
        point_addr, _gcodec.CMD_WRITE_REQUEST, comm_addr, trans_seq, payload
    )


def parse_received_frame(frame: bytes) -> dict[str, Any]:
    """Parse a raw radio frame. Delegates to gateway.codec.parse_frame.

    Returns a dict with the same keys as before for backward compatibility.
    Raises ValueError for frames that are too short or have an unknown start byte.
    """
    pf = _gcodec.parse_frame(frame)
    return {
        "msg_type": pf.msg_type,
        "cmd_type": pf.cmd_type,
        "comm_addr": pf.comm_addr,
        "trans_seq": pf.trans_seq,
        "status": pf.status,
        "payload": pf.payload,
        "link_quality": pf.link_quality_raw,
        "crc_ok": pf.crc_ok,
    }


def _decode_payload_with_spec(spec: Optional[PointSpec], payload: bytes) -> Any:
    if len(payload) < 2:
        raise ValueError("Point payload must be at least 2 bytes")
    raw = (payload[0] << 8) | payload[1]
    if spec is None:
        return raw

    signed_raw = raw
    if spec.value_type in ("s16", "scaled-int"):
        if spec.min_raw is None or spec.min_raw < 0:
            signed_raw = _to_signed(raw)
    elif spec.value_type == "s16":
        signed_raw = _to_signed(raw)

    if spec.value_type == "bool":
        scaled = (signed_raw + spec.offset) / spec.scale
        return bool(scaled)
    if spec.value_type == "enum":
        scaled = (signed_raw + spec.offset) / spec.scale
        enum_value = int(round(scaled))
        enums, _ = load_enums()
        return enums.get(spec.enum_name, {}).get(enum_value, enum_value)

    scaled = (signed_raw + spec.offset) / spec.scale
    if spec.scale == 1.0 and spec.offset == 0.0:
        return int(round(scaled))
    return scaled


def parse_point_value(point_addr: int, payload: bytes) -> Any:
    spec = _choose_spec(point_addr)
    return _decode_payload_with_spec(spec, payload)


def parse_point_value_with_name(
    point_addr: int, payload: bytes, logical_name: str
) -> Any:
    spec = resolve_point_spec(point_addr, logical_name)
    return _decode_payload_with_spec(spec, payload)


def _encode_raw_value(spec: PointSpec, value: Any) -> int:
    if spec.value_type == "enum":
        enums, reverse = load_enums()
        enum_labels = reverse.get(spec.enum_name, {})
        enum_values = enums.get(spec.enum_name, {})
        if isinstance(value, str):
            if value not in enum_labels:
                raise ValueError(f"Unknown enum label: {value}")
            numeric = enum_labels[value]
        else:
            numeric = int(value)
            if enum_values and numeric not in enum_values:
                raise ValueError(f"Unknown enum value: {numeric}")
        raw_val = (numeric * spec.scale) - spec.offset
    elif spec.value_type == "bool":
        raw_val = ((1 if bool(value) else 0) * spec.scale) - spec.offset
    else:
        raw_val = (float(value) * spec.scale) - spec.offset

    return int(round(raw_val))


def encode_point_value(point_addr: int, value: Any) -> bytes:
    spec = _choose_spec(point_addr)
    if spec is None:
        raw = int(value)
        return (raw & 0xFFFF).to_bytes(2, "big")

    raw = _encode_raw_value(spec, value)
    return (raw & 0xFFFF).to_bytes(2, "big")


def encode_point_value_with_name(
    point_addr: int, value: Any, logical_name: str
) -> bytes:
    spec = resolve_point_spec(point_addr, logical_name)
    if spec is None:
        return encode_point_value(point_addr, value)
    raw = _encode_raw_value(spec, value)
    return (raw & 0xFFFF).to_bytes(2, "big")


def validate_point_value(
    point_addr: int, value: Any, logical_name: Optional[str] = None
) -> None:
    spec = resolve_point_spec(point_addr, logical_name)
    if spec is None:
        return

    if spec.value_type == "bool":
        if isinstance(value, bool):
            pass
        elif isinstance(value, (int, float)) and value in (0, 1):
            pass
        else:
            raise ValueError(f"Invalid bool value for 0x{point_addr:04X}: {value}")
    elif spec.value_type != "enum":
        if not isinstance(value, (int, float)):
            raise ValueError(f"Non-numeric value for 0x{point_addr:04X}: {value}")

    raw = _encode_raw_value(spec, value)
    if spec.min_raw is not None and raw < spec.min_raw:
        raise ValueError(
            f"Value below min_raw for 0x{point_addr:04X}: {raw} < {spec.min_raw}"
        )
    if spec.max_raw is not None and raw > spec.max_raw:
        raise ValueError(
            f"Value above max_raw for 0x{point_addr:04X}: {raw} > {spec.max_raw}"
        )


def _self_test() -> None:
    logging.basicConfig(level=logging.INFO)
    specs = load_points_catalog()
    if not specs:
        raise RuntimeError("No point specs loaded")

    # Pick a few representative addresses.
    wanted = {"scaled-int": None, "bool": None, "enum": None}
    for addr, spec_list in specs.items():
        spec = spec_list[0]
        if spec.value_type in wanted and wanted[spec.value_type] is None:
            wanted[spec.value_type] = addr
        if all(wanted.values()):
            break
    test_points = [addr for addr in wanted.values() if addr is not None]

    for addr in test_points:
        spec = _choose_spec(addr)
        if spec is None:
            continue
        if spec.value_type == "scaled-int":
            value = 72.5
        elif spec.value_type == "bool":
            value = True
        elif spec.value_type == "enum":
            enums, _ = load_enums()
            mapping = enums.get(spec.enum_name, {})
            value = next(iter(mapping.values()), 0)
        else:
            value = 1
        encoded = encode_point_value(addr, value)
        decoded = parse_point_value(addr, encoded)
        LOGGER.info("self-test 0x%04X %s -> %s", addr, value, decoded)

    read_frame = build_read_point(1, 0x1000, 1)
    if read_frame[1] != len(read_frame) - 2:
        raise AssertionError("Read frame length mismatch")
    if (sum(read_frame[2:-1]) & 0xFF) != read_frame[-1]:
        raise AssertionError("Read frame CRC mismatch")

    # Build a synthetic response and parse it.
    payload = encode_point_value(0x1000, 72.5)
    response = bytearray()
    response.append(0x3C)
    response.append(0x00)
    response.extend((0x10, 0x00))
    response.append(0x01)  # READ_RESPONSE
    response.append(0x01)
    response.append(0x01)
    response.append(0x00)  # status
    response.extend(payload)
    response.append(0x2A)  # link quality
    response.append(0x00)  # crc placeholder
    response[1] = len(response) - 2
    response[-1] = sum(response[2:-1]) & 0xFF
    parsed = parse_received_frame(bytes(response))
    if not parsed["crc_ok"]:
        raise AssertionError("Response CRC mismatch")


if __name__ == "__main__":
    _self_test()
