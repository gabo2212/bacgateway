#!/usr/bin/env python3
"""Extract candidate OTA point mappings from manifest-listed captures."""
from __future__ import annotations

import argparse
import csv
import io
import logging
import sys
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable, Optional, Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from gateway.captures.manifest import CaptureEntry, Manifest, ManifestError, load_manifest  # noqa: E402
from gateway.ota.app import OtaMsg, parse_ota_msg  # noqa: E402
from gateway.ota.ieee802154 import parse_mac_frame, strip_tap  # noqa: E402
from gateway.ota.pcapng import PcapNgReader  # noqa: E402
from gateway.ota.pointmap import PointMap  # noqa: E402
from gateway.ota.zigbee import parse_aps_frame, parse_nwk_frame  # noqa: E402
from tools.ota_action_windows import ActionEvent, group_messages_by_action, load_action_log  # noqa: E402

LOGGER = logging.getLogger(__name__)
PROFILE_ID = 0xC1E4
CLUSTER_ID = 0x0002
CMD_POINT_DATA = 0x02
CMD_ACK = 0x03
DEFAULT_WINDOW = 2.0


@dataclass(frozen=True)
class DecodedOtaFrame:
    msg: OtaMsg
    src_short: int
    dst_short: int


@dataclass(frozen=True)
class MappingRow:
    capture_id: str
    device_label: str
    short_addr: str
    action_label: str
    action_window: str
    t_rel: float
    direction: str
    source_short: str
    destination_short: str
    cmd: int
    prefix: str
    code: str
    raw_payload: str
    decoded_kind: str
    decoded_value: str
    candidate_point: str
    confidence: str
    evidence_note: str

    def to_csv_dict(self) -> dict[str, object]:
        data = asdict(self)
        data["t_rel"] = f"{self.t_rel:.3f}"
        return data


@dataclass(frozen=True)
class UnknownPayloadGroup:
    capture_id: str
    device_label: str
    direction: str
    payload_len: int
    prefix: str
    code: str
    raw_payload: str
    count: int


@dataclass(frozen=True)
class MappingReport:
    rows: tuple[MappingRow, ...]
    unknown_groups: tuple[UnknownPayloadGroup, ...]
    captures_processed: tuple[str, ...]
    errors: tuple[str, ...]


def _hex(value: Optional[int], width: int) -> str:
    return "" if value is None else f"0x{value:0{width}x}"


def _value_text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, float):
        return f"{value:g}"
    return str(value)


def infer_candidate_point(action_text: str) -> str:
    text = action_text.lower().replace("_", " ").replace("-", " ")
    compact = "".join(ch for ch in text if ch.isalnum())
    if any(token in text for token in ("heating", "occ heat", "heat")) or "heatsp" in compact:
        return "occHeatSP"
    if (
        any(token in text for token in ("cooling", "occ cool", "cool", "setpoint"))
        or "coolsp" in compact
    ):
        return "occCoolSP"
    if any(token in text for token in ("occupancy", "occupied", "unoccupied", "standby")):
        return "occupancyCmd"
    if any(token in text for token in ("outdoor", "outside")):
        return "outdoorTemp"
    return "unknown"


def decode_pcap_frames(pcap_path: Path, tap_len: int = 28) -> list[DecodedOtaFrame]:
    reader = PcapNgReader(str(pcap_path))
    frames: list[DecodedOtaFrame] = []
    t0: Optional[float] = None
    for packet in reader.packets():
        if t0 is None:
            t0 = packet.timestamp
        tapped = strip_tap(packet.data, tap_len=tap_len)
        if tapped is None:
            continue
        mac = parse_mac_frame(tapped)
        if mac is None:
            continue
        nwk = parse_nwk_frame(mac.payload)
        if nwk is None and len(mac.payload) >= 2:
            nwk = parse_nwk_frame(mac.payload[:-2])
        if nwk is None:
            continue
        aps = parse_aps_frame(nwk.payload)
        if aps is None or aps.profile_id != PROFILE_ID or aps.cluster_id != CLUSTER_ID:
            continue
        msg = parse_ota_msg(packet.timestamp - t0, nwk.src, nwk.dst, aps)
        if msg is None:
            continue
        frames.append(DecodedOtaFrame(msg=msg, src_short=nwk.src, dst_short=nwk.dst))
    return frames


def _confidence_and_note(
    msg: OtaMsg,
    candidate_point: str,
    point_map: PointMap,
    known_text: str,
) -> tuple[str, str]:
    label = point_map.label_for(msg.prefix, msg.code, msg.kind) if msg.prefix is not None and msg.code is not None else None
    if label and label.lower() in known_text.lower() and not _is_candidate_label(label):
        return "confirmed", f"pointmap label {label!r} is documented in known_ota_mappings.md"
    if label:
        return "pointmap", f"pointmap label {label!r}; not promoted by this report"
    if candidate_point != "unknown":
        return "candidate", "inferred from action text only; requires evidence review"
    if msg.kind == "unknown":
        return "unknown", "unknown decoded payload preserved for triage"
    return "unknown", "no action text or pointmap label supports a mapping"


