from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Any, Optional, Iterable

import yaml


@dataclass
class BacnetPointConfig:
    object_type: str
    instance: int
    object_name: str


@dataclass
class PointConfig:
    key: str
    niagara_name: Optional[str]
    point_addr: int
    comm_addr: int
    engineering_units: Optional[str]
    writable: Optional[bool]
    poll_interval: Optional[float]
    bacnet: BacnetPointConfig


@dataclass
class ThermostatConfig:
    name: str
    logical_id: Optional[str]
    comm_addr: int
    description: Optional[str]
    ieee: Optional[str]
    model: Optional[str]
    endpoint: Optional[int]
    poll_points: Optional[tuple[int, ...]]
    points: Dict[str, PointConfig]


@dataclass
class GatewayConfig:
    thermostats: Dict[str, ThermostatConfig]


def _parse_point_addr(raw_value: Any, label: str) -> int:
    if isinstance(raw_value, bool) or raw_value is None:
        raise ValueError(f"{label} must be an integer or hex string")
    if isinstance(raw_value, int):
        return raw_value
    if isinstance(raw_value, str):
        text = raw_value.strip().lower()
        if text.startswith("0x"):
            return int(text, 16)
        if all(c in "0123456789abcdef" for c in text):
            return int(text, 16)
        return int(text)
    raise ValueError(f"{label} must be an integer or hex string")


def _parse_poll_points(raw_value: Any) -> Optional[tuple[int, ...]]:
    if raw_value is None:
        return None
    if not isinstance(raw_value, Iterable) or isinstance(raw_value, (str, bytes)):
        raise ValueError("poll_points must be a list of point addresses")
    points: list[int] = []
    for idx, value in enumerate(raw_value, start=1):
        points.append(_parse_point_addr(value, f"poll_points[{idx}]"))
    return tuple(points)


def _parse_optional_float(raw_value: Any, label: str) -> Optional[float]:
    if raw_value is None:
        return None
    try:
        return float(raw_value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be a number") from exc


def _load_points_for_thermostat(t_name: str, raw: Dict[str, Any]) -> ThermostatConfig:
    zigbee = raw.get("zigbee", {})
    endpoint = zigbee.get("endpoint")

    comm_addr_raw = raw.get("comm_addr", raw.get("comm_address"))
    if comm_addr_raw is None:
        raise ValueError(f"thermostat {t_name} must define comm_addr")
    comm_addr = _parse_point_addr(comm_addr_raw, f"thermostat {t_name} comm_addr")

    logical_id = raw.get("logical_id")
    description = raw.get("description")
    poll_points = _parse_poll_points(raw.get("poll_points"))

    points_raw = raw.get("points", {})
    if not isinstance(points_raw, dict):
        raise ValueError(f"thermostat {t_name} points must be a mapping")

    points: Dict[str, PointConfig] = {}
    for key, pdata in points_raw.items():
        if not isinstance(pdata, dict):
            raise ValueError(f"point {t_name}.{key} must be a mapping")

        bac = pdata.get("bacnet")
        if not isinstance(bac, dict):
            raise ValueError(f"point {t_name}.{key} missing bacnet mapping")

        addr_raw = pdata.get("point_addr_hex", pdata.get("viconics_address_hex"))
        if addr_raw is None:
            addr_raw = pdata.get("point_addr")
        if addr_raw is None:
            raise ValueError(f"point {t_name}.{key} missing point_addr_hex")
        point_addr = _parse_point_addr(addr_raw, f"{t_name}.{key}.point_addr_hex")

        poll_interval = _parse_optional_float(
            pdata.get("poll_interval_seconds"),
            f"{t_name}.{key}.poll_interval_seconds",
        )

        point = PointConfig(
            key=key,
            niagara_name=pdata.get("niagara_name"),
            point_addr=point_addr,
            comm_addr=comm_addr,
            engineering_units=pdata.get("engineering_units"),
            writable=pdata.get("writable"),
            poll_interval=poll_interval,
            bacnet=BacnetPointConfig(
                object_type=bac["object_type"],
                instance=int(bac["instance"]),
                object_name=bac["object_name"],
            ),
        )
        points[key] = point

    return ThermostatConfig(
        name=t_name,
        logical_id=logical_id,
        comm_addr=comm_addr,
        description=description,
        ieee=raw.get("ieee"),
        model=raw.get("model"),
        endpoint=endpoint,
        poll_points=poll_points,
        points=points,
    )


def load_config(path: str | Path) -> GatewayConfig:
    path = Path(path)
    with path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f)

    tstats: Dict[str, ThermostatConfig] = {}
    for name, tdata in data.get("thermostats", {}).items():
        tstats[name] = _load_points_for_thermostat(name, tdata)

    return GatewayConfig(thermostats=tstats)


if __name__ == "__main__":
    cfg = load_config("points.yaml")
    print("Loaded thermostats:")
    for t_name, t in cfg.thermostats.items():
        print(
            f"  {t_name}: comm_addr={t.comm_addr}, model={t.model}, "
            f"ieee={t.ieee}, endpoint={t.endpoint}"
        )
        for p_key, p in t.points.items():
            print(
                f"    {p_key}: units={p.engineering_units}, "
                f"BACnet {p.bacnet.object_type} {p.bacnet.instance}, "
                f"addr 0x{p.point_addr:04x}"
            )
