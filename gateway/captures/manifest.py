from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Iterator, Optional

import yaml

LOGGER = logging.getLogger(__name__)

ALLOWED_TASK_TYPES: tuple[str, ...] = (
    "idle",
    "ping",
    "identify",
    "point_viewer",
    "setpoint_write",
    "occupancy_write",
    "outdoor_temp_override",
    "mode_fan",
    "unknown",
)

_CAPTURE_ID_RE = re.compile(r"[^a-zA-Z0-9_]+")
_SHORT_ADDR_RE = re.compile(r"^0x[0-9a-fA-F]{1,4}$")
_EUI64_RE = re.compile(r"^[0-9a-fA-F:.\- ]{11,}$")


class ManifestError(ValueError):
    """Raised when a manifest file is structurally invalid."""


@dataclass(frozen=True)
class CaptureEntry:
    capture_id: str
    pcap: str
    action_log: Optional[str] = None
    device_label: Optional[str] = None
    short_addr: Optional[str] = None
    eui64: Optional[str] = None
    task_type: str = "unknown"
    notes: Optional[str] = None


@dataclass(frozen=True)
class Manifest:
    captures: tuple[CaptureEntry, ...] = field(default_factory=tuple)
    source: Optional[Path] = None

    def by_id(self, capture_id: str) -> Optional[CaptureEntry]:
        for entry in self.captures:
            if entry.capture_id == capture_id:
                return entry
        return None

    def __iter__(self) -> Iterator[CaptureEntry]:
        return iter(self.captures)


def _log_debug(event: str, **fields: object) -> None:
    if LOGGER.isEnabledFor(logging.DEBUG):
        LOGGER.debug(event, extra={"event": event, **fields})


def _validate_str(value: object, *, field_name: str, where: str) -> str:
    if not isinstance(value, str):
        raise ManifestError(f"{where}: '{field_name}' must be a string, got {type(value).__name__}")
    return value


def _validate_optional_str(
    value: object, *, field_name: str, where: str
) -> Optional[str]:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ManifestError(f"{where}: '{field_name}' must be a string or null")
    return value


def _validate_task_type(value: object, *, where: str) -> str:
    if value is None:
        return "unknown"
    if not isinstance(value, str):
        raise ManifestError(f"{where}: 'task_type' must be a string")
    if value not in ALLOWED_TASK_TYPES:
        raise ManifestError(
            f"{where}: 'task_type' must be one of {sorted(ALLOWED_TASK_TYPES)}, got {value!r}"
        )
    return value


def _validate_short_addr(value: object, *, where: str) -> Optional[str]:
    if value is None:
        return None
    if not isinstance(value, str) or not _SHORT_ADDR_RE.match(value):
        raise ManifestError(
            f"{where}: 'short_addr' must look like '0x0001', got {value!r}"
        )
    return value


def _validate_eui64(value: object, *, where: str) -> Optional[str]:
    if value is None:
        return None
    if not isinstance(value, str) or not _EUI64_RE.match(value):
        raise ManifestError(f"{where}: 'eui64' invalid: {value!r}")
    return value


def _entry_from_mapping(raw: object, *, index: int) -> CaptureEntry:
    where = f"captures[{index}]"
    if not isinstance(raw, dict):
        raise ManifestError(f"{where}: must be a mapping, got {type(raw).__name__}")
    capture_id = _validate_str(raw.get("capture_id"), field_name="capture_id", where=where)
    pcap = _validate_str(raw.get("pcap"), field_name="pcap", where=where)
    return CaptureEntry(
        capture_id=capture_id,
        pcap=pcap,
        action_log=_validate_optional_str(
            raw.get("action_log"), field_name="action_log", where=where
        ),
        device_label=_validate_optional_str(
            raw.get("device_label"), field_name="device_label", where=where
        ),
        short_addr=_validate_short_addr(raw.get("short_addr"), where=where),
        eui64=_validate_eui64(raw.get("eui64"), where=where),
        task_type=_validate_task_type(raw.get("task_type"), where=where),
        notes=_validate_optional_str(raw.get("notes"), field_name="notes", where=where),
    )


def parse_manifest(data: object, *, source: Optional[Path] = None) -> Manifest:
    if data is None:
        return Manifest(captures=(), source=source)
    if not isinstance(data, dict):
        raise ManifestError(f"manifest root must be a mapping, got {type(data).__name__}")
    raw_captures = data.get("captures", [])
    if raw_captures is None:
        raw_captures = []
    if not isinstance(raw_captures, list):
        raise ManifestError("'captures' must be a list")
    entries: list[CaptureEntry] = []
    seen_ids: set[str] = set()
    for index, raw in enumerate(raw_captures):
        entry = _entry_from_mapping(raw, index=index)
        if entry.capture_id in seen_ids:
            raise ManifestError(
                f"captures[{index}]: duplicate capture_id {entry.capture_id!r}"
            )
        seen_ids.add(entry.capture_id)
        entries.append(entry)
    return Manifest(captures=tuple(entries), source=source)


def load_manifest(path: str | Path) -> Manifest:
    p = Path(path)
    if not p.exists():
        raise ManifestError(f"manifest file not found: {p}")
    with p.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle)
    _log_debug("manifest.load", path=str(p), entries=len(data.get("captures", []) or []) if isinstance(data, dict) else 0)
    return parse_manifest(data, source=p)


def suggest_capture_id(pcap_path: str | Path) -> str:
    stem = Path(pcap_path).stem
    cleaned = _CAPTURE_ID_RE.sub("_", stem).strip("_")
    if not cleaned:
        return "capture"
    if cleaned[0].isdigit():
        cleaned = f"c_{cleaned}"
    return cleaned


def find_action_log(pcap_path: str | Path) -> Optional[Path]:
    p = Path(pcap_path)
    sibling = p.with_suffix(".txt")
    if sibling.exists():
        return sibling
    parent = p.parent
    candidates: Iterable[Path] = parent.glob("*.txt") if parent.exists() else ()
    stem_lower = p.stem.lower()
    for candidate in candidates:
        if candidate.stem.lower() == stem_lower:
            return candidate
    return None


_TASK_TYPE_KEYWORDS: tuple[tuple[str, str], ...] = (
    ("setpoint", "setpoint_write"),
    ("occupancy", "occupancy_write"),
    ("occupied", "occupancy_write"),
    ("outdoor", "outdoor_temp_override"),
    ("fan", "mode_fan"),
    ("mode", "mode_fan"),
    ("identify", "identify"),
    ("ping", "ping"),
    ("idle", "idle"),
    ("baseline", "idle"),
    ("point_viewer", "point_viewer"),
    ("viewer", "point_viewer"),
)


def suggest_task_type(pcap_path: str | Path, action_log: Optional[Path] = None) -> str:
    haystacks: list[str] = [str(pcap_path).lower()]
    if action_log is not None:
        haystacks.append(str(action_log).lower())
        try:
            haystacks.append(action_log.read_text(encoding="utf-8", errors="replace").lower())
        except OSError:
            pass
    blob = "\n".join(haystacks)
    for keyword, task_type in _TASK_TYPE_KEYWORDS:
        if keyword in blob:
            return task_type
    return "unknown"
