#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Optional


def _parse_hex(value: object) -> int:
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        return int(value, 0)
    raise ValueError("invalid hex value")


def _format_hex(value: int) -> str:
    return f"0x{value:02x}"


def load_pointmap(path: Path) -> dict[str, Any]:
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


def _sorted_family_keys(families: dict[str, Any]) -> list[str]:
    return sorted(families.keys(), key=lambda key: _parse_hex(key))


def _normalize_families(families: dict[str, Any]) -> dict[str, Any]:
    normalized: dict[str, Any] = {}
    for family_key in _sorted_family_keys(families):
        info = families.get(family_key, {})
        if not isinstance(info, dict):
            continue
        enum_codes = info.get("enum_codes", [])
        report_to_write = info.get("report_to_write", {})
        enum_list = []
        if isinstance(enum_codes, list):
            parsed_codes = []
            for code in enum_codes:
                try:
                    parsed_codes.append(_format_hex(_parse_hex(code)))
                except ValueError:
                    continue
            enum_list = sorted(set(parsed_codes), key=lambda value: _parse_hex(value))
        report_map: dict[str, str] = {}
        if isinstance(report_to_write, dict):
            items = []
            for report, write in report_to_write.items():
                try:
                    items.append((_parse_hex(report), _parse_hex(write)))
                except ValueError:
                    continue
            for report, write in sorted(items, key=lambda item: item[0]):
                report_map[_format_hex(report)] = _format_hex(write)
        normalized[family_key.lower()] = {
            "enum_codes": enum_list,
            "report_to_write": report_map,
        }
    return normalized


def _normalize_points(points: list[Any]) -> list[dict[str, str]]:
    normalized: list[dict[str, str]] = []
    for entry in points:
        if not isinstance(entry, dict):
            continue
        family = entry.get("family")
        code = entry.get("code")
        kind = entry.get("kind")
        label = entry.get("label")
        if not isinstance(family, str) or not isinstance(code, str):
            continue
        if not isinstance(kind, str) or not isinstance(label, str):
            continue
        normalized.append(
            {
                "family": family.lower(),
                "code": code.lower(),
                "kind": kind,
                "label": label,
            }
        )
    normalized.sort(key=lambda item: (_parse_hex(item["family"]), _parse_hex(item["code"]), item["kind"]))
    return normalized


def normalize_pointmap(data: dict[str, Any]) -> dict[str, Any]:
    families = data.get("families", {})
    points = data.get("points", [])
    normalized = {
        "families": _normalize_families(families if isinstance(families, dict) else {}),
        "points": _normalize_points(points if isinstance(points, list) else []),
    }
    return normalized


def save_pointmap(path: Path, data: dict[str, Any]) -> None:
    normalized = normalize_pointmap(data)
    path.write_text(json.dumps(normalized, indent=2) + "\n", encoding="utf-8")


def _find_point(points: list[dict[str, str]], family: str, code: str, kind: str) -> Optional[int]:
    for idx, entry in enumerate(points):
        if entry.get("family") == family and entry.get("code") == code and entry.get("kind") == kind:
            return idx
    return None


def add_point(data: dict[str, Any], family: int, code: int, kind: str, label: str) -> bool:
    points = data.setdefault("points", [])
    if not isinstance(points, list):
        raise ValueError("points must be a list")
    family_hex = _format_hex(family)
    code_hex = _format_hex(code)
    idx = _find_point(points, family_hex, code_hex, kind)
    if idx is not None:
        existing = points[idx]
        if existing.get("label") == label:
            return False
        raise ValueError("point already exists with different label")
    points.append({"family": family_hex, "code": code_hex, "kind": kind, "label": label})
    return True


def _has_cycle(report_to_write: dict[str, str], report: int, write: int) -> bool:
    visited = {report}
    current = write
    while True:
        if current in visited:
            return True
        visited.add(current)
        next_code = report_to_write.get(_format_hex(current))
        if next_code is None:
            return False
        current = _parse_hex(next_code)


def add_report_map(data: dict[str, Any], family: int, report: int, write: int) -> bool:
    families = data.setdefault("families", {})
    if not isinstance(families, dict):
        raise ValueError("families must be a dict")
    family_hex = _format_hex(family)
    info = families.setdefault(family_hex, {"enum_codes": [], "report_to_write": {}})
    if not isinstance(info, dict):
        raise ValueError("family info must be a dict")
    report_map = info.setdefault("report_to_write", {})
    if not isinstance(report_map, dict):
        raise ValueError("report_to_write must be a dict")
    report_hex = _format_hex(report)
    write_hex = _format_hex(write)
    existing = report_map.get(report_hex)
    if existing is not None:
        if existing == write_hex:
            return False
        raise ValueError("report_to_write already set for report code")
    if _has_cycle(report_map, report, write):
        raise ValueError("report_to_write mapping would create a cycle")
    report_map[report_hex] = write_hex
    return True


