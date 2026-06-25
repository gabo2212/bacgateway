import logging
from pathlib import Path

from gateway.bacnet_server import PointRegistry
from gateway.identity_registry import IdentityRegistry
from gateway.model import (
    BacnetPointConfig,
    GatewayConfig,
    PointConfig,
    ThermostatConfig,
)
from gateway.ota.events import (
    OtaAckEvent,
    OtaDeviceIdentity,
    OtaPointEvent,
    OtaUnknownEvent,
)
from gateway.radio.ota_registry_adapter import OtaRegistryAdapter


def _point(
    *,
    key: str,
    niagara_name: str,
    point_addr: int,
    comm_addr: int,
    instance: int | None = None,
) -> PointConfig:
    return PointConfig(
        key=key,
        niagara_name=niagara_name,
        point_addr=point_addr,
        comm_addr=comm_addr,
        engineering_units="fahrenheit",
        writable=True,
        poll_interval=None,
        bacnet=BacnetPointConfig(
            object_type="analogValue",
            instance=instance if instance is not None else point_addr,
            object_name=niagara_name,
        ),
    )


def _thermostat(
    name: str,
    *,
    comm_addr: int,
    ieee: str | None,
    points: dict[str, PointConfig],
) -> ThermostatConfig:
    return ThermostatConfig(
        name=name,
        logical_id=name,
        comm_addr=comm_addr,
        description=None,
        ieee=ieee,
        model="VT76xx",
        endpoint=None,
        poll_points=None,
        points=points,
    )


def _config(*, include_heat: bool = True, two_thermostats: bool = False) -> GatewayConfig:
    ac1_points = {
        "occ_cool_setpoint": _point(
            key="occ_cool_setpoint",
            niagara_name="OccupiedCoolingSetpoint",
            point_addr=0x1005,
            comm_addr=10,
        )
    }
    if include_heat:
        ac1_points["occ_heat_setpoint"] = _point(
            key="occ_heat_setpoint",
            niagara_name="OccupiedHeatingSetpoint",
            point_addr=0x1006,
            comm_addr=10,
        )

    thermostats = {
        "AC1": _thermostat(
            "AC1",
            comm_addr=10,
            ieee="1d:35:08:04:32:20:31:04",
            points=ac1_points,
        )
    }
    if two_thermostats:
        thermostats["AC2"] = _thermostat(
            "AC2",
            comm_addr=11,
            ieee="1d:35:08:02:07:43:61:04",
            points={
                "occupied_cool_setpoint": _point(
                    key="occupied_cool_setpoint",
                    niagara_name="OccupiedCoolingSetpoint",
                    point_addr=0x1005,
                    comm_addr=11,
                    instance=2005,
                )
            },
        )
    return GatewayConfig(thermostats=thermostats)


def _point_event(
    canonical_point: str,
    value: float,
    *,
    eui64: str | None = "1d:35:08:04:32:20:31:04",
    short_addr: int | None = 0x143E,
) -> OtaPointEvent:
    return OtaPointEvent(
        identity=OtaDeviceIdentity(eui64=eui64, short_addr=short_addr),
        prefix=0x08,
        code=0x4B,
        canonical_point=canonical_point,
        kind="analog_x10",
        value=value,
    )


def test_cool_setpoint_event_updates_configured_bacnet_point() -> None:
    cfg = _config()
    registry = PointRegistry(cfg)
    adapter = OtaRegistryAdapter(
        registry,
        identity_registry=IdentityRegistry.from_config(cfg),
    )

    updated = adapter.apply_event(
        _point_event("occupied_cool_setpoint", 69.0)
    )

    runtime = registry.get_by_bacnet("analogValue", 0x1005)
    assert updated is True
    assert runtime is not None
    assert runtime.value == 69.0
    assert runtime.quality == "good"


def test_heat_setpoint_event_updates_when_configured() -> None:
    cfg = _config(include_heat=True)
    registry = PointRegistry(cfg)
    adapter = OtaRegistryAdapter(
        registry,
        identity_registry=IdentityRegistry.from_config(cfg),
    )

    updated = adapter.apply_event(
        _point_event("occupied_heat_setpoint", 72.0)
    )

    runtime = registry.get_by_bacnet("analogValue", 0x1006)
    assert updated is True
    assert runtime is not None
    assert runtime.value == 72.0


