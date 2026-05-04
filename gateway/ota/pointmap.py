from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


def _parse_hex(value: object) -> Optional[int]:
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        text = value.strip().lower()
        try:
            return int(text, 0)
        except ValueError:
            return None
    return None


def _format_hex(value: int) -> str:
    return f"0x{value:02x}"


@dataclass(frozen=True)
class PointKey:
    prefix: int
    code: int
    kind: str


@dataclass(frozen=True)
class PointDef:
    prefix: int
    code: int
    kind: str
    label: str


class PointMap:
    def __init__(
        self,
        enum_codes: dict[int, set[int]],
        report_to_write: dict[int, dict[int, int]],
        points: dict[PointKey, PointDef],
    ) -> None:
        self._enum_codes = enum_codes
        self._report_to_write = report_to_write
        self._points = points

    @classmethod
    def from_json(cls, path: Path) -> PointMap:
        try:
            raw = path.read_text(encoding="utf-8")
        except OSError:
            return cls({}, {}, {})
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return cls({}, {}, {})
        if not isinstance(data, dict):
            return cls({}, {}, {})

        families = data.get("families", {})
        points = data.get("points", [])

        enum_codes: dict[int, set[int]] = {}
        report_to_write: dict[int, dict[int, int]] = {}
        if isinstance(families, dict):
            for fam_key, fam_info in families.items():
                prefix = _parse_hex(fam_key)
                if prefix is None or not isinstance(fam_info, dict):
                    continue
                enum_list = fam_info.get("enum_codes", [])
                if isinstance(enum_list, list):
                    enum_set = {
                        code for code in (_parse_hex(item) for item in enum_list) if code is not None
                    }
                    enum_codes[prefix] = enum_set
                report_map = fam_info.get("report_to_write", {})
                if isinstance(report_map, dict):
                    converted: dict[int, int] = {}
                    for report_key, write_value in report_map.items():
                        report = _parse_hex(report_key)
                        write = _parse_hex(write_value)
                        if report is None or write is None:
                            continue
                        converted[report] = write
                    report_to_write[prefix] = converted

        point_defs: dict[PointKey, PointDef] = {}
        if isinstance(points, list):
            for entry in points:
                if not isinstance(entry, dict):
                    continue
                prefix = _parse_hex(entry.get("family"))
                code = _parse_hex(entry.get("code"))
                kind = entry.get("kind")
                label = entry.get("label")
                if (
                    prefix is None
                    or code is None
                    or not isinstance(kind, str)
                    or not isinstance(label, str)
                ):
                    continue
                key = PointKey(prefix=prefix, code=code, kind=kind)
                point_defs[key] = PointDef(prefix=prefix, code=code, kind=kind, label=label)

        return cls(enum_codes, report_to_write, point_defs)

    def canonicalize(self, prefix: int, code: int) -> int:
        mapping = self._report_to_write.get(prefix)
        if not mapping:
            return code
        return mapping.get(code, code)

    def is_enum(self, prefix: int, code: int) -> bool:
        return code in self._enum_codes.get(prefix, set())

    def label_for(self, prefix: int, code: int, kind: str) -> Optional[str]:
        entry = self._points.get(PointKey(prefix=prefix, code=code, kind=kind))
        return entry.label if entry else None

    def ensure_placeholder(self, prefix: int, code: int, kind: str) -> None:
        key = PointKey(prefix=prefix, code=code, kind=kind)
        if key in self._points:
            return
        label = f"auto_{_format_hex(prefix)}_{_format_hex(code)}_{kind}".lower()
        self._points[key] = PointDef(prefix=prefix, code=code, kind=kind, label=label)
