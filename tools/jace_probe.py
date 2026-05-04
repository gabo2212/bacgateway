#!/usr/bin/env python3
"""
tools/jace_probe.py — Phase A3 JACE Ethernet discovery and read probe CLI.

Subcommands
-----------
Required (A2 discovery):
  ping           TCP reachability to host:port
  probe-http     HTTP response and login-page shape detection
  probe-login    Attempt login with credentials; detect cookieDigest/SCRAM
  probe-obix     Check /obix/ and /ord/ endpoint availability
  summary        Run all probes and print a discovery summary

Optional (non-blocking):
  probe-fox      TCP reachability on the Fox port (default 1911)

A3 read slice (implemented):
  read-known-points  --device-ref Device2 --points RoomTemperature ...

Credentials: NIAGARA_USER / NIAGARA_PASS (existing repo env vars).
Config:      --config configs/demo_a2.yaml  (same indirection pattern as demo_a2.yaml).
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import socket
import sys
import time
import urllib.request
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from gateway.niagara_client import (  # noqa: E402
    NiagaraClient,
    detect_login_scheme,
)
from gateway.jace_client import JaceEthernetClient  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
LOGGER = logging.getLogger("jace_probe")

_FOX_DEFAULT_PORT = 1911


# ---------------------------------------------------------------------------
# Config helpers
# ---------------------------------------------------------------------------

def _load_config(path: str) -> dict[str, Any]:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
    except FileNotFoundError:
        return {}
    return data if isinstance(data, dict) else {}


def _resolve_credentials(cfg: dict[str, Any]) -> tuple[str | None, str | None]:
    niagara = cfg.get("niagara", {}) if isinstance(cfg.get("niagara"), dict) else {}
    user_env = niagara.get("username_env", "NIAGARA_USER")
    pass_env = niagara.get("password_env", "NIAGARA_PASS")
    return os.environ.get(user_env), os.environ.get(pass_env)


def _resolve_host_port(args: argparse.Namespace, cfg: dict[str, Any]) -> tuple[str, int]:
    niagara = cfg.get("niagara", {}) if isinstance(cfg.get("niagara"), dict) else {}
    host = args.host or niagara.get("host", "")
    if not host:
        raise SystemExit("Missing JACE host. Use --host or --config with niagara.host.")
    port = args.port or 80
    return host, int(port)


# ---------------------------------------------------------------------------
# Probe functions — reuse NiagaraClient where possible
# ---------------------------------------------------------------------------

def probe_ping(host: str, port: int, timeout: float = 3.0) -> bool:
    """TCP reachability check."""
    client = JaceEthernetClient(host=host, port=port, timeout_sec=timeout)
    ok = client.ping()
    status = "REACHABLE" if ok else "UNREACHABLE"
    LOGGER.info("ping host=%s port=%s => %s", host, port, status)
    return ok


def probe_http(host: str, port: int, timeout: float = 5.0) -> dict[str, Any]:
    """HTTP response check and login-page shape detection.

    Reuses _LOGIN_MARKERS logic already present in niagara_client.
    """
    url = f"http://{host}:{port}/"
    result: dict[str, Any] = {"url": url, "status": None, "login_detected": False,
                               "scheme": None, "error": None}
    try:
        req = urllib.request.Request(url, headers={"Accept": "text/html,*/*"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read(8192).decode("utf-8", errors="replace")
            result["status"] = resp.status
            result["login_detected"] = any(
                m in body.lower() for m in ("login", "username", "cookiedigest")
            )
            result["scheme"] = detect_login_scheme(body)
    except Exception as exc:
        result["error"] = str(exc)
    LOGGER.info("probe_http %s => status=%s login=%s scheme=%s err=%s",
                url, result["status"], result["login_detected"],
                result["scheme"], result["error"])
    return result


def probe_login(host: str, port: int, username: str | None, password: str | None,
                timeout: float = 10.0) -> dict[str, Any]:
    """Attempt login; detect scheme; reuse NiagaraClient for cookieDigest/SCRAM."""
    result: dict[str, Any] = {"login_ok": False, "scheme": None, "error": None}
    if not username or not password:
        result["error"] = "Missing credentials (NIAGARA_USER / NIAGARA_PASS)"
        LOGGER.warning("probe_login: %s", result["error"])
        return result
    client = NiagaraClient(host=host if port == 80 else f"{host}:{port}",
                           scheme="http", username=username, password=password,
                           timeout_sec=timeout)
    try:
        client.login()
        result["login_ok"] = client.login_ok
        result["scheme"] = client.login_scheme
    except Exception as exc:
        result["error"] = str(exc)
    LOGGER.info("probe_login host=%s login_ok=%s scheme=%s err=%s",
                host, result["login_ok"], result["scheme"], result["error"])
    return result


def probe_obix(host: str, port: int, username: str | None, password: str | None,
               timeout: float = 10.0) -> dict[str, Any]:
    """Check /obix/ and a known /ord/ path; reuses NiagaraClient read stack."""
    result: dict[str, Any] = {"obix_root": None, "ord_probe": None, "error": None}
    niagara_host = host if port == 80 else f"{host}:{port}"
    _PROBE_ORD = ("station:|slot:/Drivers/WirelessTstatNetwork"
                  "/Device2/points/RoomTemperature/out")
    client = NiagaraClient(host=niagara_host, scheme="http",
                           username=username, password=password,
                           timeout_sec=timeout, probe_ord_path=_PROBE_ORD)
    try:
        client.login()
        result["obix_root"] = client.login_ok
        real = client.read_real(_PROBE_ORD)
        result["ord_probe"] = {"value": real.value, "unit": real.unit}
    except Exception as exc:
        result["error"] = str(exc)
    LOGGER.info("probe_obix host=%s obix_ok=%s ord=%s err=%s",
                host, result["obix_root"], result["ord_probe"], result["error"])
    return result


def probe_fox(host: str, fox_port: int = _FOX_DEFAULT_PORT, timeout: float = 3.0) -> bool:
    """TCP reachability on the Fox port."""
    try:
        with socket.create_connection((host, fox_port), timeout=timeout):
            LOGGER.info("probe_fox host=%s port=%s => REACHABLE", host, fox_port)
            return True
    except OSError:
        LOGGER.info("probe_fox host=%s port=%s => UNREACHABLE", host, fox_port)
        return False


def run_summary(host: str, port: int, fox_port: int, username: str | None,
                password: str | None, timeout: float) -> None:
    """Run all required probes and print a discovery summary."""
    print(f"\n=== JACE Discovery Summary — {host}:{port} ===")
    print(f"Timestamp : {time.strftime('%Y-%m-%dT%H:%M:%S')}")

    ping_ok = probe_ping(host, port, timeout=min(timeout, 5.0))
    print(f"\n[ping]        {'OK' if ping_ok else 'FAIL'}")

    http_r = probe_http(host, port, timeout=timeout)
    print(f"[probe-http]  status={http_r['status']}  login_detected={http_r['login_detected']}"
          f"  scheme={http_r['scheme']}  err={http_r['error']}")

    login_r = probe_login(host, port, username, password, timeout=timeout)
    print(f"[probe-login] login_ok={login_r['login_ok']}  scheme={login_r['scheme']}"
          f"  err={login_r['error']}")

    obix_r = probe_obix(host, port, username, password, timeout=timeout)
    print(f"[probe-obix]  obix_root={obix_r['obix_root']}  ord_probe={obix_r['ord_probe']}"
          f"  err={obix_r['error']}")

    fox_ok = probe_fox(host, fox_port=fox_port, timeout=min(timeout, 3.0))
    print(f"[probe-fox]   port={fox_port}  {'REACHABLE' if fox_ok else 'UNREACHABLE'}")
    print()


# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# read-known-points command implementation
# ---------------------------------------------------------------------------

def _cmd_read_known_points(
    *,
    host: str,
    port: int,
    username: str | None,
    password: str | None,
    timeout: float,
    device_ref: str,
    points: tuple[str, ...],
    output_json: bool,
) -> None:
    """Connect, read the 3 known A3 points, print results, exit non-zero on any error."""
    client = JaceEthernetClient(
        host=host,
        port=port,
        username=username,
        password=password,
        timeout_sec=timeout,
    )
    client.connect()
    try:
        results = client.read_points(device_ref, points)
    finally:
        client.close()

    any_error = any(r.quality != "ok" for r in results.values())

    if output_json:
        payload = {
            name: {
                "value": r.value,
                "timestamp": r.timestamp,
                "quality": r.quality,
                "last_error": r.last_error,
            }
            for name, r in results.items()
        }
        print(json.dumps(payload, indent=2))
    else:
        print(f"device_ref: {device_ref}")
        for name, r in results.items():
            if r.quality == "ok":
                print(f"  {name}: {r.value}  [ok]")
            else:
                print(f"  {name}: ERROR — {r.last_error}")

    if any_error:
        sys.exit(1)


# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="JACE Ethernet discovery probe — Phase A3"
    )
    parser.add_argument("--config", default="configs/demo_a2.yaml",
                        help="Path to JACE/Niagara config YAML (default: configs/demo_a2.yaml)")
    parser.add_argument("--host", help="JACE IP/hostname (overrides config)")
    parser.add_argument("--port", type=int, default=0,
                        help="HTTP port (default: 80 or from config)")
    parser.add_argument("--fox-port", type=int, default=_FOX_DEFAULT_PORT,
                        help=f"Fox port (default: {_FOX_DEFAULT_PORT})")
    parser.add_argument("--timeout", type=float, default=10.0,
                        help="Network timeout in seconds (default: 10.0)")

    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("ping", help="TCP reachability to host:port")
    sub.add_parser("probe-http", help="HTTP response and login-page shape detection")
    sub.add_parser("probe-login", help="Attempt login; detect cookieDigest/SCRAM")
    sub.add_parser("probe-obix", help="Check /obix/ and /ord/ endpoint availability")
    sub.add_parser("probe-fox", help="TCP reachability on Fox port")
    sub.add_parser("summary", help="Run all probes and print discovery summary")

    rkp = sub.add_parser(
        "read-known-points",
        help="Read 3 confirmed Device2 points via NiagaraClient (A3 read slice)",
    )
    rkp.add_argument("--device-ref", default="Device2",
                     help="Device reference key (default: Device2)")
    rkp.add_argument("--points", nargs="+",
                     default=["RoomTemperature", "OccupiedHeatingSetpoint",
                              "OccupiedCoolingSetpoint"],
                     help="Point names to read (default: all 3 confirmed A3 points)")
    rkp.add_argument("--json", action="store_true", dest="output_json",
                     help="Emit results as JSON")

    args = parser.parse_args()
    cfg = _load_config(args.config)
    host, port = _resolve_host_port(args, cfg)
    username, password = _resolve_credentials(cfg)
    fox_port = args.fox_port

    if args.command == "ping":
        ok = probe_ping(host, port, timeout=args.timeout)
        print("REACHABLE" if ok else "UNREACHABLE")

    elif args.command == "probe-http":
        r = probe_http(host, port, timeout=args.timeout)
        print(r)

    elif args.command == "probe-login":
        r = probe_login(host, port, username, password, timeout=args.timeout)
        print(r)

    elif args.command == "probe-obix":
        r = probe_obix(host, port, username, password, timeout=args.timeout)
        print(r)

    elif args.command == "probe-fox":
        ok = probe_fox(host, fox_port=fox_port, timeout=args.timeout)
        print("REACHABLE" if ok else "UNREACHABLE")

    elif args.command == "summary":
        run_summary(host, port, fox_port, username, password, timeout=args.timeout)

    elif args.command == "read-known-points":
        _cmd_read_known_points(
            host=host,
            port=port,
            username=username,
            password=password,
            timeout=args.timeout,
            device_ref=args.device_ref,
            points=tuple(args.points),
            output_json=args.output_json,
        )


if __name__ == "__main__":
    main()

