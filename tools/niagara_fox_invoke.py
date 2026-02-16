#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import random
import socket
import sys
import time
import uuid
from pathlib import Path


FoxMessage = str | bytes


def _build_fox_message(kind: str, msg_id: int, status: int, op: str, body: str, *, newline: str) -> str:
    return f"fox {kind} {msg_id} {status} {op}{newline}{{{newline}{body}{newline}}};;{newline}"


def _build_fox_client_hello(
    msg_id: int,
    http_session: str,
    *,
    host_name: str,
    host_address: str,
    vm_uuid: str,
    newline: str,
) -> str:
    body = newline.join(
        (
            "fox.version=s:1.0.1",
            "id=i:0",
            f"hostName=s:{host_name}",
            f"hostAddress=s:{host_address}",
            "app.name=s:WbApplet",
            "app.version=s:3.8.213",
            "vm.name=s:Java HotSpot(TM) 64-Bit Server VM",
            "vm.version=s:25.202-b08",
            "os.name=s:Windows 10",
            "os.version=s:10.0",
            f"httpSession=s:{http_session}",
            "lang=s:en",
            "timeZone=s:America/New_York;-18000000;3600000;02:00:00.000,wall,march,8,on or after,sunday,undefined;02:00:00.000,wall,november,1,on or after,sunday,undefined",
            "hostId=s:#null;",
            f"vmUuid=s:{vm_uuid}",
        )
    )
    return _build_fox_message("a", msg_id, -1, "fox hello", body, newline=newline)


def _wire_len_prefixed_text(text: str) -> bytes:
    raw = text.encode("utf-8")
    if len(raw) > 0xFFFF:
        raise ValueError(f"Fox wire text too long: {len(raw)}")
    return len(raw).to_bytes(2, "big") + raw


def _build_http_fox_credentials_blob(username: str, session_id: str) -> bytes:
    # Binary payload observed in successful Workbench/Applet traffic:
    # baja:HttpFoxCredentials { username: baja:String, sessionId: baja:String }
    out = bytearray()
    out.extend(b"\x01\x01\x01\x00")
    out.extend(b"\xff\xff\xff\xff")
    out.extend(b"\x00\x01")
    out.extend(_wire_len_prefixed_text("baja:HttpFoxCredentials"))

    out.extend(b"\x01\x01\x01\x01")
    out.extend(_wire_len_prefixed_text("username"))
    out.extend(b"\x00\x00\x00\x00")
    out.extend(b"\x00\x01")
    out.extend(_wire_len_prefixed_text("baja:String"))
    out.extend(b"\x01")
    out.extend(_wire_len_prefixed_text(username))

    out.extend(b"\x01\x01\x01\x01")
    out.extend(_wire_len_prefixed_text("sessionId"))
    out.extend(b"\x00\x00\x00\x00")
    out.extend(b"\x00\x01")
    out.extend(_wire_len_prefixed_text("baja:String"))
    out.extend(b"\x01")
    out.extend(_wire_len_prefixed_text(session_id))

    out.extend(b"\x00")
    return bytes(out)


def _build_fox_auth_message1(msg_id: int, username: str, session_id: str, *, newline: str) -> bytes:
    credentials = _build_http_fox_credentials_blob(username=username, session_id=session_id)
    prefix = (
        f"fox a {msg_id} -1 fox authMessage1{newline}"
        f"{{{newline}"
        f"authInput=s:authInputHttp{newline}"
        f"username=s:{username}{newline}"
        f"credentials=b:{len(credentials)}["
    ).encode("ascii")
    suffix = f"]{newline}}};;{newline}".encode("ascii")
    return prefix + credentials + suffix


