#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


@dataclass
class HttpRecord:
    frame: int
    ts_epoch: float
    stream: str
    src: str
    dst: str
    is_request: bool
    method: str | None
    uri: str | None
    status: str | None
    content_type: str | None
    body: str | None


def _lookup_field(layers: dict[str, Any], key: str) -> Any:
    if key in layers:
        return layers[key]
    for layer_name in ("frame", "ip", "tcp", "http", "data"):
        layer = layers.get(layer_name)
        if isinstance(layer, dict) and key in layer:
            return layer[key]
    return None


def _first_text(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        for item in value:
            text = _first_text(item)
            if text is not None:
                return text
        return None
    if isinstance(value, dict):
        for item in value.values():
            text = _first_text(item)
            if text is not None:
                return text
        return None
    return str(value)


def _decode_body(text: str | None) -> str | None:
    if text is None:
        return None
    value = text.strip()
    if not value:
        return None
    if re.fullmatch(r"[0-9A-Fa-f]+", value) and len(value) % 2 == 0:
        try:
            return bytes.fromhex(value).decode("utf-8", errors="replace")
        except Exception:
            return value
    return value


def _collect_records(tshark_bin: str, pcap_path: Path) -> list[HttpRecord]:
    cmd = [
        tshark_bin,
        "-r",
        str(pcap_path),
        "-Y",
        "http.request || http.response",
        "-T",
        "json",
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or "tshark parse failed")

    packets = json.loads(proc.stdout)
    records: list[HttpRecord] = []
    for packet in packets:
        layers = packet.get("_source", {}).get("layers", {})
        if not isinstance(layers, dict):
            continue

        frame_s = _first_text(_lookup_field(layers, "frame.number")) or "0"
        ts_s = _first_text(_lookup_field(layers, "frame.time_epoch")) or "0"
        stream = _first_text(_lookup_field(layers, "tcp.stream")) or "?"
        src = _first_text(_lookup_field(layers, "ip.src")) or "?"
        dst = _first_text(_lookup_field(layers, "ip.dst")) or "?"

        method = _first_text(_lookup_field(layers, "http.request.method"))
        uri = (
            _first_text(_lookup_field(layers, "http.request.full_uri"))
            or _first_text(_lookup_field(layers, "http.request.uri"))
        )
        status = _first_text(_lookup_field(layers, "http.response.code"))
        content_type = _first_text(_lookup_field(layers, "http.content_type"))
        body_raw = (
            _first_text(_lookup_field(layers, "http.file_data"))
            or _first_text(_lookup_field(layers, "data-text-lines"))
            or _first_text(_lookup_field(layers, "data.data"))
        )
        body = _decode_body(body_raw)

        is_request = method is not None
        if not is_request and status is None:
            continue

        try:
            frame = int(frame_s)
        except ValueError:
            frame = 0
        try:
            ts_epoch = float(ts_s)
        except ValueError:
            ts_epoch = 0.0

        records.append(
            HttpRecord(
                frame=frame,
                ts_epoch=ts_epoch,
                stream=stream,
                src=src,
                dst=dst,
                is_request=is_request,
                method=method,
                uri=uri,
                status=status,
                content_type=content_type,
                body=body,
            )
        )

    records.sort(key=lambda rec: rec.frame)
    return records


def _capture_pcap(
    tshark_bin: str,
    interface: str,
    host: str,
    port: int,
    duration: int,
    output_path: Path,
) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    capture_filter = f"host {host} and tcp port {port}"
    cmd = [
        tshark_bin,
        "-i",
        interface,
        "-f",
        capture_filter,
        "-a",
        f"duration:{duration}",
        "-w",
        str(output_path),
    ]
    print(f"# capture: {' '.join(cmd)}")
    print(f"# perform the Niagara UI write now (capture window: {duration}s)")
    proc = subprocess.run(cmd, text=True)
    if proc.returncode != 0:
        raise RuntimeError("tshark capture failed (try running shell as Administrator/root)")


def _snippet(text: str | None, max_len: int) -> str:
    if not text:
        return "-"
    compact = " ".join(text.split())
    if len(compact) <= max_len:
        return compact
    return compact[:max_len] + "..."


def _print_records(
    records: list[HttpRecord],
    point_filter: str | None,
    max_body: int,
    host: str,
) -> None:
    if not records:
        print("no HTTP request/response records found")
        return

    streams: dict[str, list[HttpRecord]] = {}
    for rec in records:
        streams.setdefault(rec.stream, []).append(rec)

    selected_streams: list[str] = []
    if point_filter:
        needle = point_filter.lower()
        for stream, recs in streams.items():
            joined = " ".join(
                [
                    rec.uri or "",
                    rec.body or "",
                    rec.content_type or "",
                    rec.method or "",
                    rec.status or "",
                ]
                for rec in recs
            )
            flat = " ".join(joined).lower()
            if needle in flat:
                selected_streams.append(stream)
    else:
        selected_streams = list(streams.keys())
    selected_streams.sort(key=lambda s: int(s) if s.isdigit() else s)

    if not selected_streams:
        print("no streams matched point filter")
        return

    replay_candidate: HttpRecord | None = None
    for stream in selected_streams:
        recs = sorted(streams[stream], key=lambda rec: rec.frame)
        print(f"\n=== tcp.stream {stream} ===")
        for rec in recs:
            ts = datetime.fromtimestamp(rec.ts_epoch).isoformat(sep=" ", timespec="milliseconds")
            if rec.is_request:
                print(
                    f"REQ #{rec.frame} {ts} {rec.src}->{rec.dst} "
                    f"{rec.method or '-'} {rec.uri or '-'} ct={rec.content_type or '-'}"
                )
                if rec.body:
                    print(f"  body: {_snippet(rec.body, max_body)}")
                if replay_candidate is None and rec.method in ("POST", "PUT"):
                    replay_candidate = rec
            else:
                print(
                    f"RES #{rec.frame} {ts} {rec.src}->{rec.dst} "
                    f"status={rec.status or '-'} ct={rec.content_type or '-'}"
                )
                if rec.body:
                    print(f"  body: {_snippet(rec.body, max_body)}")

    if replay_candidate:
        uri = replay_candidate.uri or "/"
        if uri.startswith("http://") or uri.startswith("https://"):
            url = uri
        else:
            url = f"http://{host}{uri}"
        print("\n# replay candidate")
        print(f"method: {replay_candidate.method}")
        print(f"url: {url}")
        print(f"content-type: {replay_candidate.content_type or 'application/xml'}")
        print(f"body: {_snippet(replay_candidate.body, max_body)}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Capture Niagara HTTP traffic during a UI write and summarize request/response pairs."
    )
    parser.add_argument("--host", required=True, help="Niagara host/IP to capture")
    parser.add_argument("--port", type=int, default=80, help="Niagara HTTP port (default: 80)")
    parser.add_argument(
        "--interface",
        help="Capture interface for tshark (required unless --pcap is provided)",
    )
    parser.add_argument("--duration", type=int, default=25, help="Capture duration in seconds")
    parser.add_argument(
        "--pcap",
        help="Analyze an existing pcap/pcapng file instead of capturing live",
    )
    parser.add_argument(
        "--out",
        help="Output pcapng path for live capture (default: captures/niagara_http_<ts>.pcapng)",
    )
    parser.add_argument(
        "--point-filter",
        default=None,
        help="Substring filter (e.g. OccupiedHeatingSetpoint) to select relevant streams",
    )
    parser.add_argument("--max-body", type=int, default=220, help="Max body chars to print")
    parser.add_argument(
        "--tshark-bin",
        default="tshark",
        help="Path/name of tshark binary (default: tshark)",
    )
    args = parser.parse_args()

    tshark_bin = args.tshark_bin
    if shutil.which(tshark_bin) is None:
        print("tshark not found in PATH. Install Wireshark/tshark first.", file=sys.stderr)
        return 2

    if args.pcap:
        pcap_path = Path(args.pcap)
        if not pcap_path.exists():
            print(f"pcap file not found: {pcap_path}", file=sys.stderr)
            return 2
    else:
        if not args.interface:
            print("--interface is required for live capture", file=sys.stderr)
            return 2
        stamp = time.strftime("%Y%m%d_%H%M%S")
        default_out = Path("captures") / f"niagara_http_{stamp}.pcapng"
        pcap_path = Path(args.out) if args.out else default_out
        try:
            _capture_pcap(
                tshark_bin=tshark_bin,
                interface=args.interface,
                host=args.host,
                port=args.port,
                duration=max(1, args.duration),
                output_path=pcap_path,
            )
        except Exception as exc:  # noqa: BLE001
            print(f"capture failed: {exc}", file=sys.stderr)
            return 1

    try:
        records = _collect_records(tshark_bin=tshark_bin, pcap_path=pcap_path)
    except Exception as exc:  # noqa: BLE001
        print(f"parse failed: {exc}", file=sys.stderr)
        return 1

    print(f"# analyzed pcap: {pcap_path}")
    _print_records(
        records=records,
        point_filter=args.point_filter,
        max_body=max(40, args.max_body),
        host=args.host,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
