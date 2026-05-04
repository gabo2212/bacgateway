"""
gateway/jace_client.py — JACE Ethernet adapter interface and scaffold.

Phase A3: initial 3-point read slice.
  - JaceClient         : ABC that defines the read-only source-adapter contract.
  - JaceEthernetClient : Concrete A3 adapter.  Wraps NiagaraClient internally;
                         no second login/read stack.  read_points() is
                         implemented for the 3 confirmed Device2 points.
  - JaceStubClient     : Deterministic test/wiring helper.  Never touches
                         the network.

Design rule from planforlivecon:
  JaceEthernetClient is a *source adapter only*.  It must not contain BACnet
  server logic.  Stage B will replace it with the direct-radio stack while
  keeping the same interface contract.
"""
from __future__ import annotations

import abc
import logging
import socket
import time
from typing import TYPE_CHECKING

from .jace_types import JaceDiscoveryRecord, JacePointReadResult

if TYPE_CHECKING:
    from .niagara_client import NiagaraClient

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Supported point set — Phase A3 read slice
# Anchored to configs/demo_a2.yaml ord.* paths.
# Any point name not in this mapping is rejected locally with a structured
# per-point error result; it never reaches the Niagara stack.
# ---------------------------------------------------------------------------
_ORD_TEMPLATE = (
    "station:|slot:/Drivers/WirelessTstatNetwork/{device_ref}/points/{point_name}/out"
)

_SUPPORTED_POINTS: frozenset[str] = frozenset({
    "RoomTemperature",
    "OccupiedHeatingSetpoint",
    "OccupiedCoolingSetpoint",
})


class JaceClient(abc.ABC):
    """Read-only source-adapter contract for a JACE Ethernet connection.

    Concrete implementations (JaceEthernetClient for the borrowed-JACE path,
    future RadioSession-based adapter for Stage B) must satisfy this interface
    so the model and BACnet layers never depend on a specific source.
    """

    @abc.abstractmethod
    def connect(self) -> None:
        """Open the connection to the JACE.  Idempotent."""

    @abc.abstractmethod
    def close(self) -> None:
        """Release resources.  Safe to call without a prior connect()."""

    @abc.abstractmethod
    def ping(self) -> bool:
        """Return True if the JACE is reachable at the TCP level."""

    @abc.abstractmethod
    def list_devices(self) -> list[JaceDiscoveryRecord]:
        """Return discovery records for devices visible on this JACE."""

    @abc.abstractmethod
    def read_points(
        self,
        device_ref: str,
        point_names: tuple[str, ...],
    ) -> dict[str, JacePointReadResult]:
        """Read the named points from *device_ref*.

        Args:
            device_ref:  Opaque device reference (e.g. ``"Device2"``).
            point_names: Explicit list of point names to read.  The next
                         milestone uses exactly three:
                         ``("RoomTemperature", "OccupiedHeatingSetpoint",
                           "OccupiedCoolingSetpoint")``.

        Returns:
            Mapping of point name → JacePointReadResult.
        """


