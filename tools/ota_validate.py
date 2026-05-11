#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _parse_hex(text: str) -> int:
    return int(text, 0)


def _load_json(path: Path) -> dict:
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _has_point(points: list[dict], family: str, code: str, kind: str, label: str) -> bool:
    for entry in points:
        if (
            entry.get("family") == family
            and entry.get("code") == code
            and entry.get("kind") == kind
            and entry.get("label") == label
        ):
            return True
    return False


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate OTA pointmap.json invariants (Note: Pending YAML update, expects JSON)")
    parser.add_argument("--map", default="gateway/ota/pointmap.json", help="Path to pointmap.json")
    args = parser.parse_args()

    path = Path(args.map)
    data = _load_json(path)
    if not data:
        raise SystemExit("pointmap.json could not be loaded")

    families = data.get("families", {})
    points = data.get("points", [])
    if not isinstance(families, dict) or not isinstance(points, list):
        raise SystemExit("pointmap.json missing families/points")

    errors: list[str] = []

    family_08 = families.get("0x08", {})
    if not isinstance(family_08, dict):
        errors.append("families.0x08 missing or invalid")
    else:
        report_map = family_08.get("report_to_write", {})
        if not isinstance(report_map, dict):
            errors.append("families.0x08.report_to_write missing or invalid")
        else:
            if report_map.get("0x0a") != "0x49":
                errors.append("missing report_to_write 0x0a->0x49")
            if report_map.get("0x2d") != "0x4b":
                errors.append("missing report_to_write 0x2d->0x4b")

    if not _has_point(points, "0x08", "0x49", "analog_x10", "occupied_heat_setpoint"):
        errors.append("missing occupied_heat_setpoint label for 0x49")
    if not _has_point(points, "0x08", "0x4b", "analog_x10", "occupied_cool_setpoint"):
        errors.append("missing occupied_cool_setpoint label for 0x4b")

    if errors:
        for error in errors:
            print(f"error: {error}")
        raise SystemExit(1)
    print("pointmap.json validation OK")


if __name__ == "__main__":
    main()
