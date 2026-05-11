from __future__ import annotations

import logging
from typing import Iterator, Optional
import time

from .nrf_bridge_proto import NrfFrame, MAGIC

LOGGER = logging.getLogger(__name__)

class NrfBridgeClient:
    def __init__(self, port: str, baudrate: int = 115200, timeout: float = 1.0):
        self.port = port
        self.baudrate = baudrate
        self.timeout = timeout
        self._serial = None
        self._buffer = bytearray()

    def connect(self) -> None:
        try:
            import serial
        except ImportError:
            raise ImportError("pyserial is required for NrfBridgeClient")
        
        self._serial = serial.Serial(self.port, self.baudrate, timeout=self.timeout)

    def disconnect(self) -> None:
        if self._serial:
            self._serial.close()
            self._serial = None

    def read_frames(self) -> Iterator[NrfFrame]:
        if not self._serial:
            return
        
        while True:
            chunk = self._serial.read(1024)
            if not chunk:
                break
            
            self._buffer.extend(chunk)
            
            while len(self._buffer) >= 12:
                magic_idx = self._buffer.find(MAGIC)
                if magic_idx == -1:
                    self._buffer.clear()
                    break
                
                if magic_idx > 0:
                    self._buffer = self._buffer[magic_idx:]
                
                if len(self._buffer) < 12:
                    break
                
                import struct
                try:
                    header = struct.unpack("<2sBBBBH", self._buffer[:8])
                except struct.error:
                    self._buffer = self._buffer[1:]
                    continue
                
                length = header[5]
                total_len = 8 + length + 4
                
                if len(self._buffer) < total_len:
                    break
                
                frame_data = bytes(self._buffer[:total_len])
                self._buffer = self._buffer[total_len:]
                
                try:
                    frame = NrfFrame.decode(frame_data)
                    if frame:
                        yield frame
                except ValueError as e:
                    LOGGER.warning(f"Frame decode error: {e}")
