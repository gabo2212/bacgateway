#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Optional

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from gateway.ota.app import OtaMsg, parse_ota_msg
from gateway.ota.ieee802154 import parse_mac_frame, strip_tap
from gateway.ota.pcapng import PcapNgReader
from gateway.ota.zigbee import parse_aps_frame, parse_nwk_frame

PROFILE_ID = 0xC1E4
CLUSTER_ID = 0x0002


@dataclass
class CatalogEntry:
    count: int
    first: float
    last: float
    sample: str
    kinds: set[str]


def _parse_int(text: str) -> int:
    text = text.strip().lower()
    if text.startswith("0x"):
        return int(text, 16)
    if any(c in "abcdef" for c in text):
        return int(text, 16)
    return int(text)


def _format_line(msg: OtaMsg) -> str:
    parts = [
        f"t={msg.t_rel:.3f}",
        f"dir={msg.direction}",
        f"dev=0x{msg.device_short:04x}",
        f"cmd={msg.cmd_id}",
        f"payload={msg.raw_rest_hex}",
        f"kind={msg.kind}",
    ]
    if msg.value is not None:
        parts.append(f"value={msg.value}")
    if msg.label:
        parts.append(f"label={msg.label}")
    if msg.ack_extra_hex is not None:
        parts.append(f"extra={msg.ack_extra_hex}")
    if msg.kind == "unknown" or msg.reason:
        parts.append(f"rest_len={msg.rest_len}")
    if msg.reason:
        parts.append(f"reason={msg.reason}")
    return " ".join(parts)


def _iter_messages(
    pcap_path: str,
    tap_len: int,
    pan_filter: Optional[int],
    only_app: bool,
) -> Iterator[OtaMsg]:
    reader = PcapNgReader(pcap_path)
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
        if pan_filter is not None and mac.pan_id != pan_filter:
            continue
        nwk = parse_nwk_frame(mac.payload)
        if nwk is None and len(mac.payload) >= 2:
            nwk = parse_nwk_frame(mac.payload[:-2])
        if nwk is None:
            continue
        aps = parse_aps_frame(nwk.payload)
        if aps is None:
            continue
        if only_app and (aps.profile_id != PROFILE_ID or aps.cluster_id != CLUSTER_ID):
            continue
        msg = parse_ota_msg(t_rel, nwk.src, nwk.dst, aps)
        if msg is None:
            continue
        yield msg


def _sort_prefix(value: Optional[int]) -> int:
    return value if value is not None else 0x1_0000


def _sort_code(value: Optional[int]) -> int:
    return value if value is not None else 0x1_0000


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate OTA decoder baseline artifacts")
    parser.add_argument("--pcap", required=True, help="Path to pcapng capture")
    parser.add_argument("--device", help="Filter by device short address (e.g. 0x143e)")
    parser.add_argument("--tap-len", type=int, default=28, help="802.15.4 TAP header length")
    parser.add_argument("--pan", help="Filter by PAN ID (e.g. 0x00d2)")
    parser.add_argument(
        "--only-app",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Filter to vendor app profile/cluster (default: enabled)",
    )
    parser.add_argument(
        "--out-dir",
        default="ota/baselines",
        help="Output directory (default: ota/baselines)",
    )
    parser.add_argument("--tag", help="Filename tag (default: pcap stem)")
    args = parser.parse_args()

    device_filter = _parse_int(args.device) if args.device else None
    pan_filter = _parse_int(args.pan) if args.pan else None
    tag = args.tag or Path(args.pcap).stem
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    messages: list[OtaMsg] = []
    for msg in _iter_messages(args.pcap, args.tap_len, pan_filter, args.only_app):
        if device_filter is not None and msg.device_short != device_filter:
            continue
        messages.append(msg)
    messages.sort(key=lambda m: m.t_rel)

    decoded_path = out_dir / f"{tag}.decoded.txt"
    unknowns_path = out_dir / f"{tag}.unknowns.txt"
    u8_path = out_dir / f"{tag}.u8-snippets.txt"
    catalog_path = out_dir / f"{tag}.catalog.csv"

    with decoded_path.open("w", encoding="utf-8", newline="\n") as handle:
        for msg in messages:
            handle.write(_format_line(msg) + "\n")

    with unknowns_path.open("w", encoding="utf-8", newline="\n") as handle:
        for msg in messages:
            if msg.kind == "unknown":
                handle.write(_format_line(msg) + "\n")

    with u8_path.open("w", encoding="utf-8", newline="\n") as handle:
        for msg in messages:
            if msg.kind == "u8":
                handle.write(_format_line(msg) + "\n")

    catalog: dict[
        tuple[int, str, int, Optional[int], Optional[int], int], CatalogEntry
    ] = {}
    for msg in messages:
        rest_len = len(msg.raw_rest_hex) // 2
        key = (
            msg.device_short,
            msg.direction,
            msg.cmd_id,
            msg.prefix,
            msg.code,
            rest_len,
        )
        entry = catalog.get(key)
        if entry is None:
            catalog[key] = CatalogEntry(
                count=1,
                first=msg.t_rel,
                last=msg.t_rel,
                sample=msg.raw_rest_hex,
                kinds={msg.kind},
            )
        else:
            entry.count += 1
            entry.first = min(entry.first, msg.t_rel)
            entry.last = max(entry.last, msg.t_rel)
            entry.kinds.add(msg.kind)

    with catalog_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            [
                "dev",
                "direction",
                "cmd",
                "prefix",
                "code",
                "rest_len",
                "kind_set",
                "count",
                "first",
                "last",
                "sample",
            ]
        )
        rows: list[tuple[tuple[object, ...], list[object]]] = []
        for key, entry in catalog.items():
            dev, direction, cmd, prefix, code, rest_len = key
            kind_set = "|".join(sorted(entry.kinds))
            row = [
                f"0x{dev:04x}",
                direction,
                cmd,
                f"0x{prefix:02x}" if prefix is not None else "?",
                f"0x{code:02x}" if code is not None else "?",
                rest_len,
                kind_set,
                entry.count,
                f"{entry.first:.3f}",
                f"{entry.last:.3f}",
                entry.sample,
            ]
            sort_key = (
                dev,
                direction,
                cmd,
                _sort_prefix(prefix),
                _sort_code(code),
                rest_len,
                kind_set,
            )
            rows.append((sort_key, row))
        for _, row in sorted(rows, key=lambda item: item[0]):
            writer.writerow(row)


if __name__ == "__main__":
    main()