def rename_label(
    data: dict[str, Any],
    family: int,
    code: int,
    kind: str,
    label: str,
    force: bool,
) -> bool:
    points = data.setdefault("points", [])
    if not isinstance(points, list):
        raise ValueError("points must be a list")
    family_hex = _format_hex(family)
    code_hex = _format_hex(code)
    idx = _find_point(points, family_hex, code_hex, kind)
    if idx is None:
        raise ValueError("point not found")
    for entry in points:
        if entry is points[idx]:
            continue
        if entry.get("label") == label:
            if not force:
                raise ValueError("label already used; re-run with --force to override")
            print("warning: label reused across multiple codes", file=sys.stderr)
            break
    if points[idx].get("label") == label:
        return False
    points[idx]["label"] = label
    return True


def list_points(data: dict[str, Any], family: Optional[int]) -> list[str]:
    points = data.get("points", [])
    if not isinstance(points, list):
        return []
    output: list[str] = []
    for entry in _normalize_points(points):
        if family is not None and _parse_hex(entry["family"]) != family:
            continue
        output.append(
            " ".join(
                [
                    f"family={entry['family']}",
                    f"code={entry['code']}",
                    f"kind={entry['kind']}",
                    f"label={entry['label']}",
                ]
            )
        )
    return output


def validate_pointmap(data: dict[str, Any], allow_multi_label: bool) -> list[str]:
    errors: list[str] = []
    points = _normalize_points(data.get("points", [])) if isinstance(data.get("points", []), list) else []
    seen: set[tuple[str, str, str]] = set()
    labels: dict[str, list[tuple[str, str, str]]] = {}
    for entry in points:
        key = (entry["family"], entry["code"], entry["kind"])
        if key in seen:
            errors.append(f"duplicate point entry {key}")
        seen.add(key)
        labels.setdefault(entry["label"], []).append(key)
    for label, entries in labels.items():
        if len(entries) > 1 and not allow_multi_label:
            errors.append(f"label reused across multiple codes: {label}")
    families = data.get("families", {})
    if isinstance(families, dict):
        for family, info in families.items():
            if not isinstance(info, dict):
                errors.append(f"family {family} is not a dict")
                continue
            report_map = info.get("report_to_write", {})
            if isinstance(report_map, dict):
                for report, write in report_map.items():
                    try:
                        report_val = _parse_hex(report)
                        write_val = _parse_hex(write)
                    except ValueError:
                        errors.append(f"invalid report_to_write entry in family {family}")
                        continue
                    if _has_cycle(report_map, report_val, write_val):
                        errors.append(f"cycle detected in family {family} report_to_write")
                        break
            else:
                errors.append(f"family {family} report_to_write is not a dict")
    else:
        errors.append("families is not a dict")
    return errors


def main() -> None:
    parser = argparse.ArgumentParser(description="Edit gateway/ota/pointmap.json safely")
    parser.add_argument("--map", default="gateway/ota/pointmap.json", help="Path to pointmap.json")
    subparsers = parser.add_subparsers(dest="command", required=True)

    add_point_parser = subparsers.add_parser("add-point", help="Add a point entry")
    add_point_parser.add_argument("--family", required=True)
    add_point_parser.add_argument("--code", required=True)
    add_point_parser.add_argument("--kind", required=True)
    add_point_parser.add_argument("--label", required=True)

    add_report_parser = subparsers.add_parser("add-report-map", help="Add report->write mapping")
    add_report_parser.add_argument("--family", required=True)
    add_report_parser.add_argument("--report-code", required=True)
    add_report_parser.add_argument("--write-code", required=True)

    rename_parser = subparsers.add_parser("rename-label", help="Rename a point label")
    rename_parser.add_argument("--family", required=True)
    rename_parser.add_argument("--code", required=True)
    rename_parser.add_argument("--kind", required=True)
    rename_parser.add_argument("--label", required=True)
    rename_parser.add_argument("--force", action="store_true")

    list_parser = subparsers.add_parser("list", help="List points")
    list_parser.add_argument("--family")

    validate_parser = subparsers.add_parser("validate", help="Validate pointmap.json")
    validate_parser.add_argument("--allow-multi-code-label", action="store_true")

    args = parser.parse_args()
    path = Path(args.map)
    data = load_pointmap(path)

    changed = False
    try:
        if args.command == "add-point":
            changed = add_point(
                data,
                _parse_hex(args.family),
                _parse_hex(args.code),
                args.kind,
                args.label,
            )
        elif args.command == "add-report-map":
            changed = add_report_map(
                data,
                _parse_hex(args.family),
                _parse_hex(args.report_code),
                _parse_hex(args.write_code),
            )
        elif args.command == "rename-label":
            changed = rename_label(
                data,
                _parse_hex(args.family),
                _parse_hex(args.code),
                args.kind,
                args.label,
                args.force,
            )
        elif args.command == "list":
            family = _parse_hex(args.family) if args.family else None
            for line in list_points(data, family):
                print(line)
            return
        elif args.command == "validate":
            errors = validate_pointmap(data, args.allow_multi_code_label)
            if errors:
                for error in errors:
                    print(f"error: {error}")
                raise SystemExit(1)
            print("pointmap.json validation OK")
            return
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc

    if changed:
        save_pointmap(path, data)
        print("updated pointmap.json")
    else:
        print("no changes")


if __name__ == "__main__":
    main()
