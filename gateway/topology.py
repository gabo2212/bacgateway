from __future__ import annotations

# Requires PyYAML (import yaml); ensure it is available in the environment.

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, Tuple
import ipaddress

import yaml

BACNET_DEVICE_ID_MIN = 0
BACNET_DEVICE_ID_MAX = 4_194_303
BACNET_NETWORK_MIN = 0
BACNET_NETWORK_MAX = 65_535


@dataclass(frozen=True)
class ThermostatTopo:
    name: str
    model: str
    short_mac: int
    ieee: str | None
    bacnet_network: int
    bacnet_device_id: int


@dataclass(frozen=True)
class GatewayTopo:
    name: str
    ip: str
    pan_id: int
    channel: int
    gateway_device_id: int
    pan_bacnet_network: int
    thermostats: Tuple[ThermostatTopo, ...]


def gateway_device_id_from_ip(ip: str) -> int:
    ip_obj = _parse_ipv4(ip, "gateway ip")
    last_octet = ip_obj.packed[-1]
    device_id = 400000 + last_octet
    _validate_device_id(device_id, f"gateway ip {ip_obj}")
    return device_id


def bacnet_network_number_from_pan(pan_id: int) -> int:
    _require_int(pan_id, "pan_id")
    network_number = 4000 + pan_id
    _validate_network_number(network_number, f"pan_id {pan_id}")
    return network_number


def thermostat_device_id(network_number: int, short_mac: int) -> int:
    _validate_network_number(network_number, f"network_number {network_number}")
    _require_int(short_mac, "short_mac")
    if short_mac < 0:
        raise ValueError("short_mac must be non-negative")
    device_id = (network_number * 100) + short_mac
    _validate_device_id(device_id, f"short_mac {short_mac} on network {network_number}")
    return device_id


def load_topology(path: str | Path) -> Tuple[GatewayTopo, ...]:
    path = Path(path)
    with path.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle)

    if not isinstance(data, dict):
        raise ValueError("Topology file must be a mapping at the top level")

    gateways_raw = data.get("gateways")
    if not isinstance(gateways_raw, list):
        raise ValueError("Topology file must contain a list of gateways")

    device_id_sources: Dict[int, str] = {}
    gateways: list[GatewayTopo] = []

    for idx, raw in enumerate(gateways_raw, start=1):
        if not isinstance(raw, dict):
            raise ValueError(f"Gateway entry #{idx} must be a mapping")

        name = _require_str(raw.get("name"), f"gateway #{idx} name")
        ip_raw = _require_str(raw.get("ip"), f"gateway {name} ip")
        ip_obj = _parse_ipv4(ip_raw, f"gateway {name} ip")
        ip_str = str(ip_obj)

        pan_id = _require_int(raw.get("pan_id"), f"gateway {name} pan_id")
        channel = _require_int(raw.get("channel"), f"gateway {name} channel")

        gateway_device_id = gateway_device_id_from_ip(ip_str)
        _register_device_id(
            gateway_device_id, f"gateway {name}", device_id_sources
        )

        pan_network = bacnet_network_number_from_pan(pan_id)

        thermostats_raw = raw.get("thermostats", [])
        if not isinstance(thermostats_raw, list):
            raise ValueError(f"gateway {name} thermostats must be a list")

        thermostats: list[ThermostatTopo] = []
        for t_idx, t_raw in enumerate(thermostats_raw, start=1):
            if not isinstance(t_raw, dict):
                raise ValueError(
                    f"gateway {name} thermostat #{t_idx} must be a mapping"
                )

            t_name = _require_str(
                t_raw.get("name"), f"gateway {name} thermostat #{t_idx} name"
            )
            model = _require_str(
                t_raw.get("model"), f"thermostat {t_name} model"
            )
            short_mac = _require_int(
                t_raw.get("short_mac"), f"thermostat {t_name} short_mac"
            )
            if short_mac < 0:
                raise ValueError(f"thermostat {t_name} short_mac must be non-negative")

            ieee = t_raw.get("ieee")
            if ieee is not None and not isinstance(ieee, str):
                raise ValueError(f"thermostat {t_name} ieee must be a string or null")

            device_id = thermostat_device_id(pan_network, short_mac)
            _register_device_id(
                device_id,
                f"thermostat {t_name} (gateway {name})",
                device_id_sources,
            )

            thermostats.append(
                ThermostatTopo(
                    name=t_name,
                    model=model,
                    short_mac=short_mac,
                    ieee=ieee,
                    bacnet_network=pan_network,
                    bacnet_device_id=device_id,
                )
            )

        gateways.append(
            GatewayTopo(
                name=name,
                ip=ip_str,
                pan_id=pan_id,
                channel=channel,
                gateway_device_id=gateway_device_id,
                pan_bacnet_network=pan_network,
                thermostats=tuple(thermostats),
            )
        )

    return tuple(gateways)


def _require_str(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{label} must be a non-empty string")
    return value


def _require_int(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{label} must be an integer")
    return value


def _parse_ipv4(ip: str, label: str) -> ipaddress.IPv4Address:
    try:
        ip_obj = ipaddress.ip_address(ip)
    except ValueError as exc:
        raise ValueError(f"{label} must be a valid IPv4 address") from exc

    if not isinstance(ip_obj, ipaddress.IPv4Address):
        raise ValueError(f"{label} must be an IPv4 address")
    return ip_obj


def _validate_device_id(device_id: int, context: str) -> None:
    if not (BACNET_DEVICE_ID_MIN <= device_id <= BACNET_DEVICE_ID_MAX):
        raise ValueError(
            f"{context} device id {device_id} is out of range "
            f"({BACNET_DEVICE_ID_MIN}..{BACNET_DEVICE_ID_MAX})"
        )


def _validate_network_number(network_number: int, context: str) -> None:
    if not (BACNET_NETWORK_MIN <= network_number <= BACNET_NETWORK_MAX):
        raise ValueError(
            f"{context} network number {network_number} is out of range "
            f"({BACNET_NETWORK_MIN}..{BACNET_NETWORK_MAX})"
        )


def _register_device_id(
    device_id: int, label: str, registry: Dict[int, str]
) -> None:
    if device_id in registry:
        existing = registry[device_id]
        raise ValueError(
            f"Duplicate BACnet device id {device_id} for {label} (already used by {existing})"
        )
    registry[device_id] = label


def _print_topology(gateways: Iterable[GatewayTopo]) -> None:
    for gateway in gateways:
        print(
            f"Gateway {gateway.name}: device_id={gateway.gateway_device_id} "
            f"pan_network={gateway.pan_bacnet_network}"
        )
        for thermostat in gateway.thermostats:
            print(
                f"  Thermostat {thermostat.name}: device_id={thermostat.bacnet_device_id}"
            )


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Load and validate topology YAML.")
    parser.add_argument(
        "path",
        nargs="?",
        default="spec/topology.yaml",
        help="Path to topology YAML (default: spec/topology.yaml)",
    )
    args = parser.parse_args()

    topo = load_topology(args.path)
    _print_topology(topo)
