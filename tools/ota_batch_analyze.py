#!/usr/bin/env python3
"""Batch-decode pcapng captures listed in a manifest and print summaries."""
from __future__ import annotations

import argparse
import json
import logging
import sys
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Iterable, Iterator, Optional, Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from gateway.captures.manifest import CaptureEntry, Manifest, load_manifest  # noqa: E402
from gateway.ota.app import OtaMsg, parse_ota_msg  # noqa: E402
from gateway.ota.ieee802154 import parse_mac_frame, strip_tap  # noqa: E402
from gateway.ota.pcapng import PcapNgReader  # noqa: E402
from gateway.ota.zigbee import parse_aps_frame, parse_nwk_frame  # noqa: E402

LOGGER = logging.getLogger(__name__)

PROFILE_ID = 0xC1E4
CLUSTER_ID = 0x0002


@dataclass
class CaptureSummary:
    capture_id: str
    pcap: str
    pcap_exists: bool = True
    total_frames: int = 0
    matching_vendor_frames: int = 0
    short_addrs: list[str] = field(default_factory=list)
    eui64s: list[str] = field(default_factory=list)
    cmd_counts: dict[str, int] = field(default_factory=dict)
    analog_x10_events: int = 0
    enum_events: int = 0
    ack_events: int = 0
    unknown_events: int = 0
    error: Optional[str] = None

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass
class _Accumulator:
    total_frames: int = 0
    matching_vendor_frames: int = 0
    short_addrs: set[int] = field(default_factory=set)
    eui64s: set[int] = field(default_factory=set)
    cmd_counts: Counter[int] = field(default_factory=Counter)
    analog_x10_events: int = 0
    enum_events: int = 0
    ack_events: int = 0
    unknown_events: int = 0


def _format_short(addr: int) -> str:
    return f"0x{addr:04x}"


def _format_eui64(addr: int) -> str:
    return f"0x{addr:016x}"


def _accumulate_message(acc: _Accumulator, msg: OtaMsg) -> None:
    acc.matching_vendor_frames += 1
    acc.short_addrs.add(msg.device_short)
    acc.cmd_counts[msg.cmd_id] += 1
    if msg.kind == "analog_x10":
        acc.analog_x10_events += 1
    elif msg.kind == "enum":
        acc.enum_events += 1
    elif msg.kind == "ack":
        acc.ack_events += 1
    elif msg.kind == "unknown":
        acc.unknown_events += 1


def summarize_messages(
    messages: Iterable[OtaMsg],
    *,
    capture_id: str,
    pcap: str,
    total_frames: int = 0,
    extra_short_addrs: Iterable[int] = (),
    extra_eui64s: Iterable[int] = (),
) -> CaptureSummary:
    """Aggregate decoded OtaMsg events into a CaptureSummary."""
    acc = _Accumulator()
    acc.total_frames = total_frames
    for msg in messages:
        _accumulate_message(acc, msg)
    for short in extra_short_addrs:
        acc.short_addrs.add(short)
    for eui in extra_eui64s:
        acc.eui64s.add(eui)
    return CaptureSummary(
        capture_id=capture_id,
        pcap=pcap,
        total_frames=acc.total_frames,
        matching_vendor_frames=acc.matching_vendor_frames,
        short_addrs=sorted(_format_short(s) for s in acc.short_addrs),
        eui64s=sorted(_format_eui64(e) for e in acc.eui64s),
        cmd_counts={str(k): v for k, v in sorted(acc.cmd_counts.items())},
        analog_x10_events=acc.analog_x10_events,
        enum_events=acc.enum_events,
        ack_events=acc.ack_events,
        unknown_events=acc.unknown_events,
    )


