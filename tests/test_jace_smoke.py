"""
tests/test_jace_smoke.py — Network-free smoke tests for Phase A3 JACE read slice.

Tests covered:
  1. JacePointReadResult and JaceDiscoveryRecord: instantiation and field access.
  2. JaceStubClient: satisfies ABC contract, returns expected placeholder shapes.
  3. JaceEthernetClient: constructor wiring, ping() is mockable and network-free,
     list_devices() raises NotImplementedError, read_points() is implemented.
  4. JaceEthernetClient.read_points(): ORD path mapping, success/error results,
     unsupported point name rejection, RuntimeError without connect.
  5. CLI: --help and read-known-points subcommand no longer says "pending".
  6. Import smoke: all public names importable.
"""
from __future__ import annotations

import subprocess
import sys
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]

# ---------------------------------------------------------------------------
# Import smoke (test 5) — intentionally at module level so any import failure
# fails the entire module rather than just one test.
# ---------------------------------------------------------------------------

from gateway.jace_types import JaceDiscoveryRecord, JacePointReadResult  # noqa: E402
from gateway.jace_client import JaceClient, JaceEthernetClient, JaceStubClient  # noqa: E402


# ---------------------------------------------------------------------------
# 1. Type dataclass tests
# ---------------------------------------------------------------------------


class TestJacePointReadResult(unittest.TestCase):
    def test_instantiation_all_fields(self) -> None:
        r = JacePointReadResult(value=72.5, timestamp=1.0, quality="ok", last_error=None)
        self.assertEqual(r.value, 72.5)
        self.assertEqual(r.quality, "ok")
        self.assertIsNone(r.last_error)

    def test_instantiation_error_state(self) -> None:
        r = JacePointReadResult(value=None, timestamp=2.0, quality="error",
                                last_error="connection refused")
        self.assertIsNone(r.value)
        self.assertEqual(r.quality, "error")
        self.assertEqual(r.last_error, "connection refused")

    def test_frozen(self) -> None:
        r = JacePointReadResult(value=0.0, timestamp=0.0, quality="unknown", last_error=None)
        with self.assertRaises((AttributeError, TypeError)):
            r.value = 1.0  # type: ignore[misc]


class TestJaceDiscoveryRecord(unittest.TestCase):
    def test_instantiation(self) -> None:
        rec = JaceDiscoveryRecord(
            host="192.168.15.12",
            port=80,
            protocol_hint="cookieDigest",
            firmware_version=None,
            model=None,
            device_refs=("Device2",),
            timestamp=time.monotonic(),
        )
        self.assertEqual(rec.host, "192.168.15.12")
        self.assertEqual(rec.device_refs, ("Device2",))
        self.assertIsNone(rec.firmware_version)

    def test_frozen(self) -> None:
        rec = JaceDiscoveryRecord(
            host="h", port=80, protocol_hint=None,
            firmware_version=None, model=None,
            device_refs=(), timestamp=0.0,
        )
        with self.assertRaises((AttributeError, TypeError)):
            rec.host = "other"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# 2. JaceStubClient — ABC satisfaction and placeholder shapes
# ---------------------------------------------------------------------------


class TestJaceStubClient(unittest.TestCase):
    def setUp(self) -> None:
        self.client = JaceStubClient()

    def test_is_jace_client(self) -> None:
        self.assertIsInstance(self.client, JaceClient)

    def test_connect_close_no_error(self) -> None:
        self.client.connect()
        self.client.close()

    def test_ping_returns_true(self) -> None:
        self.assertTrue(self.client.ping())

    def test_list_devices_returns_list(self) -> None:
        devices = self.client.list_devices()
        self.assertIsInstance(devices, list)
        self.assertEqual(len(devices), 1)
        self.assertIsInstance(devices[0], JaceDiscoveryRecord)
        self.assertIn("Device2", devices[0].device_refs)

    def test_read_points_returns_dict(self) -> None:
        pts = ("RoomTemperature", "OccupiedHeatingSetpoint")
        result = self.client.read_points("Device2", pts)
        self.assertIsInstance(result, dict)
        self.assertIn("RoomTemperature", result)
        self.assertIsInstance(result["RoomTemperature"], JacePointReadResult)

    def test_read_points_keys_match_input(self) -> None:
        pts = ("RoomTemperature", "OccupiedHeatingSetpoint", "OccupiedCoolingSetpoint")
        result = self.client.read_points("Device2", pts)
        self.assertEqual(set(result.keys()), set(pts))


