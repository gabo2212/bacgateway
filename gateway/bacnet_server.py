from __future__ import annotations

import argparse
import asyncio
import logging
import queue
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Dict, Iterable, Tuple, Any, Optional

try:
    from bacpypes3.apdu import ReadPropertyACK, ReadPropertyRequest, SimpleAckPDU, WritePropertyRequest
    from bacpypes3.basetypes import EngineeringUnits, PropertyIdentifier
    from bacpypes3.constructeddata import Array
    from bacpypes3.errors import ExecutionError
    from bacpypes3.ipv4.app import NormalApplication
    from bacpypes3.local.analog import AnalogInputObject, AnalogValueObject
    from bacpypes3.local.device import DeviceObject
    from bacpypes3.pdu import IPv4Address
    from bacpypes3.primitivedata import Null, Unsigned
    _BACPYPES_IMPORT_ERROR: ModuleNotFoundError | None = None
except ModuleNotFoundError as exc:  # pragma: no cover - exercised when deps are absent
    _BACPYPES_IMPORT_ERROR = exc

    class _MissingBacpypes:
        def __init__(self, *args: object, **kwargs: object) -> None:
            raise ModuleNotFoundError(
                "bacpypes3 is required for BACnet runtime objects"
            ) from _BACPYPES_IMPORT_ERROR

    class _MissingApplication:
        def __init__(self, *args: object, **kwargs: object) -> None:
            raise ModuleNotFoundError(
                "bacpypes3 is required to start the BACnet server"
            ) from _BACPYPES_IMPORT_ERROR

    class PropertyIdentifier(str):  # type: ignore[no-redef]
        pass

    class Null:  # type: ignore[no-redef]
        pass

    class Unsigned(int):  # type: ignore[no-redef]
        pass

    class Array:  # type: ignore[no-redef]
        pass

    class ExecutionError(Exception):  # type: ignore[no-redef]
        pass

    ReadPropertyACK = _MissingBacpypes  # type: ignore[assignment]
    ReadPropertyRequest = _MissingBacpypes  # type: ignore[assignment]
    SimpleAckPDU = _MissingBacpypes  # type: ignore[assignment]
    WritePropertyRequest = _MissingBacpypes  # type: ignore[assignment]
    EngineeringUnits = _MissingBacpypes  # type: ignore[assignment]
    NormalApplication = _MissingApplication  # type: ignore[assignment]
    AnalogInputObject = _MissingBacpypes  # type: ignore[assignment]
    AnalogValueObject = _MissingBacpypes  # type: ignore[assignment]
    DeviceObject = _MissingBacpypes  # type: ignore[assignment]
    IPv4Address = _MissingBacpypes  # type: ignore[assignment]

import yaml

from .model import GatewayConfig, PointConfig, load_config
from .radio.session import RadioSession, RadioError, RadioStatusError
from .radio.vwg_serial import VwgSerialTransport
from proto import codec

logger = logging.getLogger(__name__)
PRESENT_VALUE = PropertyIdentifier("presentValue")


@dataclass
class PointRuntime:
    cfg: PointConfig
    value: Any | None = None
    timestamp: datetime | None = None
    quality: str = "unknown"
    status: int | None = None
    link_quality: int | None = None


