#!/usr/bin/env python3
"""Correlate operator action-log events with decoded OTA frames.

For each ``mm:ss`` action in the paired action log, prints all decoded OTA
frames whose relative timestamp falls within +/- ``--window`` seconds.

Output is deterministic: actions are processed in the order they appear in
the log; frames within each window are sorted by relative timestamp.
"""
from __future__ import annotations

import argparse
import logging
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional, Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from gateway.captures.manifest import (  # noqa: E402
    CaptureEntry,
    Manifest,
    ManifestError,
    load_manifest,
)
from gateway.ota.app import OtaMsg, parse_ota_msg  # noqa: E402
from gateway.ota.ieee802154 import parse_mac_frame, strip_tap  # noqa: E402
from gateway.ota.pcapng import PcapNgReader  # noqa: E402
from gateway.ota.zigbee import parse_aps_frame, parse_nwk_frame  # noqa: E402

LOGGER = logging.getLogger(__name__)

PROFILE_ID = 0xC1E4
CLUSTER_ID = 0x0002

_ACTION_LINE_RE = re.compile(
    r"^\s*(?:T\s*=\s*)?(\d{1,3})(?::(\d{2}))?(?:\.(\d{1,3}))?\s*(.*?)\s*$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class ActionEvent:
    t_rel: float
    label: str
    raw_line: str


def parse_action_log(text: str) -> list[ActionEvent]:
    events: list[ActionEvent] = []
    for raw_line in text.splitlines():
        if not raw_line.strip() or raw_line.lstrip().startswith("#"):
            continue
        match = _ACTION_LINE_RE.match(raw_line)
        if not match:
            continue
        millis_raw = match.group(3)
        millis = int(millis_raw.ljust(3, "0")) if millis_raw else 0
        if match.group(2) is None:
            t_rel = int(match.group(1)) + millis / 1000.0
        else:
            minutes = int(match.group(1))
            seconds = int(match.group(2))
            if seconds >= 60:
                continue
            t_rel = minutes * 60.0 + seconds + millis / 1000.0
        label = match.group(4) or ""
        events.append(ActionEvent(t_rel=t_rel, label=label, raw_line=raw_line.rstrip()))
    return events


def load_action_log(path: Path) -> list[ActionEvent]:
    text = path.read_text(encoding="utf-8", errors="replace")
    return parse_action_log(text)


def decode_pcap_messages(pcap_path: Path, tap_len: int = 28) -> list[OtaMsg]:
    reader = PcapNgReader(str(pcap_path))
    messages: list[OtaMsg] = []
    t0: Optional[float] = None
    for packet in reader.packets():
        if t0 is None:
            t0 = packet.timestamp
        t_rel = packet.timestamp - t0
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
        if aps is None:
            continue
        if aps.profile_id != PROFILE_ID or aps.cluster_id != CLUSTER_ID:
            continue
        msg = parse_ota_msg(t_rel, nwk.src, nwk.dst, aps)
        if msg is None:
            continue
        messages.append(msg)
    return messages


def group_messages_by_action(
    actions: Sequence[ActionEvent],
    messages: Iterable[OtaMsg],
    *,
    window: float,
) -> list[tuple[ActionEvent, list[OtaMsg]]]:
    materialized = sorted(messages, key=lambda m: m.t_rel)
    grouped: list[tuple[ActionEvent, list[OtaMsg]]] = []
    for action in actions:
        lo = action.t_rel - window
        hi = action.t_rel + window
        in_window = [m for m in materialized if lo <= m.t_rel <= hi]
        grouped.append((action, in_window))
    return grouped


def _format_msg(msg: OtaMsg) -> str:
    parts = [
        f"t={msg.t_rel:7.3f}",
        f"dir={msg.direction}",
        f"dev=0x{msg.device_short:04x}",
        f"cmd={msg.cmd_id}",
        f"kind={msg.kind}",
    ]
    if msg.value is not None:
        parts.append(f"value={msg.value}")
    if msg.label:
        parts.append(f"label={msg.label}")
    if msg.kind == "unknown":
        parts.append(f"payload={msg.raw_rest_hex}")
    return "  ".join(parts)


def render_report(
    capture_id: str,
    grouped: Sequence[tuple[ActionEvent, list[OtaMsg]]],
    *,
    window: float,
) -> str:
    lines = [f"capture_id: {capture_id}", f"window: +/- {window:.2f}s"]
    if not grouped:
        lines.append("(no actions in log)")
        return "\n".join(lines)
    for action, frames in grouped:
        mm = int(action.t_rel) // 60
        ss = action.t_rel - mm * 60
        header = f"\n[{mm:02d}:{ss:06.3f}] {action.label}".rstrip()
        lines.append(header)
        if not frames:
            lines.append("  (no frames in window)")
            continue
        for msg in frames:
            lines.append(f"  {_format_msg(msg)}")
    return "\n".join(lines)


def _resolve_entry(manifest: Manifest, capture_id: str) -> CaptureEntry:
    entry = manifest.by_id(capture_id)
    if entry is None:
        known = ", ".join(e.capture_id for e in manifest) or "(none)"
        raise ManifestError(
            f"capture_id {capture_id!r} not found in manifest. Known: {known}"
        )
    return entry


def run(
    manifest_path: Path,
    capture_id: str,
    *,
    window: float,
    base_dir: Path = ROOT,
) -> str:
    manifest = load_manifest(manifest_path)
    entry = _resolve_entry(manifest, capture_id)
    if not entry.action_log:
        raise ManifestError(
            f"capture {capture_id!r} has no action_log; cannot correlate actions"
        )
    action_log_path = (base_dir / entry.action_log).resolve()
    if not action_log_path.exists():
        raise ManifestError(f"action log not found: {action_log_path}")
    pcap_path = (base_dir / entry.pcap).resolve()
    if not pcap_path.exists():
        raise ManifestError(f"pcap not found: {pcap_path}")
    actions = load_action_log(action_log_path)
    messages = decode_pcap_messages(pcap_path)
    grouped = group_messages_by_action(actions, messages, window=window)
    return render_report(capture_id, grouped, window=window)


def main(argv: Optional[Sequence[str]] = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--manifest", required=True, help="Path to captures/manifest.yaml")
    parser.add_argument("--capture", required=True, help="capture_id from the manifest")
    parser.add_argument(
        "--window",
        type=float,
        default=2.0,
        help="Time window (seconds) on each side of an action (default: 2.0)",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.WARNING)
    try:
        report = run(Path(args.manifest), args.capture, window=args.window)
    except ManifestError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