def _iter_pcap_events(
    pcap_path: Path, tap_len: int = 28
) -> Iterator[tuple[Optional[OtaMsg], Optional[int], bool]]:
    """Yield (msg, eui64_seen, is_total_frame) tuples for each pcap packet.

    ``msg`` is set when a vendor app frame decoded successfully.
    ``eui64_seen`` is set when an EUI-64 was observed in the MAC header.
    ``is_total_frame`` is True for every successfully unpacked pcap record so
    the caller can count totals separately.
    """
    reader = PcapNgReader(str(pcap_path))
    t0: Optional[float] = None
    for packet in reader.packets():
        if t0 is None:
            t0 = packet.timestamp
        t_rel = packet.timestamp - t0
        yield None, None, True
        tapped = strip_tap(packet.data, tap_len=tap_len)
        if tapped is None:
            continue
        mac = parse_mac_frame(tapped)
        if mac is None:
            continue
        eui_seen: Optional[int] = None
        if mac.src_ext is not None:
            eui_seen = mac.src_ext
        elif mac.dst_ext is not None:
            eui_seen = mac.dst_ext
        if eui_seen is not None:
            yield None, eui_seen, False
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
        yield msg, None, False


def analyze_pcap(entry: CaptureEntry, *, base_dir: Path = ROOT) -> CaptureSummary:
    pcap_path = (base_dir / entry.pcap).resolve()
    if not pcap_path.exists():
        return CaptureSummary(
            capture_id=entry.capture_id,
            pcap=entry.pcap,
            pcap_exists=False,
            error=f"pcap file not found: {pcap_path}",
        )
    acc = _Accumulator()
    try:
        for msg, eui_seen, is_total in _iter_pcap_events(pcap_path):
            if is_total:
                acc.total_frames += 1
            if eui_seen is not None:
                acc.eui64s.add(eui_seen)
            if msg is not None:
                _accumulate_message(acc, msg)
    except (OSError, ValueError) as exc:
        LOGGER.warning("ota_batch.read_failed", extra={"pcap": str(pcap_path), "error": str(exc)})
        return CaptureSummary(
            capture_id=entry.capture_id,
            pcap=entry.pcap,
            pcap_exists=True,
            error=f"failed to read pcap: {exc}",
        )
    return CaptureSummary(
        capture_id=entry.capture_id,
        pcap=entry.pcap,
        total_frames=acc.total_frames,
        matching_vendor_frames=acc.matching_vendor_frames,
        short_addrs=sorted(_format_short(s) for s in acc.short_addrs),
        eui64s=sorted(_format_eui64(e) for e in acc.eui64s),
        cmd_counts={str(k): v for k, v in sorted(acc.cmd_counts.items())},
        analog_x10_events=acc.analog_x10_events,
        enum_events=acc.enum_events,
        ack_events=acc.ack_events,
        unknown_events=acc.unknown_events,
    )


def analyze_manifest(
    manifest: Manifest, *, base_dir: Path = ROOT
) -> list[CaptureSummary]:
    return [analyze_pcap(entry, base_dir=base_dir) for entry in manifest]


def format_summary_text(summary: CaptureSummary) -> str:
    lines = [
        f"capture_id: {summary.capture_id}",
        f"  pcap:                    {summary.pcap}",
    ]
    if summary.error:
        lines.append(f"  error:                   {summary.error}")
        return "\n".join(lines)
    lines.extend(
        [
            f"  total_frames:            {summary.total_frames}",
            f"  matching_vendor_frames:  {summary.matching_vendor_frames}",
            f"  short_addrs:             {', '.join(summary.short_addrs) or '-'}",
            f"  eui64s:                  {', '.join(summary.eui64s) or '-'}",
            f"  cmd_counts:              "
            f"{', '.join(f'cmd{k}={v}' for k, v in summary.cmd_counts.items()) or '-'}",
            f"  analog_x10_events:       {summary.analog_x10_events}",
            f"  enum_events:             {summary.enum_events}",
            f"  ack_events:              {summary.ack_events}",
            f"  unknown_events:          {summary.unknown_events}",
        ]
    )
    return "\n".join(lines)


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True, help="Path to captures/manifest.yaml")
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.WARNING)

    manifest = load_manifest(args.manifest)
    summaries = analyze_manifest(manifest)

    if args.json:
        json.dump(
            [s.to_dict() for s in summaries],
            sys.stdout,
            indent=2,
            sort_keys=False,
        )
        sys.stdout.write("\n")
        return 0

    if not summaries:
        print("(manifest contains no captures)")
        return 0
    for index, summary in enumerate(summaries):
        if index > 0:
            print()
        print(format_summary_text(summary))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