class PointRegistry:
    def __init__(self, cfg: GatewayConfig) -> None:
        self._points: Dict[Tuple[str, int], PointRuntime] = {}
        self._points_by_addr: Dict[Tuple[int, int], PointRuntime] = {}
        self._lock = threading.Lock()

        for tstat_cfg in cfg.thermostats.values():
            for point_cfg in tstat_cfg.points.values():
                runtime = PointRuntime(cfg=point_cfg, value=None)
                for object_type in _object_type_aliases(point_cfg.bacnet.object_type):
                    key = (object_type, point_cfg.bacnet.instance)
                    self._points[key] = runtime
                self._points_by_addr[(point_cfg.comm_addr, point_cfg.point_addr)] = runtime
                logger.debug("Registered point %s -> %s", key, runtime)

    def get_by_bacnet(self, object_type: str, instance: int) -> PointRuntime | None:
        with self._lock:
            for alias in _object_type_aliases(object_type):
                runtime = self._points.get((alias, instance))
                if runtime is not None:
                    return runtime
            return None

    def get_by_address(self, comm_addr: int, point_addr: int) -> PointRuntime | None:
        with self._lock:
            return self._points_by_addr.get((comm_addr, point_addr))

    def update_from_radio(
        self,
        comm_addr: int,
        point_addr: int,
        value: Any | None,
        status: int | None,
        link_quality: int | None,
        ok: bool,
    ) -> None:
        runtime = self.get_by_address(comm_addr, point_addr)
        if runtime is None:
            logger.warning(
                "Received value for unknown point comm=%s addr=0x%04X",
                comm_addr,
                point_addr,
            )
            return
        self._update_runtime(runtime, value, status, link_quality, ok)

    def update_from_bacnet(self, runtime: PointRuntime, value: Any | None) -> None:
        self._update_runtime(runtime, value, runtime.status, runtime.link_quality, True)

    def _update_runtime(
        self,
        runtime: PointRuntime,
        value: Any | None,
        status: int | None,
        link_quality: int | None,
        ok: bool,
    ) -> None:
        with self._lock:
            runtime.value = value
            runtime.timestamp = datetime.now(timezone.utc)
            runtime.status = status
            runtime.link_quality = link_quality
            runtime.quality = "good" if ok else "bad"

    def runtimes(self) -> Iterable[PointRuntime]:
        with self._lock:
            seen: set[int] = set()
            runtimes: list[PointRuntime] = []
            for runtime in self._points.values():
                marker = id(runtime)
                if marker in seen:
                    continue
                seen.add(marker)
                runtimes.append(runtime)
            return runtimes


@dataclass
class RadioConfig:
    serial_port: str
    baud: int = 57600
    rtscts: bool = True
    poll_interval_seconds: float = 5.0
    response_timeout_seconds: float = 2.0
    retry_count: int = 3
    inter_message_delay_seconds: float = 0.01


@dataclass
class WriteRequest:
    point_cfg: PointConfig
    value: Any
    future: asyncio.Future


class RadioManager:
    def __init__(
        self,
        cfg: GatewayConfig,
        registry: PointRegistry,
        radio_cfg: RadioConfig,
        loop: asyncio.AbstractEventLoop,
    ) -> None:
        self._cfg = cfg
        self._registry = registry
        self._radio_cfg = radio_cfg
        self._loop = loop

        transport = VwgSerialTransport(
            port=radio_cfg.serial_port,
            baud=radio_cfg.baud,
            rtscts=radio_cfg.rtscts,
        )
        self._session = RadioSession(
            transport,
            response_timeout=radio_cfg.response_timeout_seconds,
            retry_count=radio_cfg.retry_count,
            inter_message_delay=radio_cfg.inter_message_delay_seconds,
        )

        self._write_timeout = (
            radio_cfg.response_timeout_seconds * max(1, radio_cfg.retry_count) + 1.0
        )

        self._stop_event = threading.Event()
        self._poll_thread = threading.Thread(target=self._poll_loop, daemon=True)
        self._write_thread = threading.Thread(target=self._write_loop, daemon=True)
        self._write_queue: queue.Queue[WriteRequest] = queue.Queue()
        self._poll_whitelist = self._build_poll_whitelist()

    def start(self) -> None:
        self._session.start()
        self._stop_event.clear()
        self._poll_thread.start()
        self._write_thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        self._session.close()
        if self._poll_thread.is_alive():
            self._poll_thread.join(timeout=2.0)
        if self._write_thread.is_alive():
            self._write_thread.join(timeout=2.0)

    async def write_point(self, point_cfg: PointConfig, value: Any) -> None:
        future: asyncio.Future = self._loop.create_future()
        self._write_queue.put(WriteRequest(point_cfg=point_cfg, value=value, future=future))
        try:
            await asyncio.wait_for(future, timeout=self._write_timeout)
        except asyncio.TimeoutError as exc:
            future.cancel()
            raise RadioError("Write timed out") from exc

    def _poll_loop(self) -> None:
        interval_fast = self._radio_cfg.poll_interval_seconds
        interval_slow = max(interval_fast * 3, 10.0)
        next_poll: dict[tuple[int, int], float] = {}

        while not self._stop_event.is_set():
            now = time.monotonic()
            for runtime in self._registry.runtimes():
                if not self._should_poll(runtime):
                    continue
                key = (runtime.cfg.comm_addr, runtime.cfg.point_addr)
                interval = runtime.cfg.poll_interval
                if interval is None:
                    interval = interval_fast if _is_fast_point(runtime.cfg) else interval_slow
                due = next_poll.get(key, 0.0)
                if now < due:
                    continue
                next_poll[key] = now + interval
                try:
                    result = self._session.read_point(
                        runtime.cfg.comm_addr,
                        runtime.cfg.point_addr,
                        runtime.cfg.niagara_name,
                    )
                    self._registry.update_from_radio(
                        runtime.cfg.comm_addr,
                        runtime.cfg.point_addr,
                        result.value,
                        result.response.frame.status,
                        result.response.frame.link_quality,
                        True,
                    )
                except RadioStatusError as err:
                    logger.warning("Read status error: %s", err)
                    self._registry.update_from_radio(
                        runtime.cfg.comm_addr,
                        runtime.cfg.point_addr,
                        runtime.value,
                        err.status_code,
                        None,
                        False,
                    )
                except RadioError as err:
                    logger.warning("Read failed: %s", err)
                    self._registry.update_from_radio(
                        runtime.cfg.comm_addr,
                        runtime.cfg.point_addr,
                        runtime.value,
                        None,
                        None,
                        False,
                    )
                except Exception:
                    logger.exception("Unexpected poll error")

            time.sleep(0.1)

    def _write_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                req = self._write_queue.get(timeout=0.2)
            except queue.Empty:
                continue
            try:
                response = self._session.write_point(
                    req.point_cfg.comm_addr,
                    req.point_cfg.point_addr,
                    req.value,
                    req.point_cfg.niagara_name,
                )
                runtime = self._registry.get_by_address(
                    req.point_cfg.comm_addr, req.point_cfg.point_addr
                )
                if runtime:
                    self._registry.update_from_radio(
                        req.point_cfg.comm_addr,
                        req.point_cfg.point_addr,
                        req.value,
                        response.frame.status,
                        response.frame.link_quality,
                        True,
                    )
                self._loop.call_soon_threadsafe(req.future.set_result, None)
            except Exception as exc:
                self._loop.call_soon_threadsafe(req.future.set_exception, exc)

    def _build_poll_whitelist(self) -> set[tuple[int, int]]:
        allowed: set[tuple[int, int]] = set()
        for tstat in self._cfg.thermostats.values():
            if tstat.poll_points:
                for addr in tstat.poll_points:
                    allowed.add((tstat.comm_addr, addr))
        return allowed

    def _should_poll(self, runtime: PointRuntime) -> bool:
        if not self._poll_whitelist:
            return True
        return (runtime.cfg.comm_addr, runtime.cfg.point_addr) in self._poll_whitelist