def _build_sys_make_broker_channel(msg_id: int, *, newline: str) -> str:
    return _build_fox_message("s", msg_id, 0, "sys makeBrokerChannel", "ord=s:station:", newline=newline)


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
    messages: list[FoxMessage],
    timeout: float,
    recv_window: float,
    inter_message_delay: float,
    wait_after_hello: float,
    wait_after_auth: float,
    wait_after_broker: float,
    wait_after_sub: float,
    wait_after_sync_stream: float,
) -> tuple[str, str | None, str | None]:
    send_error: str | None = None
    recv_error: str | None = None
    chunks: list[bytes] = []

    def drain_for(duration_sec: float) -> bool:
        nonlocal recv_error
        if duration_sec <= 0:
            return True
        end = time.time() + duration_sec
        while time.time() < end:
            remaining = max(0.01, end - time.time())
            sock.settimeout(min(timeout, remaining))
            try:
                data = sock.recv(65535)
            except TimeoutError:
                break
            except OSError as exc:
                recv_error = f"recv failed: {exc}"
                return False
            if not data:
                break
            chunks.append(data)
        return True

    with socket.create_connection((host, port), timeout=timeout) as sock:
        sock.settimeout(timeout)
        for idx, message in enumerate(messages, start=1):
            if isinstance(message, bytes):
                payload = message
                message_text = message.decode("ascii", errors="ignore")
            else:
                payload = message.encode("utf-8")
                message_text = message
            first_line = message_text.splitlines()[0].strip().lower() if message_text.splitlines() else ""
            try:
                sock.sendall(payload)
            except OSError as exc:
                send_error = f"send failed on payload {idx}: {exc}"
                break
            if "fox hello" in first_line:
                if not drain_for(wait_after_hello):
                    break
            elif "fox authmessage1" in first_line:
                if not drain_for(wait_after_auth):
                    break
            elif "makebrokerchannel" in first_line:
                if not drain_for(wait_after_broker):
                    break
            elif "station sub" in first_line:
                if not drain_for(wait_after_sub):
                    break
            elif "circuit stream" in first_line:
                if not drain_for(wait_after_sync_stream):
                    break
            time.sleep(max(0.0, inter_message_delay))

        end = time.time() + recv_window
        while time.time() < end:
            try:
                data = sock.recv(65535)
            except TimeoutError:
                break
            except OSError as exc:
                recv_error = f"recv failed: {exc}"
                break
            if not data:
                break
            chunks.append(data)

        return b"".join(chunks).decode("utf-8", errors="replace"), send_error, recv_error


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
        "--fox-http-auth",
        action="store_true",
        help="Send fox hello/auth bootstrap using authInputHttp before station messages",
    )
    parser.add_argument("--fox-user", default=None, help="Fox/HTTP username (default: from --fox-user-env)")
    parser.add_argument("--fox-pass", default=None, help="Fox/HTTP password (default: from --fox-pass-env)")
    parser.add_argument(
        "--fox-user-env",
        default="NIAGARA_USER",
        help="Environment variable name containing Fox/HTTP username (default: NIAGARA_USER)",
    )
    parser.add_argument(
        "--fox-pass-env",
        default="NIAGARA_PASS",
        help="Environment variable name containing Fox/HTTP password (default: NIAGARA_PASS)",
    )
    parser.add_argument(
        "--fox-http-session",
        default=None,
        help="Existing HTTP session token (sc...) for authInputHttp; if omitted we login via HTTP first",
    )
    parser.add_argument("--hello-msg-id", type=int, default=1, help="Message id for fox hello (default: 1)")
    parser.add_argument("--auth-msg-id", type=int, default=2, help="Message id for fox authMessage1 (default: 2)")
    parser.add_argument(
        "--broker-msg-id",
        type=int,
        default=3,
        help="Message id for sys makeBrokerChannel (default: 3)",
    )
    parser.add_argument("--hello-host-name", default=None, help="Host name advertised in fox hello")
    parser.add_argument("--hello-host-address", default=None, help="Host address advertised in fox hello")
    parser.add_argument(
        "--circuit-id",
        type=int,
        default=random.randint(9000, 9900),
        help="Circuit id used when --sync-prelude is set",
    )
    parser.add_argument(
        "--circuit-step",
        type=int,
        default=2,
        help="Delta applied to circuit id for each sync cycle (default: 2)",
    )
    parser.add_argument(
        "--circuit-id-after-sub",
        type=int,
        default=None,
        help="Starting circuit id for sync cycles sent after station-sub (default: continue from --circuit-id progression)",
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
        "--sync-order",
        choices=("before-sub", "after-sub"),
        default="before-sub",
        help="When to send sync cycles relative to station-sub (default: before-sub)",
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
        default=0.001,
        help="Delay in seconds between payload sends (default: 0.001)",
    )
    parser.add_argument(
        "--wait-after-sub",
        type=float,
        default=0.05,
        help="Seconds to receive immediately after station-sub (default: 0.05)",
    )
    parser.add_argument(
        "--wait-after-hello",
        type=float,
        default=0.15,
        help="Seconds to receive immediately after fox hello (default: 0.15)",
    )
    parser.add_argument(
        "--wait-after-auth",
        type=float,
        default=0.15,
        help="Seconds to receive immediately after fox authMessage1 (default: 0.15)",
    )
    parser.add_argument(
        "--wait-after-broker",
        type=float,
        default=0.05,
        help="Seconds to receive immediately after sys makeBrokerChannel (default: 0.05)",
    )
    parser.add_argument(
        "--wait-after-sync-stream",
        type=float,
        default=0.02,
        help="Seconds to receive immediately after each circuit-stream (default: 0.02)",
    )
    args = parser.parse_args()

    newline = "\r\n" if args.crlf else "\n"

    def resolve_local_address() -> str:
        if args.hello_host_address:
            return args.hello_host_address
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
                probe.connect((args.host, int(args.port)))
                return probe.getsockname()[0]
        except Exception:
            return "127.0.0.1"

    def resolve_http_session() -> str:
        if args.fox_http_session:
            return args.fox_http_session

        repo_root = Path(__file__).resolve().parents[1]
        root_text = str(repo_root)
        if root_text not in sys.path:
            sys.path.insert(0, root_text)

        from gateway.niagara_client import NiagaraClient, NiagaraLoginOptions

        username = args.fox_user or os.getenv(args.fox_user_env)
        password = args.fox_pass or os.getenv(args.fox_pass_env)
        if not username or not password:
            raise RuntimeError(
                "Fox HTTP auth needs credentials; set --fox-user/--fox-pass or env vars from --fox-user-env/--fox-pass-env"
            )
        client = NiagaraClient(
            host=args.host,
            username=username,
            password=password,
            timeout_sec=max(3.0, float(args.timeout)),
            login_options=NiagaraLoginOptions(auth_mode="cookieDigest", endpoint_path="/login"),
        )
        client.ensure_login()
        cookie_jar = getattr(client, "_cookie_jar", None)
        if cookie_jar is None:
            raise RuntimeError("Niagara login succeeded but cookie jar was not available")
        for cookie in cookie_jar:
            if getattr(cookie, "name", "") == "niagara_session" and getattr(cookie, "value", ""):
                return cookie.value
        raise RuntimeError("Niagara login succeeded but niagara_session cookie was not found")

    def resolve_fox_user() -> str:
        user = args.fox_user or os.getenv(args.fox_user_env)
        if not user:
            raise RuntimeError(
                "Fox user is required; set --fox-user or environment variable from --fox-user-env"
            )
        return user

    msg_id = int(args.message_id)
    messages: list[FoxMessage] = []

    if args.fox_http_auth:
        fox_user = resolve_fox_user()
        http_session = resolve_http_session()
        host_name = args.hello_host_name or socket.gethostname()
        host_address = resolve_local_address()
        vm_uuid = str(uuid.uuid4())

        print(f"# fox http session len={len(http_session)} user={fox_user}")
        messages.append(
            _build_fox_client_hello(
                int(args.hello_msg_id),
                http_session,
                host_name=host_name,
                host_address=host_address,
                vm_uuid=vm_uuid,
                newline=newline,
            )
        )
        messages.append(
            _build_fox_auth_message1(
                int(args.auth_msg_id),
                fox_user,
                http_session,
                newline=newline,
            )
        )
        messages.append(_build_sys_make_broker_channel(int(args.broker_msg_id), newline=newline))

    sync_cycles = max(1, int(args.sync_cycles))
    circuit_step = int(args.circuit_step)

    def add_sync_cycles(current_msg_id: int, start_circuit_id: int) -> tuple[int, int]:
        current_circuit_id = int(start_circuit_id)
        for _ in range(sync_cycles):
            messages.append(_build_sync_open(current_msg_id, current_circuit_id, newline=newline))
            current_msg_id += 1
            messages.append(_build_sync_stream(current_msg_id, current_circuit_id, newline=newline))
            current_msg_id += 1
            messages.append(_build_sync_close(current_msg_id, current_circuit_id, newline=newline))
            current_msg_id += 1
            current_circuit_id += circuit_step
        return current_msg_id, current_circuit_id

    circuit_id = int(args.circuit_id)

    if args.sync_prelude and args.sync_order == "before-sub":
        msg_id, circuit_id = add_sync_cycles(msg_id, circuit_id)
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
    if args.sync_prelude and args.sync_order == "after-sub":
        after_sub_circuit = args.circuit_id_after_sub
        if after_sub_circuit is None:
            after_sub_circuit = circuit_id
        msg_id, circuit_id = add_sync_cycles(msg_id, after_sub_circuit)
    messages.append(_build_station_invoke(msg_id, args.ord, args.action, args.value, newline=newline))

    print("# outbound fox payloads")
    for idx, payload in enumerate(messages, start=1):
        print(f"--- payload {idx} ---")
        if isinstance(payload, bytes):
            rendered = payload.decode("latin-1", errors="replace")
            print(rendered, end="" if rendered.endswith("\n") else "\n")
        else:
            print(payload, end="" if payload.endswith("\n") else "\n")

    if args.dry_run:
        return 0

    response, send_error, recv_error = _send_and_recv(
        host=args.host,
        port=args.port,
        messages=messages,
        timeout=max(0.1, float(args.timeout)),
        recv_window=max(0.1, float(args.recv_window)),
        inter_message_delay=float(args.inter_message_delay),
        wait_after_hello=max(0.0, float(args.wait_after_hello)),
        wait_after_auth=max(0.0, float(args.wait_after_auth)),
        wait_after_broker=max(0.0, float(args.wait_after_broker)),
        wait_after_sub=max(0.0, float(args.wait_after_sub)),
        wait_after_sync_stream=max(0.0, float(args.wait_after_sync_stream)),
    )

    print("# inbound response")
    if response.strip():
        print(response)
    else:
        print("(no response bytes captured)")
    if send_error:
        print(f"# send error: {send_error}")
    if recv_error:
        print(f"# recv error: {recv_error}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
