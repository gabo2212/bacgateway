#!/usr/bin/env python3
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path
from dataclasses import dataclass
from typing import Iterator, Optional

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from gateway.ota.app import OtaMsg, parse_ota_msg
from gateway.ota.ieee802154 import parse_mac_frame, strip_tap
from gateway.ota.pcapng import PcapNgReader
from gateway.ota.zigbee import parse_aps_frame, parse_nwk_frame

LOGGER = logging.getLogger(__name__)

PROFILE_ID = 0xC1E4
CLUSTER_ID = 0x0002


@dataclass
class Stats:
    pcap_packets: int = 0
    tap_short: int = 0
    mac_none: int = 0
    pan_filtered: int = 0
    nwk_none: int = 0
    nwk_retry: int = 0
    aps_none: int = 0
    app_filtered: int = 0
    app_none: int = 0
    device_filtered: int = 0
    output: int = 0


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
    return " ".join(parts)


def _iter_messages(
    pcap_path: str,
    tap_len: int,
    pan_filter: Optional[int],
    only_app: bool,
    stats: Optional[Stats] = None,
) -> Iterator[OtaMsg]:
    reader = PcapNgReader(pcap_path)
    t0: Optional[float] = None

    for packet in reader.packets():
        if stats is not None:
            stats.pcap_packets += 1
        if t0 is None:
            t0 = packet.timestamp
        t_rel = packet.timestamp - t0

        tapped = strip_tap(packet.data, tap_len=tap_len)
        if tapped is None:
            if stats is not None:
                stats.tap_short += 1
            continue
        mac = parse_mac_frame(tapped)
        if mac is None:
            if stats is not None:
                stats.mac_none += 1
            continue
        if pan_filter is not None and mac.pan_id != pan_filter:
            if stats is not None:
                stats.pan_filtered += 1
            continue
        nwk = parse_nwk_frame(mac.payload)
        if nwk is None and len(mac.payload) >= 2:
            nwk = parse_nwk_frame(mac.payload[:-2])
            if nwk is not None and stats is not None:
                stats.nwk_retry += 1
        if nwk is None:
            if stats is not None:
                stats.nwk_none += 1
            continue
        aps = parse_aps_frame(nwk.payload)
        if aps is None:
            if stats is not None:
                stats.aps_none += 1
            continue
        if only_app and (aps.profile_id != PROFILE_ID or aps.cluster_id != CLUSTER_ID):
            if stats is not None:
                stats.app_filtered += 1
            continue
        msg = parse_ota_msg(t_rel, nwk.src, nwk.dst, aps)
        if msg is None:
            if stats is not None:
                stats.app_none += 1
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
        help="Print packet processing stats to stderr",
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.WARNING)

    device_filter = _parse_int(args.device) if args.device else None
    pan_filter = _parse_int(args.pan) if args.pan else None

    stats = Stats() if args.stats else None

    out_path = Path("ota") / "baselines" / f"{Path(args.pcap).stem}.decoded.txt"
    out_path.parent.mkdir(parents=True, exist_ok=True)

    with out_path.open("w", encoding="utf-8", newline="\n") as handle:
        for msg in _iter_messages(args.pcap, args.tap_len, pan_filter, args.only_app, stats):
            if device_filter is not None and msg.device_short != device_filter:
                if stats is not None:
                    stats.device_filtered += 1
                continue
            line = _format_line(msg)
            print(line)
            handle.write(line + "\n")
            if stats is not None:
                stats.output += 1

    if stats is not None:
        print(
            "stats:",
            f"pcap_packets={stats.pcap_packets}",
            f"tap_short={stats.tap_short}",
            f"mac_none={stats.mac_none}",
            f"pan_filtered={stats.pan_filtered}",
            f"nwk_none={stats.nwk_none}",
            f"nwk_retry={stats.nwk_retry}",
            f"aps_none={stats.aps_none}",
            f"app_filtered={stats.app_filtered}",
            f"app_none={stats.app_none}",
            f"device_filtered={stats.device_filtered}",
            f"output={stats.output}",
            file=sys.stderr,
        )


if __name__ == "__main__":
    main()