def test_heat_setpoint_event_ignored_when_not_configured(caplog) -> None:
    cfg = _config(include_heat=False)
    registry = PointRegistry(cfg)
    adapter = OtaRegistryAdapter(
        registry,
        identity_registry=IdentityRegistry.from_config(cfg),
    )

    with caplog.at_level(logging.DEBUG):
        updated = adapter.apply_event(
            _point_event("occupied_heat_setpoint", 72.0)
        )

    assert updated is False
    assert registry.get_by_bacnet("analogValue", 0x1006) is None
    assert "no configured point" in caplog.text


def test_unknown_canonical_point_is_logged_and_ignored(caplog) -> None:
    cfg = _config()
    registry = PointRegistry(cfg)
    adapter = OtaRegistryAdapter(
        registry,
        identity_registry=IdentityRegistry.from_config(cfg),
    )

    with caplog.at_level(logging.DEBUG):
        updated = adapter.apply_event(
            _point_event("roomTemp", 70.2)
        )

    cool_runtime = registry.get_by_bacnet("analogValue", 0x1005)
    assert updated is False
    assert cool_runtime is not None
    assert cool_runtime.value is None
    assert "unknown canonical_point" in caplog.text


def test_ack_event_does_not_update_present_value() -> None:
    cfg = _config()
    registry = PointRegistry(cfg)
    adapter = OtaRegistryAdapter(
        registry,
        identity_registry=IdentityRegistry.from_config(cfg),
    )

    updated = adapter.apply_event(
        OtaAckEvent(
            identity=OtaDeviceIdentity(
                eui64="1d:35:08:04:32:20:31:04",
                short_addr=0x143E,
            ),
            prefix=0x08,
            code=0x4B,
            canonical_point="occupied_cool_setpoint",
        )
    )

    runtime = registry.get_by_bacnet("analogValue", 0x1005)
    assert updated is False
    assert runtime is not None
    assert runtime.value is None


def test_unknown_event_does_not_crash_and_can_log_candidate(caplog) -> None:
    cfg = _config()
    registry = PointRegistry(cfg)
    adapter = OtaRegistryAdapter(
        registry,
        identity_registry=IdentityRegistry.from_config(cfg),
        dump_unknown_codes=True,
    )

    with caplog.at_level(logging.DEBUG):
        updated = adapter.apply_event(
            OtaUnknownEvent(
                identity=OtaDeviceIdentity(short_addr=0x143E),
                prefix=0x08,
                code=0x45,
                raw_rest_hex="084502bc",
                reason="unconfirmed_mapping",
            )
        )

    assert updated is False
    assert "candidate_ota_point" in caplog.text
    assert "confirmed" not in caplog.text


def test_short_address_fallback_updates_matching_thermostat() -> None:
    cfg = _config()
    identities = IdentityRegistry.from_config(cfg)
    identities.observe(eui64="1d:35:08:04:32:20:31:04", short_addr=0x143E)
    registry = PointRegistry(cfg)
    adapter = OtaRegistryAdapter(registry, identity_registry=identities)

    updated = adapter.apply_event(
        _point_event(
            "occupied_cool_setpoint",
            69.0,
            eui64=None,
            short_addr=0x143E,
        )
    )

    runtime = registry.get_by_address(10, 0x1005)
    assert updated is True
    assert runtime is not None
    assert runtime.value == 69.0


def test_eui64_identity_is_preferred_over_short_address() -> None:
    cfg = _config(two_thermostats=True)
    identities = IdentityRegistry.from_config(cfg)
    identities.observe(eui64="1d:35:08:04:32:20:31:04", short_addr=0x0001)
    identities.observe(eui64="1d:35:08:02:07:43:61:04", short_addr=0x143E)
    registry = PointRegistry(cfg)
    adapter = OtaRegistryAdapter(registry, identity_registry=identities)

    updated = adapter.apply_event(
        _point_event(
            "occupied_cool_setpoint",
            69.0,
            eui64="1d:35:08:04:32:20:31:04",
            short_addr=0x143E,
        )
    )

    ac1_runtime = registry.get_by_address(10, 0x1005)
    ac2_runtime = registry.get_by_address(11, 0x1005)
    assert updated is True
    assert ac1_runtime is not None
    assert ac2_runtime is not None
    assert ac1_runtime.value == 69.0
    assert ac2_runtime.value is None


def test_bacnet_server_does_not_import_ota_parser_modules() -> None:
    source = Path("gateway/bacnet_server.py").read_text(encoding="utf-8")

    assert "gateway.ota" not in source
    assert "from .ota" not in source
    assert "from gateway.radio.live_adapter" not in source