class GatewayApplication(NormalApplication):
    def __init__(
        self,
        device: DeviceObject,
        points: PointRegistry,
        local_address: IPv4Address,
        radio: Optional[RadioManager] = None,
    ) -> None:
        super().__init__(device, local_address)
        self.points = points
        self.radio = radio

    async def do_ReadPropertyRequest(self, apdu: ReadPropertyRequest) -> None:
        if apdu.propertyIdentifier != PRESENT_VALUE:
            await super().do_ReadPropertyRequest(apdu)
            return

        obj_id = apdu.objectIdentifier
        runtime = self.points.get_by_bacnet(obj_id[0], obj_id[1])
        if runtime is None:
            await super().do_ReadPropertyRequest(apdu)
            return

        obj = self.get_object_id(obj_id)
        property_type = obj.get_property_type(apdu.propertyIdentifier) if obj else None

        value = runtime.value
        if value is None:
            value_to_return = Null()
        elif property_type and not isinstance(value, property_type):
            value_to_return = property_type(value)
        else:
            value_to_return = value

        if obj and not isinstance(value_to_return, Null):
            obj.presentValue = value_to_return

        resp = ReadPropertyACK(
            objectIdentifier=obj_id,
            propertyIdentifier=apdu.propertyIdentifier,
            propertyArrayIndex=apdu.propertyArrayIndex,
            propertyValue=value_to_return,
            context=apdu,
        )
        await self.response(resp)

    async def do_WritePropertyRequest(self, apdu: WritePropertyRequest) -> None:
        if apdu.propertyIdentifier != PRESENT_VALUE:
            await super().do_WritePropertyRequest(apdu)
            return

        obj_id = apdu.objectIdentifier
        runtime = self.points.get_by_bacnet(obj_id[0], obj_id[1])
        if runtime is None:
            await super().do_WritePropertyRequest(apdu)
            return

        obj = self.get_object_id(obj_id)
        if not obj:
            raise ExecutionError(errorClass="object", errorCode="unknownObject")

        property_type = obj.get_property_type(apdu.propertyIdentifier)
        if property_type is None:
            raise ExecutionError(errorClass="property", errorCode="unknownProperty")

        array_index = apdu.propertyArrayIndex
        priority = apdu.priority

        if issubclass(property_type, Array):
            if array_index is None:
                pass
            elif array_index == 0:
                property_type = Unsigned
            else:
                property_type = property_type._subtype

        property_value = apdu.propertyValue.cast_out(
            property_type, null=(priority is not None)
        )

        if isinstance(property_value, Null):
            raise ExecutionError(errorClass="property", errorCode="valueOutOfRange")

        numeric_value = float(property_value)

        try:
            _validate_write(runtime.cfg, numeric_value)
        except ValueError as err:
            raise ExecutionError(errorClass="property", errorCode="valueOutOfRange") from err

        if self.radio is None:
            self.points.update_from_bacnet(runtime, numeric_value)
        else:
            try:
                await self.radio.write_point(runtime.cfg, numeric_value)
            except RadioStatusError as err:
                logger.warning("Radio write rejected: %s", err)
                raise ExecutionError(
                    errorClass="property", errorCode="writeAccessDenied"
                ) from err
            except RadioError as err:
                logger.warning("Radio write failed: %s", err)
                raise ExecutionError(
                    errorClass="device", errorCode="communicationFailure"
                ) from err

        await obj.write_property(
            apdu.propertyIdentifier, property_value, array_index, priority
        )
        await self.response(SimpleAckPDU(context=apdu))


