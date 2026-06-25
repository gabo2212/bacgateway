import asyncio
import importlib.util
import subprocess
import sys

import pytest

from gateway.bacnet_server import PRESENT_VALUE, PointRegistry, create_app_from_config
from gateway.identity_registry import IdentityRegistry
from gateway.model import load_config
from gateway.ota.events import OtaDeviceIdentity, OtaPointEvent
from gateway.radio.ota_registry_adapter import OtaRegistryAdapter


def test_nrf_bacnet_mirror_help_exits_zero() -> None:
    result = subprocess.run(
        [sys.executable, "gateway/nrf_bacnet_mirror.py", "--help"],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    assert "--config" in result.stdout
    assert "--port" in result.stdout
    assert "--channel" in result.stdout


def test_bacnet_runtime_fails_loudly_without_bacpypes3() -> None:
    if importlib.util.find_spec("bacpypes3") is not None:
        pytest.skip("bacpypes3 is installed; missing-dependency guard not active")

    cfg = load_config("points.yaml")
    registry = PointRegistry(cfg)

    async def _build_app() -> None:
        create_app_from_config(cfg, registry, radio=None)

    with pytest.raises(ModuleNotFoundError, match="bacpypes3 is required"):
        asyncio.run(_build_app())


@pytest.mark.skipif(
    importlib.util.find_spec("bacpypes3") is None,
    reason="bacpypes3 is required to exercise the real BACnet app/read path",
)
def test_synthetic_ota_event_updates_shared_registry_and_bacnet_read_source() -> None:
    from bacpypes3.apdu import ReadPropertyRequest
    from bacpypes3.primitivedata import Real

    cfg = load_config("points.yaml")
    registry = PointRegistry(cfg)
    adapter = OtaRegistryAdapter(
        registry,
        identity_registry=IdentityRegistry.from_config(cfg),
    )

    async def _exercise_chain() -> None:
        app = create_app_from_config(cfg, registry, radio=None)
        responses = []

        async def _capture_response(apdu):
            responses.append(apdu)

        app.response = _capture_response
        try:
            event = OtaPointEvent(
                identity=OtaDeviceIdentity(short_addr=0x143E),
                prefix=0x08,
                code=0x4B,
                canonical_point="occupied_cool_setpoint",
                kind="analog_x10",
                value=69.0,
            )

            assert adapter.apply_event(event) is True
            runtime = registry.get_by_bacnet("analogValue", 1005)
            assert runtime is not None
            assert runtime.value == 69.0
            assert app.points is registry
            assert app.points.get_by_bacnet("analogValue", 1005) is runtime
            assert app.points.get_by_bacnet("analogValue", 1005).value == 69.0

            await app.do_ReadPropertyRequest(
                ReadPropertyRequest(
                    propertyIdentifier=PRESENT_VALUE,
                    objectIdentifier=("analogValue", 1005),
                    propertyArrayIndex=None,
                )
            )

            assert responses
            assert float(responses[0].propertyValue.cast_out(Real)) == 69.0
            bacnet_obj = app.get_object_id(responses[0].objectIdentifier)
            assert bacnet_obj is not None
            assert float(bacnet_obj.presentValue) == 69.0
        finally:
            app.close()

    asyncio.run(_exercise_chain())
