import threading
import time
import unittest

from gateway.radio.session import RadioSession
from proto import codec


class DummyTransport:
    def __init__(self) -> None:
        self.sent_frames: list[bytes] = []
        self._handler = None
        self._open = False

    def set_frame_handler(self, handler) -> None:
        self._handler = handler

    def open(self) -> None:
        self._open = True

    def close(self) -> None:
        self._open = False

    def is_open(self) -> bool:
        return self._open

    def send_frame(self, frame: bytes) -> None:
        self.sent_frames.append(frame)

    def inject_frame(self, frame: bytes) -> None:
        if self._handler:
            self._handler(frame)


def build_read_response(
    msg_type: int, comm_addr: int, trans_seq: int, payload: bytes
) -> bytes:
    response = bytearray()
    response.append(0x3C)
    response.append(0x00)
    response.extend(msg_type.to_bytes(2, "big"))
    response.append(0x01)  # READ_RESPONSE
    response.append(comm_addr & 0xFF)
    response.append(trans_seq & 0xFF)
    response.append(0x00)  # status
    response.extend(payload)
    response.append(0x2A)  # link quality
    response.append(0x00)  # crc placeholder
    response[1] = len(response) - 2
    response[-1] = sum(response[2:-1]) & 0xFF
    return bytes(response)


class SessionMatchingTests(unittest.TestCase):
    def test_read_point_matches_response(self) -> None:
        transport = DummyTransport()
        session = RadioSession(transport, response_timeout=0.5, retry_count=1)
        session.start()

        def responder() -> None:
            while not transport.sent_frames:
                time.sleep(0.01)
            request = transport.sent_frames[0]
            parsed = codec.parse_received_frame(request)
            payload = codec.encode_point_value(parsed["msg_type"], 72.5)
            response = build_read_response(
                parsed["msg_type"], parsed["comm_addr"], parsed["trans_seq"], payload
            )
            transport.inject_frame(response)

        threading.Thread(target=responder, daemon=True).start()
        result = session.read_point(1, 0x1000)
        self.assertTrue(result.response.frame.crc_ok)
        self.assertEqual(result.response.frame.msg_type, 0x1000)


if __name__ == "__main__":
    unittest.main()
