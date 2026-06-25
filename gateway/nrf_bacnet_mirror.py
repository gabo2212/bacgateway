from __future__ import annotations

import argparse
import asyncio
import logging
import sys
import threading
import time
from pathlib import Path
from typing import Any

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    __package__ = "gateway"

LOGGER = logging.getLogger(__name__)


class NrfBacnetMirror:
    def __init__(
        self,
        *,
        port: str,
        channel: int,
        registry: Any,
        nrf_client: Any,
        live_adapter: Any,
        ota_adapter: Any,
        limit: int | None = None,
    ) -> None:
        self.port = port
        self.channel = channel
        self.registry = registry
        self._client = nrf_client
        self._live_adapter = live_adapter
        self._ota_adapter = ota_adapter
        self._limit = limit
        self._stop_event = threading.Event()
        self._done_event = threading.Event()
        self._thread = threading.Thread(target=self._rx_loop, daemon=True)
        self.frame_count = 0
        self.event_count = 0

    def start(self) -> None:
        self._client.connect()
        self._stop_event.clear()
        self._done_event.clear()
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        try:
            self._client.disconnect()
        finally:
            if self._thread.is_alive():
                self._thread.join(timeout=2.0)

    async def wait_until_done(self) -> None:
        while not self._done_event.is_set():
            await asyncio.sleep(0.1)

    def _rx_loop(self) -> None:
        from gateway.radio.nrf_bridge_proto import FrameType, RxFramesPayload

        try:
            while not self._stop_event.is_set():
                saw_frame = False
                for frame in self._client.read_frames():
                    if self._stop_event.is_set():
                        break
                    saw_frame = True
                    if frame.frame_type == FrameType.RX_FRAME:
                        rx_payload = RxFramesPayload.decode(frame.payload)
                        self.frame_count += 1
                        if rx_payload.channel != self.channel:
                            LOGGER.debug(
                                "nrf_rx_channel_mismatch",
                                extra={
                                    "expected_channel": self.channel,
                                    "actual_channel": rx_payload.channel,
                                },
                            )
                        event = self._live_adapter.process_rx_frame(rx_payload)
                        if event is not None:
                            self.event_count += 1
                            self._ota_adapter.apply_event(event)
                        if self._limit is not None and self.frame_count >= self._limit:
                            self._stop_event.set()
                            break
                    elif frame.frame_type == FrameType.ERROR:
                        LOGGER.error(
                            "nrf_bridge_error",
                            extra={
                                "message": frame.payload.decode(errors="replace"),
                            },
                        )
                if self._limit is not None and self.frame_count >= self._limit:
                    break
                if not saw_frame:
                    time.sleep(0.05)
        except Exception:
            LOGGER.exception("nrf_rx_loop_failed")
        finally:
            self._done_event.set()


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Same-process nRF RX to BACnet/IP passive mirror",
    )
    parser.add_argument(
        "--config",
        default="points.yaml",
        help="Path to points config (default: points.yaml)",
    )
    parser.add_argument(
        "--port",
        required=True,
        help="nRF USB serial port (for example COM7 or /dev/ttyACM0)",
    )
    parser.add_argument(
        "--channel",
        type=int,
        required=True,
        help="IEEE 802.15.4 receive channel",
    )
    parser.add_argument("--limit", type=int, help="Exit after N received RX frames")
    parser.add_argument(
        "--dump-unknown-codes",
        action="store_true",
        help="Log passive candidate_ota_point entries for unknown OTA point codes",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        help="Python logging level (default: INFO)",
    )
    parser.add_argument("--device-id", type=int, default=5001)
    parser.add_argument("--device-name", default="BAC Viconics nRF Mirror")
    parser.add_argument("--bind-address", default="0.0.0.0")
    parser.add_argument("--bacnet-port", type=int, default=47808)
    return parser


async def run_mirror(args: argparse.Namespace) -> None:
    from gateway.bacnet_server import PointRegistry, create_app_from_config
    from gateway.identity_registry import IdentityRegistry
    from gateway.model import load_config
    from gateway.ota.pointmap import load_pointmap
    from gateway.radio.live_adapter import LiveAdapter
    from gateway.radio.nrf_bridge_client import NrfBridgeClient
    from gateway.radio.ota_registry_adapter import OtaRegistryAdapter

    cfg = load_config(args.config)
    registry = PointRegistry(cfg)
    identity_registry = IdentityRegistry.from_config(cfg)
    pointmap_path = Path(__file__).resolve().parent / "ota" / "pointmap.yaml"

    bacnet_app = create_app_from_config(
        cfg,
        registry,
        radio=None,
        device_id=args.device_id,
        device_name=args.device_name,
        bind_address=args.bind_address,
        port=args.bacnet_port,
    )
    mirror = NrfBacnetMirror(
        port=args.port,
        channel=args.channel,
        registry=registry,
        nrf_client=NrfBridgeClient(port=args.port),
        live_adapter=LiveAdapter(point_map=load_pointmap(pointmap_path)),
        ota_adapter=OtaRegistryAdapter(
            registry,
            identity_registry=identity_registry,
            dump_unknown_codes=args.dump_unknown_codes,
        ),
        limit=args.limit,
    )

    LOGGER.warning(
        "receive_only_live_mirror",
        extra={
            "port": args.port,
            "channel": args.channel,
            "config": args.config,
            "bacnet_port": args.bacnet_port,
        },
    )
    mirror.start()
    try:
        if args.limit is None:
            await asyncio.Future()
        else:
            await mirror.wait_until_done()
    except asyncio.CancelledError:
        pass
    finally:
        mirror.stop()
        try:
            bacnet_app.close()
        except Exception:
            LOGGER.exception("bacnet_app_close_failed")


def main(argv: list[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=getattr(logging, str(args.log_level).upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    try:
        asyncio.run(run_mirror(args))
    except KeyboardInterrupt:
        return 130
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
