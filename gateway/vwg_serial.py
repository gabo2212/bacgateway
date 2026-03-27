"""Canonical VWG serial transport (gateway-level).

Wraps pyserial into a byte-stream reader loop with:
- Transport-level counters (frames_tx, frames_rx, crc_errors, length_errors)
- last_tx_frame / last_rx_frame for probe diagnostics
- CRC validation at the transport layer (bad-CRC frames are counted and discarded)
- Platform-aware RTS/CTS default (True on non-Windows, False on Windows)
"""
from __future__ import annotations

import logging
import sys
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Optional

try:
    from serial import SerialException
except ImportError:
    # pyserial not installed; SerialException only needed when port is actually opened.
    SerialException = OSError  # type: ignore[misc,assignment]

LOGGER = logging.getLogger(__name__)

FrameHandler = Callable[[bytes], None]

START_BYTES = (0x3C, 0x40)
MIN_MSG_LENGTH = 6
MAX_MSG_LENGTH = 255

# True on Linux/macOS, False on Windows
_RTSCTS_DEFAULT: bool = sys.platform != "win32"


# ---------------------------------------------------------------------------
# Counters
# ---------------------------------------------------------------------------


@dataclass
class TransportCounters:
    frames_tx: int = 0
    frames_rx: int = 0
    crc_errors: int = 0
    length_errors: int = 0


# ---------------------------------------------------------------------------
# Transport
# ---------------------------------------------------------------------------


class VwgSerialTransport:
    def __init__(
        self,
        port: str,
        baud: int = 57600,
        rtscts: bool = _RTSCTS_DEFAULT,
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
        self._serial: Optional[object] = None  # serial.Serial, imported lazily
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._write_lock = threading.Lock()

        self.counters = TransportCounters()
        self.last_tx_frame: Optional[bytes] = None
        self.last_rx_frame: Optional[bytes] = None

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
        self.counters.frames_tx += 1
        self.last_tx_frame = frame

    def _open_serial(self) -> None:
        import serial  # lazy: pyserial only needed when a real port is opened
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
        """Read one complete frame from the serial stream.

        Skips garbage bytes until a valid start byte is found.
        Returns None if the stop event fires or a protocol violation is found.
        Increments length_errors / crc_errors and discards framing violations.
        """
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
            self.counters.length_errors += 1
            return None

        rest = self._read_exact(length)
        if rest is None:
            return None

        frame = bytes([start, length]) + rest

        # CRC validation: sum(frame[2:-1]) & 0xFF must equal frame[-1]
        crc_computed = sum(frame[2:-1]) & 0xFF
        if crc_computed != frame[-1]:
            LOGGER.debug(
                "Discarding frame with bad CRC: computed=0x%02X got=0x%02X",
                crc_computed,
                frame[-1],
            )
            self.counters.crc_errors += 1
            return None

        self.counters.frames_rx += 1
        self.last_rx_frame = frame
        return frame

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