def _is_candidate_label(label: str) -> bool:
    lowered = label.lower()
    return lowered.startswith(("auto_", "sp_", "occ_", "candidate_")) or "candidate" in lowered


def frame_to_row(
    entry: CaptureEntry,
    frame: DecodedOtaFrame,
    *,
    action: Optional[ActionEvent],
    window: float,
    point_map: PointMap,
    known_text: str,
) -> MappingRow:
    msg = frame.msg
    action_label = action.label if action is not None else "(unwindowed)"
    if action is None:
        action_window = "unwindowed"
    else:
        action_window = f"{action.t_rel - window:.3f}..{action.t_rel + window:.3f}"
    candidate_point = infer_candidate_point(action_label)
    confidence, note = _confidence_and_note(msg, candidate_point, point_map, known_text)
    if candidate_point == "unknown" and msg.label and confidence in {"confirmed", "pointmap"}:
        candidate_point = msg.label
    return MappingRow(
        capture_id=entry.capture_id,
        device_label=entry.device_label or "",
        short_addr=entry.short_addr or _hex(msg.device_short, 4),
        action_label=action_label,
        action_window=action_window,
        t_rel=msg.t_rel,
        direction=msg.direction,
        source_short=_hex(frame.src_short, 4),
        destination_short=_hex(frame.dst_short, 4),
        cmd=msg.cmd_id,
        prefix=_hex(msg.prefix, 2),
        code=_hex(msg.code, 2),
        raw_payload=msg.raw_rest_hex,
        decoded_kind=msg.kind,
        decoded_value=_value_text(msg.value),
        candidate_point=candidate_point,
        confidence=confidence,
        evidence_note=note,
    )


def rows_from_frames(
    entry: CaptureEntry,
    frames: Sequence[DecodedOtaFrame],
    *,
    actions: Sequence[ActionEvent],
    window: float,
    point_map: PointMap,
    known_text: str,
) -> list[MappingRow]:
    by_msg_id = {id(frame.msg): frame for frame in frames}
    rows: list[MappingRow] = []
    if actions:
        grouped = group_messages_by_action(actions, [frame.msg for frame in frames], window=window)
        for action, messages in grouped:
            for msg in messages:
                if msg.cmd_id not in {CMD_POINT_DATA, CMD_ACK}:
                    continue
                rows.append(frame_to_row(entry, by_msg_id[id(msg)], action=action, window=window, point_map=point_map, known_text=known_text))
        return rows
    for frame in sorted(frames, key=lambda item: item.msg.t_rel):
        if frame.msg.cmd_id in {CMD_POINT_DATA, CMD_ACK}:
            rows.append(frame_to_row(entry, frame, action=None, window=window, point_map=point_map, known_text=known_text))
    return rows


def group_unknown_payloads(rows: Iterable[MappingRow]) -> list[UnknownPayloadGroup]:
    counts: Counter[tuple[str, str, str, int, str, str, str]] = Counter()
    for row in rows:
        if row.decoded_kind != "unknown":
            continue
        key = (
            row.capture_id,
            row.device_label,
            row.direction,
            len(row.raw_payload) // 2,
            row.prefix,
            row.code,
            row.raw_payload,
        )
        counts[key] += 1
    return [
        UnknownPayloadGroup(*key, count=count)
        for key, count in sorted(counts.items(), key=lambda item: (item[0], -item[1]))
    ]


def _resolve_path(base_dir: Path, value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else (base_dir / path).resolve()


def build_mapping_report(
    manifest: Manifest,
    *,
    capture_id: Optional[str] = None,
    window: float = DEFAULT_WINDOW,
    base_dir: Path = ROOT,
) -> MappingReport:
    point_map = PointMap.from_json(base_dir / "gateway" / "ota" / "pointmap.yaml")
    known_path = base_dir / "docs" / "ota" / "known_ota_mappings.md"
    known_text = known_path.read_text(encoding="utf-8", errors="replace") if known_path.exists() else ""
    entries = list(manifest)
    if capture_id is not None:
        found = manifest.by_id(capture_id)
        if found is None:
            known = ", ".join(entry.capture_id for entry in manifest) or "(none)"
            raise ManifestError(f"capture_id {capture_id!r} not found in manifest. Known: {known}")
        entries = [found]
    rows: list[MappingRow] = []
    processed: list[str] = []
    errors: list[str] = []
    for entry in entries:
        pcap_path = _resolve_path(base_dir, entry.pcap)
        if not pcap_path.exists():
            errors.append(f"{entry.capture_id}: pcap not found: {pcap_path}")
            continue
        try:
            frames = decode_pcap_frames(pcap_path)
            actions: list[ActionEvent] = []
            if entry.action_log:
                action_path = _resolve_path(base_dir, entry.action_log)
                if action_path.exists():
                    actions = load_action_log(action_path)
                else:
                    errors.append(f"{entry.capture_id}: action log not found: {action_path}")
            rows.extend(rows_from_frames(entry, frames, actions=actions, window=window, point_map=point_map, known_text=known_text))
            processed.append(entry.capture_id)
        except (OSError, ValueError) as exc:
            LOGGER.warning("ota_mapping.capture_failed", extra={"capture_id": entry.capture_id, "error": str(exc)})
            errors.append(f"{entry.capture_id}: {exc}")
    return MappingReport(rows=tuple(rows), unknown_groups=tuple(group_unknown_payloads(rows)), captures_processed=tuple(processed), errors=tuple(errors))


CSV_FIELDS = tuple(MappingRow.__dataclass_fields__.keys())


def format_csv(rows: Sequence[MappingRow]) -> str:
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=CSV_FIELDS, lineterminator="\n")
    writer.writeheader()
    for row in rows:
        writer.writerow(row.to_csv_dict())
    return buffer.getvalue()


