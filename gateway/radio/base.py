from __future__ import annotations

from typing import Callable, Protocol
import threading
import time

FrameHandler = Callable[[bytes], None]


class FrameTransport(Protocol):
    def set_frame_handler(self, handler: FrameHandler) -> None:
        ...

    def open(self) -> None:
        ...

    def close(self) -> None:
        ...

    def send_frame(self, frame: bytes) -> None:
        ...

    def is_open(self) -> bool:
        ...


class RateLimiter:
    def __init__(self, min_interval: float) -> None:
        self._min_interval = max(0.0, min_interval)
        self._last: dict[str, float] = {}
        self._lock = threading.Lock()

    def allow(self, key: str) -> bool:
        if self._min_interval <= 0:
            return True
        now = time.monotonic()
        with self._lock:
            last = self._last.get(key)
            if last is None or (now - last) >= self._min_interval:
                self._last[key] = now
                return True
        return False
