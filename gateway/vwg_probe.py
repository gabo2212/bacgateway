"""Minimal probe library + CLI for raw VWG radio connectivity testing.

Library usage::

    from gateway.vwg_probe import raw_identify, ProbeResult
    result = raw_identify(port="COM3")

CLI usage::

    python -m gateway.vwg_probe raw-frames --port COM3
    python -m gateway.vwg_probe raw-identify --port COM3
"""
from __future__ import annotations

import argparse
import logging
import sys
from dataclasses import dataclass, field
from typing import Any, Optional

from gateway import codec as gcodec
from gateway.radio.session import RadioSession, RadioTimeout
from gateway.vwg_serial import VwgSerialTransport

LOGGER = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# ProbeResult
# ---------------------------------------------------------------------------


@dataclass
class ProbeResult:
    success: bool
    port: str
    attempts: int
    tx_hex: Optional[str] = None
    rx_hex: Optional[str] = None
    info: dict[str, Any] = field(default_factory=dict)
    error: Optional[str] = None


# ---------------------------------------------------------------------------
# raw_identify
# ---------------------------------------------------------------------------


def raw_identify(
    port: str,
    baud: int = 57600,
    rtscts: Optional[bool] = None,
    timeout: float = 2.0,
    retries: int = 3,
    *,
    _transport: Optional[VwgSerialTransport] = None,
) -> ProbeResult:
    """Send one RF_MODULE_IDENTIFY request and return a ProbeResult.

    Computes attempts from the transport TX counter delta.
    Captures tx_hex / rx_hex from transport-level last_tx_frame / last_rx_frame.
    Catches RadioTimeout and returns ProbeResult(success=False, ...) instead of re-raising.
    """
    if rtscts is None:
        import sys as _sys
        rtscts = _sys.platform != "win32"

    transport = _transport or VwgSerialTransport(port=port, baud=baud, rtscts=rtscts)
    session = RadioSession(transport, response_timeout=timeout, retry_count=retries)
    session.start()

    tx_before = transport.counters.frames_tx
    try:
        response = session.identify_raw()
    except RadioTimeout as exc:
        attempts = transport.counters.frames_tx - tx_before
        tx_hex = transport.last_tx_frame.hex().upper() if transport.last_tx_frame else None
        rx_hex = transport.last_rx_frame.hex().upper() if transport.last_rx_frame else None
        return ProbeResult(
            success=False,
            port=port,
            attempts=attempts,
            tx_hex=tx_hex,
            rx_hex=rx_hex,
            error=str(exc),
        )
    except Exception as exc:
        session.close()
        raise
    finally:
        session.close()

    attempts = transport.counters.frames_tx - tx_before
    tx_hex = transport.last_tx_frame.hex().upper() if transport.last_tx_frame else None
    rx_hex = transport.last_rx_frame.hex().upper() if transport.last_rx_frame else None
    info = gcodec.parse_identify_payload(response.frame.payload)

    return ProbeResult(
        success=True,
        port=port,
        attempts=attempts,
        tx_hex=tx_hex,
        rx_hex=rx_hex,
        info=info,
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _add_transport_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--port", required=True, help="Serial port (e.g. COM3 or /dev/ttyUSB0)")
    parser.add_argument("--baud", type=int, default=57600)
    parser.add_argument("--rtscts", action="store_true", default=None)
    parser.add_argument("--no-rtscts", dest="rtscts", action="store_false")
    parser.add_argument("--timeout", type=float, default=2.0)
    parser.add_argument("--retries", type=int, default=3)


def main(argv: Optional[list[str]] = None) -> None:
    logging.basicConfig(level=logging.INFO)
    parser = argparse.ArgumentParser(description="VWG radio probe (gateway-level)")
    sub = parser.add_subparsers(dest="command", required=True)

    id_parser = sub.add_parser("raw-identify", help="Send one IDENTIFY frame and show result")
    _add_transport_args(id_parser)

    frames_parser = sub.add_parser(
        "raw-frames", help="Open port and log raw frames (Ctrl-C to stop)"
    )
    _add_transport_args(frames_parser)

    args = parser.parse_args(argv)

    if args.command == "raw-identify":
        result = raw_identify(
            port=args.port,
            baud=args.baud,
            rtscts=args.rtscts,
            timeout=args.timeout,
            retries=args.retries,
        )
        print(f"success : {result.success}")
        print(f"port    : {result.port}")
        print(f"attempts: {result.attempts}")
        print(f"TX      : {result.tx_hex}")
        print(f"RX      : {result.rx_hex}")
        if result.success:
            for k, v in result.info.items():
                print(f"  {k}: {v}")
        else:
            print(f"error   : {result.error}")
        sys.exit(0 if result.success else 1)

    if args.command == "raw-frames":
        import time as _time
        transport = VwgSerialTransport(
            port=args.port, baud=args.baud, rtscts=args.rtscts
        )

        def _log(frame: bytes) -> None:
            print(f"RX [{len(frame):3d}]: {frame.hex().upper()}")

        transport.set_frame_handler(_log)
        transport.open()
        print(f"Listening on {args.port} — press Ctrl-C to stop")
        try:
            while True:
                _time.sleep(0.5)
        except KeyboardInterrupt:
            pass
        finally:
            transport.close()
            c = transport.counters
            print(
                f"\nCounters: frames_rx={c.frames_rx} crc_errors={c.crc_errors}"
                f" length_errors={c.length_errors}"
            )


if __name__ == "__main__":
    main()

