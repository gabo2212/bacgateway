#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import logging
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
from gateway.ota.registry import ObservedKey, ObservedStats, update_registry
from gateway.ota.zigbee import parse_aps_frame, parse_nwk_frame

LOGGER = logging.getLogger(__name__)

PROFILE_ID = 0xC1E4
CLUSTER_ID = 0x0002


@dataclass
class StatEntry:
    count: int
    first: float
    last: float
    sample: str


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


def _format_json(msg: OtaMsg) -> dict[str, object]:
    data: dict[str, object] = {
        "t": round(msg.t_rel, 3),
        "dir": msg.direction,
        "dev": f"0x{msg.device_short:04x}",
        "cmd": msg.cmd_id,
        "payload": msg.raw_rest_hex,
        "kind": msg.kind,
    }
    if msg.value is not None:
        data["value"] = msg.value
    if msg.label:
        data["label"] = msg.label
    if msg.ack_extra_hex is not None:
        data["extra"] = msg.ack_extra_hex
    if msg.kind == "unknown" or msg.reason:
        data["rest_len"] = msg.rest_len
    if msg.reason:
        data["reason"] = msg.reason
    return data


def _format_optional_hex(value: Optional[int], width: int) -> str:
    if value is None:
        return "?"
    return f"0x{value:0{width}x}"


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


