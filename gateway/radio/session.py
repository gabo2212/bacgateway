from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from typing import Any, Optional

from .base import FrameTransport, RateLimiter
from proto import codec
from gateway import codec as gcodec

LOGGER = logging.getLogger(__name__)

# Re-export constants from canonical source so existing importers keep working.
START_REQUEST = gcodec.START_REQUEST
START_RESPONSE = gcodec.START_RESPONSE

CMD_READ_REQUEST = gcodec.CMD_READ_REQUEST
CMD_READ_RESPONSE = gcodec.CMD_READ_RESPONSE
CMD_WRITE_REQUEST = gcodec.CMD_WRITE_REQUEST
CMD_WRITE_RESPONSE = gcodec.CMD_WRITE_RESPONSE

RF_MODULE_START_NETWORK = gcodec.RF_MODULE_START_NETWORK
RF_MODULE_CONFIGURE_NETWORK = gcodec.RF_MODULE_CONFIGURE_NETWORK
RF_MODULE_DUPLICATE_COMM = gcodec.RF_MODULE_DUPLICATE_COMM
RF_MODULE_IDENTIFY = gcodec.RF_MODULE_IDENTIFY

# Use the canonical ParsedFrame from gateway.codec.
ParsedFrame = gcodec.ParsedFrame


class RadioError(RuntimeError):
    pass


class RadioTimeout(RadioError):
    pass


class RadioStatusError(RadioError):
    def __init__(self, status_code: int, status_label: str) -> None:
        super().__init__(f"Radio returned status {status_code}: {status_label}")
        self.status_code = status_code
        self.status_label = status_label


@dataclass
class RadioResponse:
    frame: ParsedFrame
    status_label: Optional[str]


@dataclass
class PointReadResult:
    value: Any
    response: RadioResponse


@dataclass
class PendingRequest:
    key: tuple[int, int, int, int]
    event: threading.Event
    response: Optional[ParsedFrame] = None


