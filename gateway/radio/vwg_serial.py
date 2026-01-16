from __future__ import annotations

import logging
import threading
import time
from typing import Optional

try:
    import serial
    from serial import SerialException
except ImportError as exc:  # pragma: no cover - handled during runtime
    raise ImportError("pyserial is required for serial transport") from exc

from .base import FrameHandler

LOGGER = logging.getLogger(__name__)

START_BYTES = (0x3C, 0x40)
MIN_MSG_LENGTH = 6
MAX_MSG_LENGTH = 255


class VwgSerialTransport:
    def __init__(
        self,
        port: str,
        baud: int = 57600,
        rtscts: bool = True,
        timeout: float = 0.2,
        write_timeout: float = 1.0,
        reconnect_min_delay: float = 1.0,
        reconnect_max_delay: float = 5.0,
    ) -> None:
        self._port = port
        self._baud = baud
        self._rtscts = rtscts
        self._timeout = timeout
        self._write_timeout = write_timeout
        self._reconnect_min_delay = reconnect_min_delay
        self._reconnect_max_delay = reconnect_max_delay

        self._handler: Optional[FrameHandler] = None
        self._serial: Optional[serial.Serial] = None
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._write_lock = threading.Lock()

    def set_frame_handler(self, handler: FrameHandler) -> None:
        self._handler = handler

    def open(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._reader_loop, daemon=True)
        self._thread.start()

    def close(self) -> None:
        self._stop_event.set()
        self._close_serial()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)

    def is_open(self) -> bool:
        return self._serial is not None and self._serial.is_open

    def send_frame(self, frame: bytes) -> None:
        if not self.is_open():
            raise ConnectionError("serial port is not open")
        assert self._serial is not None
        with self._write_lock:
            self._serial.write(frame)
            self._serial.flush()

    def _open_serial(self) -> None:
        self._serial = serial.Serial(
            port=self._port,
            baudrate=self._baud,
            timeout=self._timeout,
            write_timeout=self._write_timeout,
            rtscts=self._rtscts,
        )
        LOGGER.info(
            "Serial port opened: port=%s baud=%s rtscts=%s",
            self._port,
            self._baud,
            self._rtscts,
        )

    def _close_serial(self) -> None:
        if self._serial is not None:
            try:
                if self._serial.is_open:
                    self._serial.close()
            except Exception:
                LOGGER.exception("Error closing serial port")
            finally:
                self._serial = None

    def _reader_loop(self) -> None:
        delay = self._reconnect_min_delay
        while not self._stop_event.is_set():
            if not self.is_open():
                try:
                    self._open_serial()
                    delay = self._reconnect_min_delay
                except SerialException as exc:
                    LOGGER.warning("Serial open failed: %s", exc)
                    time.sleep(delay)
                    delay = min(delay * 2, self._reconnect_max_delay)
                    continue

            try:
                frame = self._read_frame()
                if frame and self._handler:
                    self._handler(frame)
            except SerialException as exc:
                LOGGER.warning("Serial error: %s", exc)
                self._close_serial()
                time.sleep(delay)
                delay = min(delay * 2, self._reconnect_max_delay)
            except Exception:
                LOGGER.exception("Unexpected serial read error")
                time.sleep(self._reconnect_min_delay)

    def _read_frame(self) -> Optional[bytes]:
        if self._serial is None:
            return None

        start = self._read_start_byte()
        if start is None:
            return None

        length_bytes = self._read_exact(1)
        if length_bytes is None:
            return None
        length = length_bytes[0]
        if length < MIN_MSG_LENGTH or length > MAX_MSG_LENGTH:
            LOGGER.debug("Discarding frame with invalid length=%s", length)
            return None

        payload = self._read_exact(length)
        if payload is None:
            return None

        return bytes([start, length]) + payload

    def _read_start_byte(self) -> Optional[int]:
        if self._serial is None:
            return None

        while not self._stop_event.is_set():
            chunk = self._serial.read(1)
            if not chunk:
                return None
            val = chunk[0]
            if val in START_BYTES:
                return val
        return None

    def _read_exact(self, size: int) -> Optional[bytes]:
        if self._serial is None:
            return None
        buf = bytearray()
        while len(buf) < size and not self._stop_event.is_set():
            chunk = self._serial.read(size - len(buf))
            if not chunk:
                continue
            buf.extend(chunk)
        if len(buf) != size:
            return None
        return bytes(buf)
