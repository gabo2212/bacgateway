#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _parse_hex(value: object) -> Optional[int]:
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        text = value.strip().lower()
        if text == "?":
            return None
        try:
            return int(text, 0)
        except ValueError:
            return None
    return None


def _format_hex(value: int) -> str:
    return f"0x{value:02x}"


def _load_pointmap(path: Path) -> dict:
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return {"families": {}, "points": []}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return {"families": {}, "points": []}
    if not isinstance(data, dict):
        return {"families": {}, "points": []}
    data.setdefault("families", {})
    data.setdefault("points", [])
    return data


def _collect_existing(points: list[dict]) -> set[tuple[int, int, str]]:
    existing: set[tuple[int, int, str]] = set()
    for entry in points:
        if not isinstance(entry, dict):
            continue
        family = _parse_hex(entry.get("family"))
        code = _parse_hex(entry.get("code"))
        kind = entry.get("kind")
        if family is None or code is None or not isinstance(kind, str):
            continue
        existing.add((family, code, kind))
    return existing


def _load_catalog(path: Path) -> list[dict]:
    import csv

    rows: list[dict] = []
    try:
        with path.open("r", encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            for row in reader:
                rows.append(row)
    except OSError:
        return []
    return rows


def _collect_targets(
    catalog_paths: list[Path], include_ack: bool
) -> set[tuple[int, int, str]]:
    targets: set[tuple[int, int, str]] = set()
    for path in catalog_paths:
        for row in _load_catalog(path):
            cmd = _parse_hex(row.get("cmd"))
            if cmd is None:
                continue
            kind_set = row.get("kind_set")
            if not isinstance(kind_set, str):
                continue
            kind_set = kind_set.strip().lower()
            prefix = _parse_hex(row.get("prefix"))
            code = _parse_hex(row.get("code"))
            if prefix is None or code is None:
                continue
            if cmd == 2 and kind_set in {"analog_x10", "enum", "u8"}:
                targets.add((prefix, code, kind_set))
            elif cmd == 3 and include_ack and "ack" in kind_set.split("|"):
                targets.add((prefix, code, "ack"))
    return targets


def _sorted_family_keys(families: dict) -> dict:
    def sort_key(item: str) -> int:
        parsed = _parse_hex(item)
        return parsed if parsed is not None else 0

    return {key: families[key] for key in sorted(families, key=sort_key)}


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate placeholder point labels from catalogs")
    parser.add_argument(
        "catalogs",
        nargs="+",
        help="Catalog CSV files to scan",
    )
    parser.add_argument(
        "--map",
        default="gateway/ota/pointmap.json",
        help="Point map JSON path (default: gateway/ota/pointmap.json)",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Write updates back to the map file",
    )
    parser.add_argument(
        "--include-ack",
        action="store_true",
        help="Include ack entries (cmd=3) when generating placeholders",
    )
    args = parser.parse_args()

    map_path = Path(args.map)
    pointmap = _load_pointmap(map_path)
    points = pointmap.get("points", [])
    if not isinstance(points, list):
        points = []
    existing = _collect_existing(points)

    targets = _collect_targets([Path(path) for path in args.catalogs], args.include_ack)
    new_entries: list[dict] = []
    for family, code, kind in sorted(targets, key=lambda item: (item[0], item[1], item[2])):
        if (family, code, kind) in existing:
            continue
        family_hex = _format_hex(family)
        code_hex = _format_hex(code)
        label = f"auto_{family_hex}_{code_hex}_{kind}".lower()
        new_entries.append(
            {
                "family": family_hex,
                "code": code_hex,
                "kind": kind,
                "label": label,
            }
        )

    if not args.apply:
        print(json.dumps(new_entries, indent=2, sort_keys=True))
        return

    points.extend(new_entries)
    def sort_key(entry: dict) -> tuple[int, int, str]:
        family = _parse_hex(entry.get("family")) or 0
        code = _parse_hex(entry.get("code")) or 0
        kind = entry.get("kind") or ""
        return family, code, kind

    points = [entry for entry in points if isinstance(entry, dict)]
    points.sort(key=sort_key)
    pointmap["points"] = points
    families = pointmap.get("families", {})
    pointmap["families"] = _sorted_family_keys(families if isinstance(families, dict) else {})

    map_path.parent.mkdir(parents=True, exist_ok=True)
    map_path.write_text(json.dumps(pointmap, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