class RadioSession:
    def __init__(
        self,
        transport: FrameTransport,
        response_timeout: float = 2.0,
        retry_count: int = 3,
        inter_message_delay: float = 0.01,
        log_rate_limit: float = 1.0,
    ) -> None:
        self._transport = transport
        self._response_timeout = response_timeout
        self._retry_count = retry_count
        self._inter_message_delay = inter_message_delay

        self._pending: dict[tuple[int, int, int, int], PendingRequest] = {}
        self._pending_lock = threading.Lock()
        self._trans_seq = 0
        self._send_lock = threading.Lock()
        self._last_send = 0.0
        self._rate_limiter = RateLimiter(log_rate_limit)

        self._transport.set_frame_handler(self.handle_frame)

    def start(self) -> None:
        self._transport.open()

    def close(self) -> None:
        self._transport.close()

    def is_open(self) -> bool:
        return self._transport.is_open()

    def handle_frame(self, frame: bytes) -> None:
        try:
            parsed = self._parse_frame(frame)
        except Exception:
            LOGGER.exception("Failed to parse incoming frame")
            return

        if not parsed.length_ok:
            LOGGER.warning("Discarding frame with invalid length")
            return
        if not parsed.crc_ok:
            LOGGER.warning("Discarding frame with bad CRC")
            return

        self._log_frame("rx", parsed)

        key = (parsed.msg_type, parsed.cmd_type, parsed.comm_addr, parsed.trans_seq)
        with self._pending_lock:
            pending = self._pending.pop(key, None)
        if pending:
            pending.response = parsed
            pending.event.set()
        else:
            LOGGER.info(
                "Unsolicited frame msg=0x%04X cmd=%s comm=%s seq=%s",
                parsed.msg_type,
                parsed.cmd_type,
                parsed.comm_addr,
                parsed.trans_seq,
            )

    def identify_raw(self) -> RadioResponse:
        trans_seq = self._next_trans_seq()
        frame = gcodec.build_frame(RF_MODULE_IDENTIFY, CMD_READ_REQUEST, 0, trans_seq)
        return self._send_request(
            frame,
            expect_cmd=CMD_READ_RESPONSE,
            msg_type=RF_MODULE_IDENTIFY,
            comm_addr=0,
            trans_seq=trans_seq,
        )

    def identify(self) -> dict[str, Any]:
        response = self.identify_raw()
        payload_info = gcodec.parse_identify_payload(response.frame.payload)
        payload_info.update(self._response_metadata(response))
        return payload_info

    def read_network_config_raw(self) -> RadioResponse:
        trans_seq = self._next_trans_seq()
        frame = gcodec.build_frame(RF_MODULE_CONFIGURE_NETWORK, CMD_READ_REQUEST, 0, trans_seq)
        return self._send_request(
            frame,
            expect_cmd=CMD_READ_RESPONSE,
            msg_type=RF_MODULE_CONFIGURE_NETWORK,
            comm_addr=0,
            trans_seq=trans_seq,
        )

    def read_network_config(self) -> dict[str, Any]:
        response = self.read_network_config_raw()
        payload_info = gcodec.parse_network_config_payload(response.frame.payload)
        payload_info.update(self._response_metadata(response))
        return payload_info

    def configure_network(self, pan_id: int, channel: int) -> RadioResponse:
        trans_seq = self._next_trans_seq()
        payload = pan_id.to_bytes(2, "big") + bytes([channel & 0xFF])
        frame = gcodec.build_frame(
            RF_MODULE_CONFIGURE_NETWORK, CMD_WRITE_REQUEST, 0, trans_seq, payload
        )
        return self._send_request(
            frame,
            expect_cmd=CMD_WRITE_RESPONSE,
            msg_type=RF_MODULE_CONFIGURE_NETWORK,
            comm_addr=0,
            trans_seq=trans_seq,
        )

    def start_network(self) -> RadioResponse:
        trans_seq = self._next_trans_seq()
        frame = gcodec.build_frame(RF_MODULE_START_NETWORK, CMD_WRITE_REQUEST, 0, trans_seq)
        return self._send_request(
            frame,
            expect_cmd=CMD_WRITE_RESPONSE,
            msg_type=RF_MODULE_START_NETWORK,
            comm_addr=0,
            trans_seq=trans_seq,
        )

    def read_point(
        self, comm_addr: int, point_addr: int, logical_name: Optional[str] = None
    ) -> PointReadResult:
        trans_seq = self._next_trans_seq()
        frame = codec.build_read_point(comm_addr, point_addr, trans_seq)
        response = self._send_request(
            frame,
            expect_cmd=CMD_READ_RESPONSE,
            msg_type=point_addr,
            comm_addr=comm_addr,
            trans_seq=trans_seq,
        )
        if logical_name:
            value = codec.parse_point_value_with_name(
                point_addr, response.frame.payload, logical_name
            )
        else:
            value = codec.parse_point_value(point_addr, response.frame.payload)
        return PointReadResult(value=value, response=response)

    def write_point(
        self,
        comm_addr: int,
        point_addr: int,
        value: Any,
        logical_name: Optional[str] = None,
    ) -> RadioResponse:
        trans_seq = self._next_trans_seq()
        if logical_name:
            payload = codec.encode_point_value_with_name(point_addr, value, logical_name)
            frame = gcodec.build_frame(
                point_addr, CMD_WRITE_REQUEST, comm_addr, trans_seq, payload
            )
        else:
            frame = codec.build_write_point(comm_addr, point_addr, value, trans_seq)
        return self._send_request(
            frame,
            expect_cmd=CMD_WRITE_RESPONSE,
            msg_type=point_addr,
            comm_addr=comm_addr,
            trans_seq=trans_seq,
        )

    def _response_metadata(self, response: RadioResponse) -> dict[str, Any]:
        return {
            "status_code": response.frame.status,
            "status_label": response.status_label,
            "link_quality": response.frame.link_quality,
        }

    def _send_request(
        self,
        frame: bytes,
        *,
        expect_cmd: int,
        msg_type: int,
        comm_addr: int,
        trans_seq: int,
        timeout: Optional[float] = None,
        retries: Optional[int] = None,
    ) -> RadioResponse:
        timeout = self._response_timeout if timeout is None else timeout
        retries = self._retry_count if retries is None else retries

        for attempt in range(1, retries + 1):
            pending = PendingRequest(
                key=(msg_type, expect_cmd, comm_addr, trans_seq),
                event=threading.Event(),
            )
            with self._pending_lock:
                self._pending[pending.key] = pending

            try:
                self._send_frame(frame)
            except Exception:
                with self._pending_lock:
                    self._pending.pop(pending.key, None)
                raise

            if pending.event.wait(timeout=timeout):
                assert pending.response is not None
                response = RadioResponse(
                    frame=pending.response,
                    status_label=gcodec.status_label(pending.response.status),
                )
                if pending.response.status not in (None, 0):
                    raise RadioStatusError(
                        pending.response.status,
                        response.status_label or "Unknown",
                    )
                return response

            with self._pending_lock:
                self._pending.pop(pending.key, None)

            LOGGER.warning(
                "Timeout waiting for response msg=0x%04X cmd=%s comm=%s seq=%s (attempt %s/%s)",
                msg_type,
                expect_cmd,
                comm_addr,
                trans_seq,
                attempt,
                retries,
            )

        raise RadioTimeout(
            f"No response for msg=0x{msg_type:04X} cmd={expect_cmd} comm={comm_addr}"
        )

    def _send_frame(self, frame: bytes) -> None:
        with self._send_lock:
            now = time.monotonic()
            delta = now - self._last_send
            if delta < self._inter_message_delay:
                time.sleep(self._inter_message_delay - delta)
            self._transport.send_frame(frame)
            self._last_send = time.monotonic()
        self._log_frame("tx", self._parse_frame(frame))

    def _parse_frame(self, frame: bytes) -> ParsedFrame:
        return gcodec.parse_frame(frame)

    def _log_frame(self, direction: str, parsed: ParsedFrame) -> None:
        key = f"{direction}:{parsed.msg_type:04x}:{parsed.cmd_type}"
        if not self._rate_limiter.allow(key):
            return
        LOGGER.info(
            "frame_%s msg=0x%04X cmd=%s comm=%s seq=%s len=%s crc_ok=%s status=%s",
            direction,
            parsed.msg_type,
            parsed.cmd_type,
            parsed.comm_addr,
            parsed.trans_seq,
            len(parsed.raw),
            parsed.crc_ok,
            parsed.status,
        )

    def _next_trans_seq(self) -> int:
        with self._pending_lock:
            self._trans_seq = (self._trans_seq + 1) % 255
            return self._trans_seq