def build_local_device(device_id: int, device_name: str) -> DeviceObject:
    return DeviceObject(
        vendorIdentifier=36,
        modelName="ViconicsWirelessGateway",
        objectIdentifier=("device", device_id),
        objectName=device_name,
        segmentationSupported="noSegmentation",
        protocolVersion=1,
        protocolRevision=7,
    )


def _object_type_aliases(object_type: str) -> tuple[str, ...]:
    normalized = str(object_type)
    kebab = _camel_to_kebab(normalized)
    compact = kebab.replace("-", "")
    return tuple(dict.fromkeys((normalized, kebab, compact)))


def _camel_to_kebab(value: str) -> str:
    chars: list[str] = []
    for idx, char in enumerate(value):
        if char.isupper() and idx > 0 and value[idx - 1] != "-":
            chars.append("-")
        chars.append(char.lower())
    return "".join(chars)


def _units_for_point(point_cfg: PointConfig) -> EngineeringUnits:
    unit = point_cfg.engineering_units
    if not unit:
        spec = codec.resolve_point_spec(point_cfg.point_addr, point_cfg.niagara_name)
        unit = spec.units if spec else None
    unit_key = (unit or "").strip().lower()
    mapping = {
        "degc": "degreesCelsius",
        "celsius": "degreesCelsius",
        "degreescelsius": "degreesCelsius",
        "degf": "degreesFahrenheit",
        "fahrenheit": "degreesFahrenheit",
        "fahrenheit degrees": "degreesFahrenheit",
        "degreesfahrenheit": "degreesFahrenheit",
        "percent": "percent",
        "percent relative humidity": "percent",
        "percentrh": "percent",
        "none": "noUnits",
        "null": "noUnits",
    }
    unit_name = mapping.get(unit_key, "noUnits")
    if unit_name == "noUnits" and unit_key:
        logger.warning(
            "Unknown engineering units %s for %s, defaulting to noUnits",
            unit_key,
            point_cfg.key,
        )
    return EngineeringUnits(unit_name)


def _build_object(runtime: PointRuntime) -> AnalogInputObject | AnalogValueObject:
    cfg = runtime.cfg
    object_id = (cfg.bacnet.object_type, cfg.bacnet.instance)
    units = _units_for_point(cfg)

    if cfg.bacnet.object_type == "analogInput":
        return AnalogInputObject(
            objectIdentifier=object_id,
            objectName=cfg.bacnet.object_name,
            presentValue=runtime.value if runtime.value is not None else 0.0,
            units=units,
        )
    if cfg.bacnet.object_type == "analogValue":
        return AnalogValueObject(
            objectIdentifier=object_id,
            objectName=cfg.bacnet.object_name,
            presentValue=runtime.value if runtime.value is not None else 0.0,
            units=units,
        )
    raise ValueError(f"Unsupported object type {cfg.bacnet.object_type}")


