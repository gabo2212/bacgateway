import logging

from gateway.identity_registry import IdentityRegistry
from gateway.model import (
    BacnetPointConfig,
    GatewayConfig,
    PointConfig,
    ThermostatConfig,
)


def _point(key: str, niagara_name: str, point_addr: int, comm_addr: int) -> PointConfig:
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
            instance=point_addr,
            object_name=niagara_name,
        ),
    )


def _config() -> GatewayConfig:
    return GatewayConfig(
        thermostats={
            "AC1": ThermostatConfig(
                name="AC1",
                logical_id="AC1_DEVICE2",
                comm_addr=10,
                description=None,
                ieee="1d:35:08:04:32:20:31:04",
                model="VT76xx",
                endpoint=None,
                poll_points=None,
                points={
                    "occ_cool_setpoint": _point(
                        "occ_cool_setpoint",
                        "OccupiedCoolingSetpoint",
                        0x1005,
                        10,
                    )
                },
            )
        }
    )


def test_lookup_by_eui64_from_config() -> None:
    registry = IdentityRegistry.from_config(_config())

    record = registry.lookup_by_eui64("1D35080432203104")

    assert record is not None
    assert record.eui64 == "1d:35:08:04:32:20:31:04"
    assert record.thermostat_name == "AC1"
    assert record.comm_addr == 10


def test_short_address_fallback_lookup() -> None:
    registry = IdentityRegistry.from_config(_config())
    registry.observe(eui64="1d:35:08:04:32:20:31:04", short_addr=0x143E)

    record = registry.lookup(short_addr=0x143E)

    assert record is not None
    assert record.eui64 == "1d:35:08:04:32:20:31:04"
    assert record.comm_addr == 10


def test_eui64_lookup_is_preferred_over_short_address() -> None:
    registry = IdentityRegistry()
    registry.register_thermostat(
        thermostat_name="AC1",
        comm_addr=10,
        eui64="1d:35:08:04:32:20:31:04",
        short_addr=0x0001,
    )
    registry.register_thermostat(
        thermostat_name="AC2",
        comm_addr=11,
        eui64="1d:35:08:02:07:43:61:04",
        short_addr=0x143E,
    )

    record = registry.lookup(
        eui64="1d:35:08:04:32:20:31:04",
        short_addr=0x143E,
    )

    assert record is not None
    assert record.thermostat_name == "AC1"
    assert record.comm_addr == 10


def test_short_address_change_updates_cache_and_logs(caplog) -> None:
    registry = IdentityRegistry.from_config(_config())
    registry.observe(eui64="1d:35:08:04:32:20:31:04", short_addr=0x0001)

    with caplog.at_level(logging.INFO):
        record = registry.observe(
            eui64="1d:35:08:04:32:20:31:04",
            short_addr=0x143E,
        )

    assert record is not None
    assert record.current_short_addr == 0x143E
    assert registry.lookup_by_short(0x0001) is None
    assert registry.lookup_by_short(0x143E) is record
    assert "short address changed" in caplog.text
