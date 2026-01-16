from __future__ import annotations

import asyncio
import logging
from typing import Any, Iterable, Tuple

from bacpypes3.ipv4.app import NormalApplication
from bacpypes3.local.device import DeviceObject
from bacpypes3.pdu import IPv4Address
from bacpypes3.service.object import ErrorRejectAbortNack
from bacpypes3.primitivedata import ObjectIdentifier

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

REMOTE_ADDRESS = "127.0.0.1:47808"
READ_TARGETS: Iterable[Tuple[str, int, str]] = (
    ("analogInput", 1000, "Room temp"),
    ("analogValue", 1005, "Occ cool setpoint"),
    ("analogInput", 1099, "PI cooling demand"),
)


def build_client_device(device_id: int = 6001) -> DeviceObject:
    return DeviceObject(
        objectIdentifier=("device", device_id),
        objectName="BACnet Test Client",
        vendorIdentifier=36,
        modelName="Client",
        protocolVersion=1,
        protocolRevision=7,
    )


async def read_present_value(
    app: NormalApplication, objid: Tuple[str, int]
) -> Any | ErrorRejectAbortNack:
    try:
        result = await asyncio.wait_for(
            app.read_property(REMOTE_ADDRESS, ObjectIdentifier(objid), "presentValue"),
            timeout=5.0,
        )
    except asyncio.TimeoutError:
        return TimeoutError("timeout")
    return result


async def write_present_value(
    app: NormalApplication, objid: Tuple[str, int], value: float
) -> None | ErrorRejectAbortNack:
    try:
        return await asyncio.wait_for(
            app.write_property(
                REMOTE_ADDRESS, ObjectIdentifier(objid), "presentValue", value
            ),
            timeout=5.0,
        )
    except asyncio.TimeoutError:
        return TimeoutError("timeout")


def _format_value(value: Any) -> str:
    try:
        return f"{float(value):.2f}"
    except Exception:
        return repr(value)


async def main() -> None:
    local_device = build_client_device()
    local_address = IPv4Address("127.0.0.1:0")  # ephemeral UDP port
    app = NormalApplication(local_device, local_address)

    try:
        print("Reading points from device 5001 at 127.0.0.1:47808")
        for obj_type, instance, label in READ_TARGETS:
            objid = (obj_type, instance)
            result = await read_present_value(app, objid)
            if isinstance(result, (ErrorRejectAbortNack, TimeoutError)):
                print(f"{label} ({obj_type} {instance}): error {result}")
                continue
            print(f"{label} ({obj_type} {instance}) = {_format_value(result)}")

        # Write test on AV 1005
        write_objid = ("analogValue", 1005)
        new_value = 23.0
        print(f"\nWriting {new_value} to {write_objid}")
        write_result = await write_present_value(app, write_objid, new_value)
        if isinstance(write_result, (ErrorRejectAbortNack, TimeoutError)):
            print(f"Write failed: {write_result}")
        else:
            readback = await read_present_value(app, write_objid)
            if isinstance(readback, (ErrorRejectAbortNack, TimeoutError)):
                print(f"Readback failed: {readback}")
            else:
                print(
                    f"Readback ({write_objid[0]} {write_objid[1]}) = {_format_value(readback)}"
                )
    finally:
        try:
            app.close()
        except Exception:
            logger.exception("Error closing client application")


if __name__ == "__main__":
    asyncio.run(main())
