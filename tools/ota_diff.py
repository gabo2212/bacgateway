#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from gateway.ota.decoded import DecodedLine, load_decoded_lines
from gateway.ota.pointmap import PointMap, load_pointmap


def _parse_hex(value: Optional[str]) -> Optional[int]:
    if value is None:
        return None
    text = value.strip().lower()
    if text == "?":
        return None
    try:
        return int(text, 0)
    except ValueError:
        return None


def _format_hex(value: Optional[int], width: int = 2) -> str:
    if value is None:
        return "?"
    return f"0x{value:0{width}x}"


def _sort_num(value: Optional[int]) -> int:
    return value if value is not None else 0x1_0000


def _decoded_entries(path: Path, pointmap: PointMap, by_device: bool) -> dict:
    entries: dict[tuple, dict] = {}
    lines: list[DecodedLine] = load_decoded_lines(str(path))
    for line in lines:
        prefix = line.prefix
        code = line.code
        if prefix is not None and code is not None:
            code = pointmap.canonicalize(prefix, code)
        kind = line.kind
        dev = line.dev
        key = (dev, prefix, code, kind) if by_device else (prefix, code, kind)
        t = line.t
        value = line.value
        entry = entries.get(key)
        if entry is None:
            entries[key] = {"count": 1, "first_t": t, "first_value": value}
        else:
            entry["count"] += 1
            if t < entry["first_t"]:
                entry["first_t"] = t
                entry["first_value"] = value
    return entries


