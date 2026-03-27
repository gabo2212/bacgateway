# Shim: re-export canonical transport from gateway.vwg_serial.
# Import this module from either location; behaviour is identical.
from gateway.vwg_serial import (  # noqa: F401
    VwgSerialTransport,
    TransportCounters,
    FrameHandler,
)