class JaceEthernetClient(JaceClient):
    """Concrete read-only adapter for the borrowed-JACE Ethernet path.

    Phase A3a: constructor wiring only.  ``list_devices`` and
    ``read_points`` raise NotImplementedError until A3 implementation.

    Internally wraps ``gateway.niagara_client.NiagaraClient`` — the only
    proven live path (cookieDigest login + /ord reads via read_real()).
    No duplicate login/read stack is built here.

    Confirmed facts from configs/demo_a2.yaml:
        host            192.168.15.12
        port            80
        login_scheme    cookieDigest
        device_ref      Device2
        points          RoomTemperature, OccupiedHeatingSetpoint,
                        OccupiedCoolingSetpoint
    """

    def __init__(
        self,
        host: str,
        port: int = 80,
        username: str | None = None,
        password: str | None = None,
        timeout_sec: float = 10.0,
    ) -> None:
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self.timeout_sec = float(timeout_sec)
        self._niagara: NiagaraClient | None = None  # injected at connect()

    def connect(self) -> None:
        """Instantiate and store a NiagaraClient.  Login deferred to first use."""
        from .niagara_client import NiagaraClient  # lazy: avoids hard dep at import

        self._niagara = NiagaraClient(
            host=f"{self.host}:{self.port}" if self.port != 80 else self.host,
            scheme="http",
            username=self.username,
            password=self.password,
            timeout_sec=self.timeout_sec,
        )
        logger.info("jace_client connect host=%s port=%s", self.host, self.port)

    def close(self) -> None:
        self._niagara = None
        logger.info("jace_client close host=%s", self.host)

    def ping(self) -> bool:
        """TCP-level reachability check.  Does not require login."""
        try:
            with socket.create_connection((self.host, self.port), timeout=min(3.0, self.timeout_sec)):
                return True
        except OSError:
            return False

    def list_devices(self) -> list[JaceDiscoveryRecord]:
        raise NotImplementedError("list_devices not implemented in A3 read slice")

    def read_points(
        self,
        device_ref: str,
        point_names: tuple[str, ...],
    ) -> dict[str, JacePointReadResult]:
        """Read the named points from *device_ref* via NiagaraClient.read_real().

        Only the 3 confirmed A3 points are supported:
            RoomTemperature, OccupiedHeatingSetpoint, OccupiedCoolingSetpoint

        Unsupported point names produce a structured per-point error result
        (quality="error") rather than raising an exception.

        Raises:
            RuntimeError: if connect() has not been called.
        """
        if self._niagara is None:
            raise RuntimeError(
                "JaceEthernetClient.read_points() called before connect()"
            )

        # Build ORD paths; collect unsupported names first so they never
        # reach the Niagara stack.
        ord_paths: dict[str, str] = {}
        results: dict[str, JacePointReadResult] = {}
        for name in point_names:
            if name not in _SUPPORTED_POINTS:
                results[name] = JacePointReadResult(
                    value=None,
                    timestamp=time.time(),
                    quality="error",
                    last_error=f"unsupported point name: {name}",
                )
            else:
                ord_paths[name] = _ORD_TEMPLATE.format(
                    device_ref=device_ref, point_name=name
                )

        if not ord_paths:
            return results

        # Authenticate once before the read loop, using the first ORD path
        # for context — matches the proven pattern in demo_a2_mirror.py.
        first_ord = next(iter(ord_paths.values()))
        try:
            self._niagara.ensure_login(first_ord)
        except Exception as exc:  # noqa: BLE001
            logger.warning("jace_client ensure_login failed: %s", exc)
            for name in ord_paths:
                results[name] = JacePointReadResult(
                    value=None,
                    timestamp=time.time(),
                    quality="error",
                    last_error=f"login failed: {exc}",
                )
            return results

        # Per-point reads — auth already ensured, no reauth inside the loop.
        for name, ord_path in ord_paths.items():
            ts = time.time()
            try:
                real = self._niagara.read_real(
                    ord_path, ensure_auth=False, allow_reauth=False
                )
                results[name] = JacePointReadResult(
                    value=float(real.value),
                    timestamp=ts,
                    quality="ok",
                    last_error=None,
                )
                logger.debug(
                    "jace_client read ok device_ref=%s point=%s value=%s",
                    device_ref, name, real.value,
                )
            except Exception as exc:  # noqa: BLE001
                results[name] = JacePointReadResult(
                    value=None,
                    timestamp=ts,
                    quality="error",
                    last_error=str(exc),
                )
                logger.warning(
                    "jace_client read error device_ref=%s point=%s: %s",
                    device_ref, name, exc,
                )

        return results


class JaceStubClient(JaceClient):
    """Deterministic test/wiring helper.  Never touches the network.

    Returns placeholder data and logs every call so tests can assert on
    interface shape without requiring a live JACE.
    """

    def connect(self) -> None:
        logger.debug("JaceStubClient.connect() called")

    def close(self) -> None:
        logger.debug("JaceStubClient.close() called")

    def ping(self) -> bool:
        logger.debug("JaceStubClient.ping() -> True (stub)")
        return True

    def list_devices(self) -> list[JaceDiscoveryRecord]:
        logger.debug("JaceStubClient.list_devices() -> stub record")
        return [
            JaceDiscoveryRecord(
                host="stub",
                port=80,
                protocol_hint="cookieDigest",
                firmware_version=None,
                model=None,
                device_refs=("Device2",),
                timestamp=time.monotonic(),
            )
        ]

    def read_points(
        self,
        device_ref: str,
        point_names: tuple[str, ...],
    ) -> dict[str, JacePointReadResult]:
        logger.debug(
            "JaceStubClient.read_points() device_ref=%s points=%s",
            device_ref,
            point_names,
        )
        now = time.monotonic()
        return {
            name: JacePointReadResult(
                value=None,
                timestamp=now,
                quality="unknown",
                last_error=None,
            )
            for name in point_names
        }

