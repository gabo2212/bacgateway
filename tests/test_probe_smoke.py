"""Byte-stream smoke tests for gateway.vwg_serial transport and gateway.vwg_probe.

Required acceptance-gating tests (session-level tests alone are insufficient).
All six test classes must pass for Step 1 to be considered complete.
"""
from __future__ import annotations

import threading
import time
import unittest
from typing import Optional

from gateway import codec as gcodec
from gateway.vwg_serial import VwgSerialTransport
from gateway.radio.session import RadioSession, RadioTimeout
from gateway.vwg_probe import raw_identify, ProbeResult


# ---------------------------------------------------------------------------
# MockSerial — duck-typed serial.Serial substitute for testing
# ---------------------------------------------------------------------------


class MockSerial:
    """Reads from a pre-loaded byte buffer; write() is a no-op."""

    def __init__(self, data: bytes, timeout: float = 0.05) -> None:
        self._buf = bytearray(data)
        self._pos = 0
        self.is_open = True

    def read(self, n: int) -> bytes:
        if self._pos >= len(self._buf):
            return b""
        chunk = self._buf[self._pos : self._pos + n]
        self._pos += len(chunk)
        return bytes(chunk)

    def write(self, data: bytes) -> int:
        return len(data)

    def flush(self) -> None:
        pass

    def close(self) -> None:
        self.is_open = False


# ---------------------------------------------------------------------------
# Helpers to build synthetic frames
# ---------------------------------------------------------------------------


def _make_response_frame(
    msg_type: int,
    cmd_type: int,
    comm_addr: int,
    trans_seq: int,
    status: int = 0,
    payload: bytes = b"",
    lq: int = 80,
) -> bytes:
    """Build a 0x3C radio-to-host frame."""
    body = bytearray()
    body.extend(msg_type.to_bytes(2, "big"))
    body.append(cmd_type & 0xFF)
    body.append(comm_addr & 0xFF)
    body.append(trans_seq & 0xFF)
    body.append(status)
    body.extend(payload)
    body.append(lq)
    crc = sum(body) & 0xFF
    return bytes([0x3C, len(body) + 1]) + bytes(body) + bytes([crc])


def _inject_serial(transport: VwgSerialTransport, mock: MockSerial) -> None:
    """Directly inject a MockSerial into a transport (bypasses real serial open)."""
    transport._serial = mock  # type: ignore[attr-defined]
    transport._stop_event.clear()


# ---------------------------------------------------------------------------
# TestFrameResync
# ---------------------------------------------------------------------------


class TestFrameResync(unittest.TestCase):
    """Garbage bytes before a valid frame must be skipped; frame must be received."""

    def test_resync_skips_garbage(self) -> None:
        valid = gcodec.build_frame(gcodec.RF_MODULE_IDENTIFY, gcodec.CMD_READ_REQUEST, 0, 1)
        stream = bytes([0xDE, 0xAD, 0xBE, 0xEF]) + valid
        mock = MockSerial(stream)
        transport = VwgSerialTransport.__new__(VwgSerialTransport)
        transport._stop_event = threading.Event()
        transport.counters = __import__("gateway.vwg_serial", fromlist=["TransportCounters"]).TransportCounters()
        transport.last_rx_frame = None
        transport.last_tx_frame = None
        transport._handler = None
        _inject_serial(transport, mock)

        received: list[bytes] = []
        transport._handler = received.append  # type: ignore[assignment]
        frame = transport._read_frame()  # type: ignore[attr-defined]
        self.assertIsNotNone(frame)
        self.assertEqual(frame, valid)
        self.assertEqual(transport.counters.frames_rx, 1)
        self.assertEqual(transport.counters.crc_errors, 0)


# ---------------------------------------------------------------------------
# TestBadLengthRejection
# ---------------------------------------------------------------------------


class TestBadLengthRejection(unittest.TestCase):
    """Frame with a LEN byte below MIN_MSG_LENGTH must be discarded."""

    def test_short_length_rejected(self) -> None:
        # LEN=3 < MIN_MSG_LENGTH=6 → length_errors incremented, returns None
        stream = bytes([0x40, 0x03, 0x00, 0x00, 0x00])
        mock = MockSerial(stream)
        transport = VwgSerialTransport.__new__(VwgSerialTransport)
        transport._stop_event = threading.Event()
        from gateway.vwg_serial import TransportCounters
        transport.counters = TransportCounters()
        transport.last_rx_frame = None
        transport.last_tx_frame = None
        transport._handler = None
        _inject_serial(transport, mock)

        frame = transport._read_frame()  # type: ignore[attr-defined]
        self.assertIsNone(frame)
        self.assertEqual(transport.counters.length_errors, 1)
        self.assertEqual(transport.counters.frames_rx, 0)


# ---------------------------------------------------------------------------
# TestCrcRejection
# ---------------------------------------------------------------------------