def create_app_from_config(
    cfg: GatewayConfig,
    registry: PointRegistry,
    radio: Optional[RadioManager],
    device_id: int = 5001,
    device_name: str = "BAC Viconics Gateway",
    bind_address: str = "0.0.0.0",
    port: int = 47808,
) -> GatewayApplication:
    try:
        asyncio.get_running_loop()
    except RuntimeError as exc:  # pragma: no cover - defensive guard
        raise RuntimeError(
            "create_app_from_config must be called with an active asyncio event loop"
        ) from exc

    device = build_local_device(device_id, device_name)

    local_address = IPv4Address(f"{bind_address}:{port}")
    app = GatewayApplication(device, registry, local_address, radio)

    for runtime in registry.runtimes():
        bacnet_obj = _build_object(runtime)
        app.add_object(bacnet_obj)

    return app


def load_radio_config(path: str) -> RadioConfig:
    with open(path, "r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    if not isinstance(data, dict):
        raise ValueError("radio config must be a mapping")

    serial_port = data.get("serial_port") or data.get("port")
    if not serial_port:
        raise ValueError("radio config missing serial_port")

    return RadioConfig(
        serial_port=serial_port,
        baud=int(data.get("baud", 57600)),
        rtscts=bool(data.get("rtscts", True)),
        poll_interval_seconds=float(data.get("poll_interval_seconds", 5.0)),
        response_timeout_seconds=float(data.get("response_timeout_seconds", 2.0)),
        retry_count=int(data.get("retry_count", 3)),
        inter_message_delay_seconds=float(data.get("inter_message_delay_seconds", 0.01)),
    )


def _validate_write(point_cfg: PointConfig, value: float) -> None:
    spec = codec.resolve_point_spec(point_cfg.point_addr, point_cfg.niagara_name)
    if spec and "W" not in spec.rw:
        raise ValueError("Point is read-only")
    if point_cfg.writable is False:
        raise ValueError("Point is configured read-only")
    codec.validate_point_value(point_cfg.point_addr, value, point_cfg.niagara_name)


def _is_fast_point(point_cfg: PointConfig) -> bool:
    key = point_cfg.key.lower()
    if "temp" in key:
        return True
    spec = codec.resolve_point_spec(point_cfg.point_addr, point_cfg.niagara_name)
    units = (point_cfg.engineering_units or (spec.units if spec else "")).lower()
    return units.startswith("fahrenheit") or units.startswith("celsius") or units in (
        "degc",
        "degf",
        "degreescelsius",
        "degreesfahrenheit",
    )


def _describe_points(cfg: GatewayConfig) -> None:
    print("Objects exposed:")
    for t_name, t_cfg in cfg.thermostats.items():
        for p_key, p_cfg in t_cfg.points.items():
            print(
                f"  {t_name}.{p_key}: {p_cfg.bacnet.object_type} {p_cfg.bacnet.instance}"
            )


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    parser = argparse.ArgumentParser(description="BACnet gateway for Viconics radios")
    parser.add_argument(
        "--config",
        default="points.yaml",
        help="Path to points config (default: points.yaml)",
    )
    parser.add_argument(
        "--radio-config",
        default="config/radio.yaml",
        help="Path to radio config (default: config/radio.yaml)",
    )
    parser.add_argument("--device-id", type=int, default=5001)
    parser.add_argument("--device-name", default="BAC Viconics Gateway")
    parser.add_argument("--bind-address", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=47808)
    args = parser.parse_args()

    cfg = load_config(args.config)
    radio_cfg = load_radio_config(args.radio_config)

    async def _main() -> None:
        registry = PointRegistry(cfg)
        radio_manager = RadioManager(cfg, registry, radio_cfg, asyncio.get_running_loop())
        radio_manager.start()

        app = create_app_from_config(
            cfg,
            registry,
            radio_manager,
            device_id=args.device_id,
            device_name=args.device_name,
            bind_address=args.bind_address,
            port=args.port,
        )

        print(
            f"BACnet gateway running on {args.bind_address}:{args.port} "
            f"as device {args.device_id}"
        )
        _describe_points(cfg)

        try:
            await asyncio.Future()
        except asyncio.CancelledError:
            pass
        finally:
            radio_manager.stop()
            try:
                app.close()
            except Exception:  # pragma: no cover - best effort cleanup
                logger.exception("Error while closing BACpypes application")

    asyncio.run(_main())