def _md_table(headers: Sequence[str], rows: Sequence[Sequence[object]]) -> list[str]:
    lines = ["| " + " | ".join(headers) + " |", "| " + " | ".join("---" for _ in headers) + " |"]
    for row in rows:
        lines.append("| " + " | ".join(str(cell).replace("\n", " ") for cell in row) + " |")
    return lines


def format_markdown(report: MappingReport) -> str:
    rows = sorted(report.rows, key=lambda r: (r.capture_id, r.action_label, r.t_rel, r.cmd, r.prefix, r.code))
    lines = ["# Candidate OTA Mappings", "", "Generated by `tools/ota_mapping_report.py`.", "", "## Summary", ""]
    lines.append(f"- Captures processed: {len(report.captures_processed)} ({', '.join(report.captures_processed) or 'none'})")
    lines.append(f"- Evidence rows: {len(rows)}")
    lines.append(f"- Unknown payload groups: {len(report.unknown_groups)}")
    if report.errors:
        lines.extend(["", "## Errors", ""])
        lines.extend(f"- {error}" for error in report.errors)
    for title, confidence in (("Confirmed mappings", "confirmed"), ("Candidate mappings", "candidate"), ("Pointmap-known mappings", "pointmap")):
        subset = [row for row in rows if row.confidence == confidence]
        lines.extend(["", f"## {title}", ""])
        if not subset:
            lines.append("No rows in this category.")
            continue
        lines.extend(_md_table(["capture", "action", "dir", "cmd", "prefix", "code", "kind", "value", "candidate", "note"], [[r.capture_id, r.action_label, r.direction, r.cmd, r.prefix or "-", r.code or "-", r.decoded_kind, r.decoded_value or "-", r.candidate_point, r.evidence_note] for r in subset[:200]]))
        if len(subset) > 200:
            lines.append(f"\n_Truncated: {len(subset) - 200} additional rows omitted._")
    lines.extend(["", "## Unknown payload groups", ""])
    if not report.unknown_groups:
        lines.append("No unknown payload groups found.")
    else:
        lines.extend(_md_table(["capture", "device", "dir", "len", "prefix", "code", "raw", "count"], [[g.capture_id, g.device_label or "-", g.direction, g.payload_len, g.prefix or "-", g.code or "-", g.raw_payload or "-", g.count] for g in report.unknown_groups]))
    lines.extend(["", "## Missing evidence / recommended next captures", "", "- Do not promote candidate rows automatically; review action-window evidence first.", "- Prioritize repeated unknown payload groups that appear inside setpoint or occupancy action windows.", "- Captures without action logs provide unwindowed evidence only; collect or recover action logs before promotion.", "- If a candidate point lacks both action-log support and repeated A/B evidence, keep it candidate.", ""])
    return "\n".join(lines)


def format_text(report: MappingReport) -> str:
    return "\n".join([f"captures_processed={len(report.captures_processed)}", f"rows={len(report.rows)}", f"unknown_groups={len(report.unknown_groups)}", *[f"error={e}" for e in report.errors]]) + "\n"


def _write_or_print(text: str, output: Optional[str]) -> None:
    if output:
        path = Path(output)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8", newline="\n")
    else:
        print(text, end="")


def main(argv: Optional[Sequence[str]] = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True, help="Path to captures/manifest.yaml")
    parser.add_argument("--capture", help="Optional capture_id filter")
    parser.add_argument("--window", type=float, default=DEFAULT_WINDOW, help="Action window seconds on each side (default: 2.0)")
    parser.add_argument("--format", choices=["text", "markdown", "csv"], default="text")
    parser.add_argument("--output", help="Optional output path")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.WARNING)
    try:
        report = build_mapping_report(load_manifest(args.manifest), capture_id=args.capture, window=args.window)
    except ManifestError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if args.format == "markdown":
        text = format_markdown(report)
    elif args.format == "csv":
        text = format_csv(report.rows)
    else:
        text = format_text(report)
    _write_or_print(text, args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())