# ---------------------------------------------------------------------------
# 3. JaceEthernetClient — constructor, ping (mocked), stubs
# ---------------------------------------------------------------------------


class TestJaceEthernetClient(unittest.TestCase):
    def _make_client(self, **kwargs) -> JaceEthernetClient:
        defaults = dict(host="192.168.15.12", port=80,
                        username="admin", password="secret", timeout_sec=5.0)
        defaults.update(kwargs)
        return JaceEthernetClient(**defaults)

    def test_is_jace_client(self) -> None:
        self.assertIsInstance(self._make_client(), JaceClient)

    def test_constructor_stores_fields(self) -> None:
        c = self._make_client()
        self.assertEqual(c.host, "192.168.15.12")
        self.assertEqual(c.port, 80)
        self.assertEqual(c.timeout_sec, 5.0)

    def test_ping_returns_true_when_socket_succeeds(self) -> None:
        c = self._make_client()
        mock_conn = MagicMock()
        mock_conn.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn.__exit__ = MagicMock(return_value=False)
        with patch("socket.create_connection", return_value=mock_conn) as mock_sc:
            result = c.ping()
        self.assertTrue(result)
        mock_sc.assert_called_once_with(("192.168.15.12", 80), timeout=3.0)

    def test_ping_returns_false_on_oserror(self) -> None:
        c = self._make_client()
        with patch("socket.create_connection", side_effect=OSError("refused")):
            result = c.ping()
        self.assertFalse(result)

    def test_list_devices_raises_not_implemented(self) -> None:
        c = self._make_client()
        with self.assertRaises(NotImplementedError):
            c.list_devices()

    def test_read_points_raises_without_connect(self) -> None:
        """read_points() must raise RuntimeError if connect() was never called."""
        c = self._make_client()
        with self.assertRaises(RuntimeError, msg="should require connect() first"):
            c.read_points("Device2", ("RoomTemperature",))

    def test_close_safe_without_connect(self) -> None:
        c = self._make_client()
        c.close()  # must not raise


# ---------------------------------------------------------------------------
# 4. JaceEthernetClient.read_points() — real A3 implementation
# ---------------------------------------------------------------------------

_ORD_PREFIX = "station:|slot:/Drivers/WirelessTstatNetwork"
_KNOWN_POINTS = ("RoomTemperature", "OccupiedHeatingSetpoint", "OccupiedCoolingSetpoint")


def _make_connected_client(**kwargs) -> JaceEthernetClient:
    """Return a JaceEthernetClient whose _niagara is a MagicMock (no network)."""
    defaults = dict(host="192.168.15.12", port=80,
                    username="admin", password="secret", timeout_sec=5.0)
    defaults.update(kwargs)
    c = JaceEthernetClient(**defaults)
    mock_niagara = MagicMock()
    mock_niagara.ensure_login.return_value = None
    c._niagara = mock_niagara  # inject without hitting the network
    return c