class TestCrcRejection(unittest.TestCase):
    """Frame with a corrupted CRC byte must be discarded and crc_errors incremented."""

    def _make_transport_with_stream(self, stream: bytes) -> VwgSerialTransport:
        from gateway.vwg_serial import TransportCounters
        transport = VwgSerialTransport.__new__(VwgSerialTransport)
        transport._stop_event = threading.Event()
        transport.counters = TransportCounters()
        transport.last_rx_frame = None
        transport.last_tx_frame = None
        transport._handler = None
        _inject_serial(transport, MockSerial(stream))
        return transport

    def test_bad_crc_discarded(self) -> None:
        good = gcodec.build_frame(gcodec.RF_MODULE_IDENTIFY, gcodec.CMD_READ_REQUEST, 0, 2)
        corrupted = bytearray(good)
        corrupted[-1] ^= 0xFF  # flip CRC
        transport = self._make_transport_with_stream(bytes(corrupted))
        frame = transport._read_frame()  # type: ignore[attr-defined]
        self.assertIsNone(frame)
        self.assertEqual(transport.counters.crc_errors, 1)
        self.assertEqual(transport.counters.frames_rx, 0)


# ---------------------------------------------------------------------------
# TestStatusHandling
# ---------------------------------------------------------------------------


class TestStatusHandling(unittest.TestCase):
    """Status byte must be extracted for response cmd_types and absent for requests."""

    def test_response_has_status(self) -> None:
        frame = _make_response_frame(0x0F03, gcodec.CMD_READ_RESPONSE, 0, 3, status=0)
        pf = gcodec.parse_frame(frame)
        self.assertEqual(pf.status, 0)
        self.assertIsNotNone(pf.link_quality_raw)

    def test_request_has_no_status(self) -> None:
        frame = gcodec.build_frame(0x0F03, gcodec.CMD_READ_REQUEST, 0, 4)
        pf = gcodec.parse_frame(frame)
        self.assertIsNone(pf.status)


# ---------------------------------------------------------------------------
# DummyTransport — for session-level integration tests
# ---------------------------------------------------------------------------


class DummyTransport:
    """A transport that optionally injects one response frame synchronously."""

    def __init__(self, response_frame: Optional[bytes] = None) -> None:
        self._handler = None
        self._response = response_frame
        self.frames_sent: list[bytes] = []
        from gateway.vwg_serial import TransportCounters
        self.counters = TransportCounters()
        self.last_tx_frame: Optional[bytes] = None
        self.last_rx_frame: Optional[bytes] = response_frame

    def set_frame_handler(self, handler) -> None:
        self._handler = handler

    def open(self) -> None:
        pass

    def close(self) -> None:
        pass

    def is_open(self) -> bool:
        return True

    def send_frame(self, frame: bytes) -> None:
        self.frames_sent.append(frame)
        self.counters.frames_tx += 1
        self.last_tx_frame = frame
        if self._response and self._handler:
            self._handler(self._response)


# ---------------------------------------------------------------------------
# TestTimeoutRetry
# ---------------------------------------------------------------------------


class TestTimeoutRetry(unittest.TestCase):
    """raw_identify with a transport that never responds must return success=False."""

    def test_timeout_returns_failure(self) -> None:
        dummy = DummyTransport(response_frame=None)
        result = raw_identify(
            port="FAKE",
            timeout=0.05,
            retries=2,
            _transport=dummy,
        )
        self.assertFalse(result.success)
        self.assertIsNotNone(result.error)
        self.assertEqual(result.attempts, 2)


# ---------------------------------------------------------------------------
# TestRawIdentifySuccess
# ---------------------------------------------------------------------------


class TestRawIdentifySuccess(unittest.TestCase):
    """raw_identify with a well-formed response must return a populated ProbeResult."""

    def test_success_returns_probe_result(self) -> None:
        # Build a valid identify payload: 13 bytes
        identify_payload = bytes([1, 5, 0x12, 0x34] + [0xAB] * 8 + [3])

        # We don't know the trans_seq the session will assign (it increments from 0 to 1).
        # We build the response lazily by capturing the sent frame in a custom transport.
        class LazyResponseTransport(DummyTransport):
            def send_frame(self, frame: bytes) -> None:
                self.frames_sent.append(frame)
                self.counters.frames_tx += 1
                self.last_tx_frame = frame
                # Parse the request to mirror msg_type, comm_addr, trans_seq
                req = gcodec.parse_frame(frame)
                resp = _make_response_frame(
                    req.msg_type, gcodec.CMD_READ_RESPONSE,
                    req.comm_addr, req.trans_seq,
                    status=0, payload=identify_payload,
                )
                self.last_rx_frame = resp
                if self._handler:
                    self._handler(resp)

        dummy = LazyResponseTransport(response_frame=None)
        result = raw_identify(port="FAKE", timeout=1.0, retries=1, _transport=dummy)

        self.assertTrue(result.success)
        self.assertEqual(result.port, "FAKE")
        self.assertEqual(result.attempts, 1)
        self.assertIsNotNone(result.tx_hex)
        self.assertIsNotNone(result.rx_hex)
        self.assertEqual(result.info.get("firmware_maj"), 1)
        self.assertEqual(result.info.get("firmware_min"), 5)
        self.assertEqual(result.info.get("zigbee_addr"), "0x1234")


if __name__ == "__main__":
    unittest.main()

