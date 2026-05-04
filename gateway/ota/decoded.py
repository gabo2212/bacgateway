from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


def _parse_int(text: str) -> Optional[int]:
    value = text.strip().lower()
    if not value:
        return None
    try:
        return int(value, 0)
    except ValueError:
        return None


def _parse_value(text: str) -> Optional[float | int]:
    value = text.strip()
    if not value:
        return None
    try:
        if "." in value:
            return float(value)
        return int(value, 0)
    except ValueError:
        return None


def _parse_kv_line(line: str) -> dict[str, str]:
    items: dict[str, str] = {}
    for token in line.strip().split():
        if "=" not in token:
            continue
        key, value = token.split("=", 1)
        items[key] = value
    return items


def _parse_hex_pair(payload: str, start: int) -> Optional[int]:
    if len(payload) < start + 2:
        return None
    try:
        return int(payload[start : start + 2], 16)
    except ValueError:
        return None


@dataclass(frozen=True)
class DecodedLine:
    raw: str
    t: float
    direction: str
    dev: str
    dev_short: Optional[int]
    cmd: int
    payload: str
    kind: str
    value: Optional[float | int]
    label: Optional[str]
    extra: Optional[str]
    rest_len: Optional[int]
    reason: Optional[str]
    prefix: Optional[int]
    code: Optional[int]


def parse_decoded_line(line: str) -> Optional[DecodedLine]:
    if not line.strip():
        return None
    items = _parse_kv_line(line)
    t_raw = items.get("t")
    cmd_raw = items.get("cmd")
    payload = items.get("payload")
    kind = items.get("kind")
    direction = items.get("dir")
    dev = items.get("dev", "")
    if t_raw is None or cmd_raw is None or payload is None or kind is None or direction is None:
        return None
    try:
        t_val = float(t_raw)
    except ValueError:
        return None
    cmd_val = _parse_int(cmd_raw)
    if cmd_val is None:
        return None
    dev_short = _parse_int(dev) if dev else None
    value = _parse_value(items.get("value", "")) if items.get("value") is not None else None
    rest_len = _parse_int(items.get("rest_len", "")) if items.get("rest_len") is not None else None
    prefix = _parse_hex_pair(payload, 0)
    code = _parse_hex_pair(payload, 2)
    return DecodedLine(
        raw=line.rstrip("\n"),
        t=t_val,
        direction=direction,
        dev=dev,
        dev_short=dev_short,
        cmd=cmd_val,
        payload=payload,
        kind=kind,
        value=value,
        label=items.get("label"),
        extra=items.get("extra"),
        rest_len=rest_len,
        reason=items.get("reason"),
        prefix=prefix,
        code=code,
    )


def load_decoded_lines(path: str) -> list[DecodedLine]:
    try:
        with open(path, "r", encoding="utf-8") as handle:
            lines = handle.read().splitlines()
    except OSError:
        return []
    parsed: list[DecodedLine] = []
    for line in lines:
        decoded = parse_decoded_line(line)
        if decoded is not None:
            parsed.append(decoded)
    return parsed