def main() -> None:
    parser = argparse.ArgumentParser(description="Decode OTA application frames from pcapng")
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
        "--stats",
        action="store_true",
        help="Summarize unknown cmd2 frames instead of printing each message",
    )
    parser.add_argument(
        "--stats-all",
        action="store_true",
        help="Include all decoded messages in stats output",
    )
    parser.add_argument(
        "--catalog",
        help="Write observed point registry CSV (stats mode only)",
    )
    parser.add_argument(
        "--out",
        help="Write output to a specific path (default: ota/baselines/<pcap>.decoded.txt)",
    )
    parser.add_argument(
        "--format",
        choices=["text", "jsonl", "csv"],
        default="text",
        help="Output format for decoded messages (default: text)",
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.WARNING)

    device_filter = _parse_int(args.device) if args.device else None
    pan_filter = _parse_int(args.pan) if args.pan else None

    catalog_path = Path(args.catalog) if args.catalog else None
    stats_mode = args.stats or args.stats_all or catalog_path is not None

    if stats_mode:
        if args.format != "text":
            raise SystemExit("--format only applies to decoded output (not stats)")
        entries: dict[
            tuple[int, str, int, Optional[int], Optional[int], int],
            StatEntry,
        ] = {}
        catalog: dict[tuple[int, str], dict[ObservedKey, ObservedStats]] = {}

        for msg in _iter_messages(args.pcap, args.tap_len, pan_filter, args.only_app):
            if device_filter is not None and msg.device_short != device_filter:
                continue
            if not args.stats_all and not (msg.cmd_id == 0x02 and msg.kind == "unknown"):
                continue
            rest_len = len(msg.raw_rest_hex) // 2
            key = (
                msg.device_short,
                msg.direction,
                msg.cmd_id,
                msg.prefix,
                msg.code,
                rest_len,
            )
            entry = entries.get(key)
            if entry is None:
                entries[key] = StatEntry(
                    count=1,
                    first=msg.t_rel,
                    last=msg.t_rel,
                    sample=msg.raw_rest_hex,
                )
            else:
                entry.count += 1
                entry.first = min(entry.first, msg.t_rel)
                entry.last = max(entry.last, msg.t_rel)
            if catalog_path is not None:
                reg_key = (msg.device_short, msg.direction)
                reg = catalog.setdefault(reg_key, {})
                update_registry(reg, msg)

        stats_suffix = "stats-all" if args.stats_all else "stats"
        out_path = (
            Path(args.out)
            if args.out
            else Path("ota") / "baselines" / f"{Path(args.pcap).stem}.{stats_suffix}.txt"
        )
        out_path.parent.mkdir(parents=True, exist_ok=True)

        lines: list[str] = []
        for key, entry in sorted(
            entries.items(),
            key=lambda item: (
                -item[1].count,
                item[0][0],
                item[0][3] if item[0][3] is not None else 0x1_0000,
                item[0][4] if item[0][4] is not None else 0x1_0000,
                item[0][5],
                item[0][1],
                item[0][2],
            ),
        ):
            dev, direction, cmd_id, prefix, code, rest_len = key
            line = " ".join(
                [
                    f"count={entry.count}",
                    f"dev=0x{dev:04x}",
                    f"dir={direction}",
                    f"cmd={cmd_id}",
                    f"prefix={_format_optional_hex(prefix, 2)}",
                    f"code={_format_optional_hex(code, 2)}",
                    f"len={rest_len}",
                    f"first={entry.first:.3f}",
                    f"last={entry.last:.3f}",
                    f"sample={entry.sample}",
                ]
            )
            lines.append(line)
            print(line)

        with out_path.open("w", encoding="utf-8", newline="\n") as handle:
            for line in lines:
                handle.write(line + "\n")
        if catalog_path is not None:
            catalog_path.parent.mkdir(parents=True, exist_ok=True)
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
                for (dev, direction), reg in sorted(
                    catalog.items(), key=lambda item: (item[0][0], item[0][1])
                ):
                    for key, entry in sorted(
                        reg.items(),
                        key=lambda item: (
                            item[0].cmd_id,
                            item[0].prefix,
                            item[0].code,
                            item[0].rest_len,
                        ),
                    ):
                        writer.writerow(
                            [
                                f"0x{dev:04x}",
                                direction,
                                key.cmd_id,
                                f"0x{key.prefix:02x}",
                                f"0x{key.code:02x}",
                                key.rest_len,
                                "|".join(sorted(entry.kinds)),
                                entry.count,
                                f"{entry.first_t:.3f}",
                                f"{entry.last_t:.3f}",
                                entry.sample_rest_hex,
                            ]
                        )
        return

    if args.out:
        out_path = Path(args.out)
    else:
        suffix = {
            "text": "decoded.txt",
            "jsonl": "decoded.jsonl",
            "csv": "decoded.csv",
        }[args.format]
        out_path = Path("ota") / "baselines" / f"{Path(args.pcap).stem}.{suffix}"
    out_path.parent.mkdir(parents=True, exist_ok=True)

    if args.format == "text":
        with out_path.open("w", encoding="utf-8", newline="\n") as handle:
            for msg in _iter_messages(args.pcap, args.tap_len, pan_filter, args.only_app):
                if device_filter is not None and msg.device_short != device_filter:
                    continue
                line = _format_line(msg)
                print(line)
                handle.write(line + "\n")
        return

    if args.format == "jsonl":
        with out_path.open("w", encoding="utf-8", newline="\n") as handle:
            for msg in _iter_messages(args.pcap, args.tap_len, pan_filter, args.only_app):
                if device_filter is not None and msg.device_short != device_filter:
                    continue
                record = json.dumps(_format_json(msg), separators=(",", ":"))
                print(record)
                handle.write(record + "\n")
        return

    if args.format == "csv":
        with out_path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(
                [
                    "t",
                    "dir",
                    "dev",
                    "cmd",
                    "payload",
                    "kind",
                    "value",
                    "label",
                    "extra",
                    "rest_len",
                    "reason",
                ]
            )
            print(
                "t,dir,dev,cmd,payload,kind,value,label,extra,rest_len,reason"
            )
            for msg in _iter_messages(args.pcap, args.tap_len, pan_filter, args.only_app):
                if device_filter is not None and msg.device_short != device_filter:
                    continue
                row = [
                    f"{msg.t_rel:.3f}",
                    msg.direction,
                    f"0x{msg.device_short:04x}",
                    msg.cmd_id,
                    msg.raw_rest_hex,
                    msg.kind,
                    msg.value if msg.value is not None else "",
                    msg.label or "",
                    msg.ack_extra_hex or "",
                    msg.rest_len if (msg.kind == "unknown" or msg.reason) else "",
                    msg.reason or "",
                ]
                writer.writerow(row)
                print(",".join(str(item) for item in row))
        return


if __name__ == "__main__":
    main()
