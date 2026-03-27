#!/usr/bin/env python3
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from gateway.radio.session import (
    RadioSession,
    RF_MODULE_CONFIGURE_NETWORK,
    RF_MODULE_IDENTIFY,
    CMD_READ_REQUEST,
)
from gateway.radio.vwg_serial import VwgSerialTransport
from gateway import codec as gcodec
from proto import codec

logging.basicConfig(level=logging.INFO)
LOGGER = logging.getLogger(__name__)


def _parse_int(text: str) -> int:
    text = text.strip().lower()
    if text.startswith("0x"):
        return int(text, 16)
    if all(c in "0123456789abcdef" for c in text):
        return int(text, 16)
    return int(text)


def _parse_value(text: str) -> Any:
    try:
        if "." in text:
            return float(text)
        return int(text)
    except ValueError:
        return text


def _format_hex(data: bytes) -> str:
    return data.hex().upper()


_build_frame = gcodec.build_frame
_parse_identify_payload = gcodec.parse_identify_payload
_parse_network_config_payload = gcodec.parse_network_config_payload


def _load_radio_defaults(path: str) -> dict[str, Any]:
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = yaml.safe_load(handle) or {}
    except FileNotFoundError:
        return {}
    return data if isinstance(data, dict) else {}


def _print_response(response, request_bytes: bytes, value: Any | None = None) -> None:
    print(f"TX: {_format_hex(request_bytes)}")
    print(f"RX: {_format_hex(response.frame.raw)}")
    print(
        "Decoded:",
        f"msg_type=0x{response.frame.msg_type:04X}",
        f"cmd_type={response.frame.cmd_type}",
        f"comm_addr={response.frame.comm_addr}",
        f"trans_seq={response.frame.trans_seq}",
        f"status={response.frame.status}",
        f"status_label={response.status_label}",
        f"link_quality={response.frame.link_quality}",
        f"crc_ok={response.frame.crc_ok}",
    )
    if value is not None:
        print(f"Value: {value}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Probe the VWG serial radio")
    parser.add_argument(
        "--config",
        default="config/radio.yaml",
        help="Path to radio config (default: config/radio.yaml)",
    )
    parser.add_argument("--port", help="Serial port override (e.g. COM3 or /dev/ttyUSB0)")
    parser.add_argument("--baud", type=int, help="Baud rate override")
    parser.add_argument("--rtscts", action="store_true", help="Enable RTS/CTS")
    parser.add_argument("--no-rtscts", action="store_true", help="Disable RTS/CTS")
    parser.add_argument("--timeout", type=float, default=2.0)
    parser.add_argument("--retries", type=int, default=3)

    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("identify", help="Read coordinator identify info")
    subparsers.add_parser("netcfg", help="Read network configuration")

    scan_parser = subparsers.add_parser(
        "scan",
        help="Scan a range of comm_addrs to discover responding thermostats",
    )
    scan_parser.add_argument(
        "--start",
        type=lambda x: int(x, 0),
        default=1,
        help="First comm_addr to probe (default: 1)",
    )
    scan_parser.add_argument(
        "--end",
        type=lambda x: int(x, 0),
        default=50,
        help="Last comm_addr to probe inclusive (default: 50)",
    )
    scan_parser.add_argument(
        "--point",
        default="0x1000",
        help="Point address to read for presence detection (default: 0x1000 Room Temperature)",
    )

    read_parser = subparsers.add_parser("read", help="Read a point")
    read_parser.add_argument("--comm-addr", required=True)
    read_parser.add_argument("--point", required=True)
    read_parser.add_argument("--logical-name")

    write_parser = subparsers.add_parser("write", help="Write a point")
    write_parser.add_argument("--comm-addr", required=True)
    write_parser.add_argument("--point", required=True)
    write_parser.add_argument("--value", required=True)
    write_parser.add_argument("--logical-name")

    args = parser.parse_args()

    defaults = _load_radio_defaults(args.config)
    port = args.port or defaults.get("serial_port") or defaults.get("port")
    if not port:
        raise SystemExit("Missing serial port (use --port or config/radio.yaml)")
    baud = args.baud or int(defaults.get("baud", 57600))
    if args.no_rtscts:
        rtscts = False
    elif args.rtscts:
        rtscts = True
    else:
        rtscts = bool(defaults.get("rtscts", True))

    transport = VwgSerialTransport(port=port, baud=baud, rtscts=rtscts)
    session = RadioSession(transport, response_timeout=args.timeout, retry_count=args.retries)
    session.start()

    try:
        if args.command == "identify":
            response = session.identify_raw()
            request = _build_frame(
                RF_MODULE_IDENTIFY, CMD_READ_REQUEST, 0, response.frame.trans_seq
            )
            info = _parse_identify_payload(response.frame.payload)
            _print_response(response, request)
            print("Info:", info)
            return

        if args.command == "netcfg":
            response = session.read_network_config_raw()
            request = _build_frame(
                RF_MODULE_CONFIGURE_NETWORK,
                CMD_READ_REQUEST,
                0,
                response.frame.trans_seq,
            )
            info = _parse_network_config_payload(response.frame.payload)
            _print_response(response, request)
            print("Info:", info)
            return

        if args.command == "scan":
            point_addr = _parse_int(args.point)
            found: list[int] = []
            total = args.end - args.start + 1
            for comm_addr in range(args.start, args.end + 1):
                print(
                    f"  Probing comm_addr={comm_addr} ({comm_addr - args.start + 1}/{total})...",
                    end="",
                    flush=True,
                )
                try:
                    result = session.read_point(comm_addr, point_addr)
                    found.append(comm_addr)
                    print(f" FOUND  value={result.value}  lq={result.response.frame.link_quality}")
                except Exception as exc:
                    print(f" no response ({type(exc).__name__})")
            print()
            if found:
                print(f"Thermostats found at comm_addrs: {found}")
                print("Update points.yaml with the correct comm_addr value.")
            else:
                print("No thermostats responded in the scanned range.")
            return

        comm_addr = _parse_int(args.comm_addr)
        point_addr = _parse_int(args.point)
        logical_name = args.logical_name

        if args.command == "read":
            result = session.read_point(comm_addr, point_addr, logical_name)
            request = codec.build_read_point(
                comm_addr, point_addr, result.response.frame.trans_seq
            )
            _print_response(result.response, request, result.value)
            return

        if args.command == "write":
            value = _parse_value(args.value)
            response = session.write_point(comm_addr, point_addr, value, logical_name)
            if logical_name:
                payload = codec.encode_point_value_with_name(point_addr, value, logical_name)
                request = _build_frame(
                    point_addr,
                    0x02,
                    comm_addr,
                    response.frame.trans_seq,
                    payload,
                )
            else:
                request = codec.build_write_point(
                    comm_addr, point_addr, value, response.frame.trans_seq
                )
            _print_response(response, request, value)
            return

    finally:
        session.close()


if __name__ == "__main__":
    main()
