#!/usr/bin/env python3
from __future__ import annotations

import argparse
import logging
from typing import Any

import yaml

from gateway.radio.session import (
    RadioSession,
    RF_MODULE_CONFIGURE_NETWORK,
    RF_MODULE_IDENTIFY,
    CMD_READ_REQUEST,
)
from gateway.radio.vwg_serial import VwgSerialTransport
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


def _build_frame(
    msg_type: int,
    cmd_type: int,
    comm_addr: int,
    trans_seq: int,
    payload: bytes = b"",
) -> bytes:
    frame = bytearray()
    frame.append(0x40)
    frame.append(0x00)
    frame.extend(msg_type.to_bytes(2, "big"))
    frame.append(cmd_type & 0xFF)
    frame.append(comm_addr & 0xFF)
    frame.append(trans_seq & 0xFF)
    frame.extend(payload)
    frame.append(sum(frame[2:]) & 0xFF)
    frame[1] = len(frame) - 2
    return bytes(frame)


def _parse_identify_payload(payload: bytes) -> dict[str, Any]:
    if len(payload) != 13:
        raise ValueError("identify payload must be 13 bytes")
    firmware_maj = payload[0]
    firmware_min = payload[1]
    zigbee_addr = int.from_bytes(payload[2:4], "big")
    ieee_addr = payload[4:12].hex().upper()
    chip_rev = payload[12]
    return {
        "firmware_maj": firmware_maj,
        "firmware_min": firmware_min,
        "zigbee_addr": f"0x{zigbee_addr:04X}",
        "ieee_addr": f"0x{ieee_addr}",
        "chip_rev": chip_rev,
    }


def _parse_network_config_payload(payload: bytes) -> dict[str, Any]:
    if len(payload) != 3:
        raise ValueError("network config payload must be 3 bytes")
    pan_id = int.from_bytes(payload[0:2], "big")
    channel = payload[2]
    return {"pan_id": pan_id, "channel": channel}


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