class TestJaceEthernetClientReadPoints(unittest.TestCase):
    def test_ord_path_mapping(self) -> None:
        """read_points() must construct the exact ORD paths from configs/demo_a2.yaml."""
        c = _make_connected_client()
        mock_real = MagicMock()
        mock_real.value = 88.2
        c._niagara.read_real.return_value = mock_real

        c.read_points("Device2", ("RoomTemperature",))

        expected_ord = (
            "station:|slot:/Drivers/WirelessTstatNetwork"
            "/Device2/points/RoomTemperature/out"
        )
        c._niagara.read_real.assert_called_once_with(
            expected_ord, ensure_auth=False, allow_reauth=False
        )

    def test_ensure_login_called_with_first_ord(self) -> None:
        """ensure_login() must be called once with the first ORD path before reads."""
        c = _make_connected_client()
        mock_real = MagicMock()
        mock_real.value = 86.3
        c._niagara.read_real.return_value = mock_real

        c.read_points("Device2", ("OccupiedHeatingSetpoint",))

        expected_ord = (
            "station:|slot:/Drivers/WirelessTstatNetwork"
            "/Device2/points/OccupiedHeatingSetpoint/out"
        )
        c._niagara.ensure_login.assert_called_once_with(expected_ord)

    def test_success_maps_to_ok_result(self) -> None:
        """Successful read_real() must produce quality='ok' with the float value."""
        c = _make_connected_client()
        mock_real = MagicMock()
        mock_real.value = 88.2
        c._niagara.read_real.return_value = mock_real

        results = c.read_points("Device2", ("RoomTemperature",))

        r = results["RoomTemperature"]
        self.assertEqual(r.quality, "ok")
        self.assertAlmostEqual(r.value, 88.2)
        self.assertIsNone(r.last_error)
        self.assertIsInstance(r.timestamp, float)

    def test_niagara_error_maps_to_error_result(self) -> None:
        """An exception from read_real() must produce quality='error' with last_error set."""
        c = _make_connected_client()
        c._niagara.read_real.side_effect = RuntimeError("oBIX parse failed")

        results = c.read_points("Device2", ("RoomTemperature",))

        r = results["RoomTemperature"]
        self.assertEqual(r.quality, "error")
        self.assertIsNone(r.value)
        self.assertIn("oBIX parse failed", r.last_error)

    def test_unsupported_point_name_local_rejection(self) -> None:
        """An unsupported point name must be rejected locally without hitting Niagara."""
        c = _make_connected_client()

        results = c.read_points("Device2", ("BogusPoint",))

        r = results["BogusPoint"]
        self.assertEqual(r.quality, "error")
        self.assertIsNone(r.value)
        self.assertIn("unsupported point name", r.last_error)
        # Niagara stack must never be called for the unsupported name
        c._niagara.read_real.assert_not_called()

    def test_unsupported_name_does_not_affect_valid_reads(self) -> None:
        """Mix of valid and unsupported names: valid reads still succeed."""
        c = _make_connected_client()
        mock_real = MagicMock()
        mock_real.value = 88.3
        c._niagara.read_real.return_value = mock_real

        results = c.read_points("Device2", ("RoomTemperature", "BogusPoint"))

        self.assertEqual(results["RoomTemperature"].quality, "ok")
        self.assertEqual(results["BogusPoint"].quality, "error")
        self.assertIn("unsupported point name", results["BogusPoint"].last_error)

    def test_login_failure_marks_all_valid_points_error(self) -> None:
        """If ensure_login() raises, all valid requested points get quality='error'."""
        c = _make_connected_client()
        c._niagara.ensure_login.side_effect = ConnectionError("auth timeout")

        results = c.read_points("Device2", ("RoomTemperature", "OccupiedHeatingSetpoint"))

        for name in ("RoomTemperature", "OccupiedHeatingSetpoint"):
            self.assertEqual(results[name].quality, "error")
            self.assertIn("login failed", results[name].last_error)
        c._niagara.read_real.assert_not_called()


# ---------------------------------------------------------------------------
# 5. CLI smoke tests
# ---------------------------------------------------------------------------


class TestJaceProbeCliHelp(unittest.TestCase):
    def test_help_renders_without_error(self) -> None:
        result = subprocess.run(
            [sys.executable, str(ROOT / "tools" / "jace_probe.py"), "--help"],
            capture_output=True,
            text=True,
            cwd=str(ROOT),
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertIn("jace", result.stdout.lower())

    def test_read_known_points_help_not_stub(self) -> None:
        """read-known-points help must not say STUB or 'pending'."""
        result = subprocess.run(
            [sys.executable, str(ROOT / "tools" / "jace_probe.py"),
             "read-known-points", "--help"],
            capture_output=True,
            text=True,
            cwd=str(ROOT),
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        combined = (result.stdout + result.stderr).lower()
        self.assertNotIn("[stub]", combined)
        self.assertNotIn("pending", combined)


if __name__ == "__main__":
    unittest.main()