def _catalog_entries(path: Path, by_device: bool) -> dict:
    entries: dict[tuple, dict] = {}
    try:
        with path.open("r", encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            for row in reader:
                prefix = _parse_hex(row.get("prefix"))
                code = _parse_hex(row.get("code"))
                kind_set = row.get("kind_set", "")
                cmd = _parse_hex(row.get("cmd")) or 0
                direction = row.get("direction", "")
                dev = row.get("dev", "")
                key = (
                    dev,
                    prefix,
                    code,
                    kind_set,
                    cmd,
                    direction,
                ) if by_device else (
                    prefix,
                    code,
                    kind_set,
                    cmd,
                    direction,
                )
                try:
                    count = int(row.get("count", "0"))
                except ValueError:
                    count = 0
                entries[key] = {"count": count}
    except OSError:
        return entries
    return entries


def _diff_decoded(a_path: Path, b_path: Path, pointmap: PointMap, by_device: bool, limit: int) -> None:
    a_entries = _decoded_entries(a_path, pointmap, by_device)
    b_entries = _decoded_entries(b_path, pointmap, by_device)

    a_keys = set(a_entries)
    b_keys = set(b_entries)

    output: list[str] = []

    def sort_key(key: tuple) -> tuple:
        if by_device:
            dev, prefix, code, kind = key
            return (_sort_num(prefix), _sort_num(code), kind, dev)
        prefix, code, kind = key
        return (_sort_num(prefix), _sort_num(code), kind)

    for key in sorted(b_keys - a_keys, key=sort_key):
        dev = key[0] if by_device else None
        prefix = key[1] if by_device else key[0]
        code = key[2] if by_device else key[1]
        kind = key[3] if by_device else key[2]
        label = []
        if dev is not None:
            label.append(f"dev={dev}")
        label.extend(
            [
                f"prefix={_format_hex(prefix)}",
                f"code={_format_hex(code)}",
                f"kind={kind}",
            ]
        )
        output.append("only_in_b " + " ".join(label))

    for key in sorted(a_keys - b_keys, key=sort_key):
        dev = key[0] if by_device else None
        prefix = key[1] if by_device else key[0]
        code = key[2] if by_device else key[1]
        kind = key[3] if by_device else key[2]
        label = []
        if dev is not None:
            label.append(f"dev={dev}")
        label.extend(
            [
                f"prefix={_format_hex(prefix)}",
                f"code={_format_hex(code)}",
                f"kind={kind}",
            ]
        )
        output.append("only_in_a " + " ".join(label))

    for key in sorted(a_keys & b_keys, key=sort_key):
        a_value = a_entries[key].get("first_value")
        b_value = b_entries[key].get("first_value")
        if a_value is None or b_value is None:
            continue
        if a_value == b_value:
            continue
        dev = key[0] if by_device else None
        prefix = key[1] if by_device else key[0]
        code = key[2] if by_device else key[1]
        kind = key[3] if by_device else key[2]
        label = []
        if dev is not None:
            label.append(f"dev={dev}")
        label.extend(
            [
                f"prefix={_format_hex(prefix)}",
                f"code={_format_hex(code)}",
                f"kind={kind}",
                f"a={a_value}",
                f"b={b_value}",
            ]
        )
        output.append("value_change " + " ".join(label))

    for line in output[:limit]:
        print(line)


def _diff_catalog(a_path: Path, b_path: Path, by_device: bool, limit: int) -> None:
    a_entries = _catalog_entries(a_path, by_device)
    b_entries = _catalog_entries(b_path, by_device)

    a_keys = set(a_entries)
    b_keys = set(b_entries)

    output: list[str] = []

    def sort_key_catalog(key: tuple) -> tuple:
        if by_device:
            dev, prefix, code, kind_set, cmd, direction = key
            return (dev, _sort_num(prefix), _sort_num(code), kind_set, cmd, direction)
        prefix, code, kind_set, cmd, direction = key
        return (_sort_num(prefix), _sort_num(code), kind_set, cmd, direction)

    for key in sorted(b_keys - a_keys, key=sort_key_catalog):
        if by_device:
            dev, prefix, code, kind_set, cmd, direction = key
        else:
            prefix, code, kind_set, cmd, direction = key
            dev = None
        parts = []
        if dev is not None:
            parts.append(f"dev={dev}")
        parts.extend(
            [
                f"prefix={_format_hex(prefix)}",
                f"code={_format_hex(code)}",
                f"kind_set={kind_set}",
                f"cmd={cmd}",
                f"dir={direction}",
                f"count={b_entries[key]['count']}",
            ]
        )
        output.append("only_in_b " + " ".join(parts))

    for key in sorted(a_keys - b_keys, key=sort_key_catalog):
        if by_device:
            dev, prefix, code, kind_set, cmd, direction = key
        else:
            prefix, code, kind_set, cmd, direction = key
            dev = None
        parts = []
        if dev is not None:
            parts.append(f"dev={dev}")
        parts.extend(
            [
                f"prefix={_format_hex(prefix)}",
                f"code={_format_hex(code)}",
                f"kind_set={kind_set}",
                f"cmd={cmd}",
                f"dir={direction}",
                f"count={a_entries[key]['count']}",
            ]
        )
        output.append("only_in_a " + " ".join(parts))

    for key in sorted(a_keys & b_keys, key=sort_key_catalog):
        delta = b_entries[key]["count"] - a_entries[key]["count"]
        if delta == 0:
            continue
        if by_device:
            dev, prefix, code, kind_set, cmd, direction = key
        else:
            prefix, code, kind_set, cmd, direction = key
            dev = None
        parts = []
        if dev is not None:
            parts.append(f"dev={dev}")
        parts.extend(
            [
                f"prefix={_format_hex(prefix)}",
                f"code={_format_hex(code)}",
                f"kind_set={kind_set}",
                f"cmd={cmd}",
                f"dir={direction}",
                f"a={a_entries[key]['count']}",
                f"b={b_entries[key]['count']}",
                f"delta={delta}",
            ]
        )
        output.append("count_delta " + " ".join(parts))

    for line in output[:limit]:
        print(line)


def main() -> None:
    parser = argparse.ArgumentParser(description="Diff OTA decoded outputs or catalogs")
    parser.add_argument("--a", required=True, help="Path to baseline A")
    parser.add_argument("--b", required=True, help="Path to baseline B")
    parser.add_argument("--by-device", action="store_true", help="Group by device")
    parser.add_argument("--limit", type=int, default=50, help="Max lines to print")
    args = parser.parse_args()

    path_a = Path(args.a)
    path_b = Path(args.b)

    if path_a.suffix == ".txt" and path_b.suffix == ".txt":
        pointmap = load_pointmap(Path("gateway/ota/pointmap.yaml"))
        _diff_decoded(path_a, path_b, pointmap, args.by_device, args.limit)
        return
    if path_a.suffix == ".csv" and path_b.suffix == ".csv":
        _diff_catalog(path_a, path_b, args.by_device, args.limit)
        return

    raise SystemExit("Inputs must both be .decoded.txt or both be .catalog.csv")


if __name__ == "__main__":
    main()
