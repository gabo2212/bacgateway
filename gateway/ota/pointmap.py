from __future__ import annotations
import yaml
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Any

@dataclass(frozen=True)
class MappingEntry:
    device_label: str
    prefix: int
    canonical_point: str
    kind: str
    direction: str
    status: str
    evidence: str
    scale: Optional[float] = None
    write_code: Optional[int] = None
    report_code: Optional[int] = None
    ack_code: Optional[int] = None
    eui64: Optional[str] = None
    short_addr: Optional[str] = None
    enum_map: Optional[dict[int, str]] = None

def _parse_hex(val: Any) -> Optional[int]:
    if isinstance(val, int):
        return val
    if isinstance(val, str):
        try:
            return int(val, 0)
        except ValueError:
            pass
    return None

class PointMap:
    def __init__(self, entries: list[MappingEntry]):
        self.entries = entries
        self._validate()

    def _validate(self):
        for e in self.entries:
            assert e.device_label is not None
            assert e.prefix is not None
            assert e.canonical_point is not None
            assert e.kind in ('analog_x10', 'enum', 'ack', 'unknown')
            assert e.direction in ('gw->dev', 'dev->gw', 'bidirectional')
            assert e.status in ('confirmed', 'candidate')
            if e.short_addr is not None:
                pass # not required as stable identity

    def canonicalize(self, prefix: int, code: int) -> int:
        for e in self.entries:
            if e.prefix == prefix and e.report_code == code:
                return e.write_code if e.write_code is not None else code
        return code

    def is_enum(self, prefix: int, code: int) -> bool:
        for e in self.entries:
            if e.prefix == prefix and e.kind == 'enum' and (e.write_code == code or e.report_code == code or e.ack_code == code):
                return True
        return False

    def label_for(self, prefix: int, code: int, kind: str, device_label: Optional[str] = None, direction: Optional[str] = None) -> Optional[str]:
        for e in self.entries:
            if e.prefix != prefix:
                continue
            if e.kind != kind:
                continue
            if e.write_code != code and e.report_code != code and e.ack_code != code:
                if not (code == e.write_code or code == e.report_code): # fallback if only one is defined and the other isn't matched
                    pass
                continue
            if device_label and e.device_label and e.device_label != device_label:
                continue
            if direction and e.direction and e.direction != 'bidirectional' and direction != e.direction:
                continue
            return e.canonical_point
        return None

def load_pointmap(path: Path) -> PointMap:
    try:
        raw = path.read_text(encoding='utf-8')
        data = yaml.safe_load(raw) or {}
    except (OSError, yaml.YAMLError):
        return PointMap([])
    
    entries = []
    for row in data.get('mappings', []):
        entries.append(MappingEntry(
            device_label=row.get('device_label', ''),
            prefix=_parse_hex(row.get('prefix')),
            write_code=_parse_hex(row.get('write_code')),
            report_code=_parse_hex(row.get('report_code')),
            ack_code=_parse_hex(row.get('ack_code')),
            canonical_point=row.get('canonical_point', ''),
            kind=row.get('kind', 'unknown'),
            scale=row.get('scale'),
            direction=row.get('direction', 'bidirectional'),
            status=row.get('status', 'candidate'),
            evidence=row.get('evidence', ''),
            eui64=row.get('eui64'),
            short_addr=row.get('short_addr'),
            enum_map=row.get('enum_map')
        ))
    return PointMap(entries)

PointMap.from_json = load_pointmap
