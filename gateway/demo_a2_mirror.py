from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import re
import sys
import threading
import time
from dataclasses import asdict, dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Literal
from urllib.parse import parse_qs, urlparse

from bacpypes3.apdu import SimpleAckPDU, WritePropertyRequest
from bacpypes3.basetypes import EngineeringUnits, PropertyIdentifier
from bacpypes3.constructeddata import Array
from bacpypes3.errors import ExecutionError
from bacpypes3.ipv4.app import NormalApplication
from bacpypes3.local.analog import AnalogInputObject, AnalogValueObject
from bacpypes3.pdu import IPv4Address
from bacpypes3.primitivedata import Null, Unsigned

from .bacnet_server import build_local_device
from .niagara_client import NiagaraClient, NiagaraLoginOptions, WriteResult

try:  # pragma: no cover - exercised when PyYAML is available
    import yaml
except Exception:  # pragma: no cover - json fallback path
    yaml = None

logger = logging.getLogger(__name__)
PRESENT_VALUE = PropertyIdentifier("presentValue")
PointName = Literal["heat", "cool"]


@dataclass(frozen=True)
class NiagaraConfig:
    host: str
    scheme: str
    username_env: str
    password_env: str
    session_env: str
    timeout_sec: float
    login_options: NiagaraLoginOptions


@dataclass(frozen=True)
class OrdConfig:
    room_temp_out: str
    occ_heat_sp_out: str
    occ_cool_sp_out: str
    occ_heat_sp_set: str
    occ_cool_sp_set: str


@dataclass(frozen=True)
class BacnetConfig:
    bind_ip: str
    port: int
    device_id: int
    device_name: str
    room_temp_ai_instance: int
    occ_heat_sp_av_instance: int
    occ_cool_sp_av_instance: int
    write_min_f: float
    write_max_f: float
    write_rate_limit_sec: float


@dataclass(frozen=True)
class ServiceConfig:
    poll_interval_sec: float


@dataclass(frozen=True)
class StatusHttpConfig:
    bind: str
    port: int


@dataclass(frozen=True)
class DemoA2Config:
    niagara: NiagaraConfig
    ord: OrdConfig
    bacnet: BacnetConfig
    service: ServiceConfig
    status_http: StatusHttpConfig


@dataclass
class LastWriteRecord:
    point: str
    value: float
    ok: bool
    ts: float
    status: int | None
    error: str | None


@dataclass
class MirrorState:
    room_temp_f: float | None = None
    occ_heat_sp_f: float | None = None
    occ_cool_sp_f: float | None = None
    last_poll_ts: float | None = None
    login_ok: bool = False
    last_error: str | None = None
    last_write: LastWriteRecord | None = None


class MirrorStateStore:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._state = MirrorState()

    def update_poll(
        self,
        *,
        room_temp_f: float | None = None,
        occ_heat_sp_f: float | None = None,
        occ_cool_sp_f: float | None = None,
        login_ok: bool,
        error: str | None,
    ) -> None:
        with self._lock:
            if room_temp_f is not None:
                self._state.room_temp_f = room_temp_f
            if occ_heat_sp_f is not None:
                self._state.occ_heat_sp_f = occ_heat_sp_f
            if occ_cool_sp_f is not None:
                self._state.occ_cool_sp_f = occ_cool_sp_f
            self._state.last_poll_ts = time.time()
            self._state.login_ok = login_ok
            self._state.last_error = error

    def mark_error(self, error: str, login_ok: bool) -> None:
        with self._lock:
            self._state.last_error = error
            self._state.login_ok = login_ok

    def set_last_write(
        self,
        *,
        point: str,
        value: float,
        ok: bool,
        status: int | None,
        error: str | None,
    ) -> None:
        with self._lock:
            self._state.last_write = LastWriteRecord(
                point=point,
                value=value,
                ok=ok,
                ts=time.time(),
                status=status,
                error=error,
            )

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            state_copy = MirrorState(
                room_temp_f=self._state.room_temp_f,
                occ_heat_sp_f=self._state.occ_heat_sp_f,
                occ_cool_sp_f=self._state.occ_cool_sp_f,
                last_poll_ts=self._state.last_poll_ts,
                login_ok=self._state.login_ok,
                last_error=self._state.last_error,
                last_write=self._state.last_write,
            )
        payload = asdict(state_copy)
        return payload

    def current_point(self, point: PointName) -> float | None:
        with self._lock:
            if point == "heat":
                return self._state.occ_heat_sp_f
            return self._state.occ_cool_sp_f

    def update_point(self, point: PointName, value: float) -> None:
        with self._lock:
            if point == "heat":
                self._state.occ_heat_sp_f = value
            else:
                self._state.occ_cool_sp_f = value

    def update_room_temp(self, value: float) -> None:
        with self._lock:
            self._state.room_temp_f = value


@dataclass(frozen=True)
class WriteOutcome:
    ok: bool
    error_code: str | None
    message: str | None
    status: int | None


class MirrorApplication(NormalApplication):
    def __init__(
        self,
        service: "MirrorService",
        local_address: IPv4Address,
    ) -> None:
        super().__init__(service.device, local_address)
        self.service = service

    async def do_WritePropertyRequest(self, apdu: WritePropertyRequest) -> None:
        if apdu.propertyIdentifier != PRESENT_VALUE:
            await super().do_WritePropertyRequest(apdu)
            return

        obj_id = apdu.objectIdentifier
        object_type, instance = obj_id

        if object_type == "analogInput" and instance == self.service.cfg.bacnet.room_temp_ai_instance:
            raise ExecutionError(errorClass="property", errorCode="writeAccessDenied")

        if object_type != "analogValue":
            await super().do_WritePropertyRequest(apdu)
            return

        point = self.service.point_for_instance(instance)
        if point is None:
            await super().do_WritePropertyRequest(apdu)
            return

        obj = self.get_object_id(obj_id)
        if obj is None:
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
        outcome = await self.service.handle_bacnet_write(point, numeric_value)
        if not outcome.ok:
            if outcome.error_code in {"valueOutOfRange", "writeAccessDenied"}:
                raise ExecutionError(
                    errorClass="property",
                    errorCode=outcome.error_code,
                )
            raise ExecutionError(errorClass="device", errorCode="communicationFailure")

        await obj.write_property(
            apdu.propertyIdentifier,
            property_value,
            array_index,
            priority,
        )
        await self.response(SimpleAckPDU(context=apdu))


class MirrorService:
    def __init__(self, cfg: DemoA2Config, client: NiagaraClient) -> None:
        self.cfg = cfg
        self.client = client
        self.state = MirrorStateStore()
        self.device = None
        self.app: MirrorApplication | None = None

        self._room_temp_obj: AnalogInputObject | None = None
        self._heat_obj: AnalogValueObject | None = None
        self._cool_obj: AnalogValueObject | None = None

        self._last_write_mono: dict[PointName, float] = {}
        self._good_values: dict[PointName, float | None] = {"heat": None, "cool": None}
        self._loop: asyncio.AbstractEventLoop | None = None
        self._stop_event = asyncio.Event()
        self._status_server: ThreadingHTTPServer | None = None
        self._status_thread: threading.Thread | None = None

    def point_for_instance(self, instance: int) -> PointName | None:
        if instance == self.cfg.bacnet.occ_heat_sp_av_instance:
            return "heat"
        if instance == self.cfg.bacnet.occ_cool_sp_av_instance:
            return "cool"
        return None

    def ord_set_for_point(self, point: PointName) -> str:
        return (
            self.cfg.ord.occ_heat_sp_set
            if point == "heat"
            else self.cfg.ord.occ_cool_sp_set
        )

    def ord_out_for_point(self, point: PointName) -> str:
        return (
            self.cfg.ord.occ_heat_sp_out
            if point == "heat"
            else self.cfg.ord.occ_cool_sp_out
        )

    def _write_path_candidates(self, point: PointName, value: float) -> list[str]:
        primary_set = self.ord_set_for_point(point)
        point_out = self.ord_out_for_point(point)
        value_text = f"{float(value)}"
        candidates: list[str] = []

        # Try explicit action invocation syntax first for action slots.
        for base_path in (primary_set, point_out):
            for suffix in ("/set", "/out"):
                if base_path.endswith(suffix):
                    root = base_path[: -len(suffix)]
                    candidates.extend(
                        [
                            f"{root}/proxyExt/writeValue",
                            f"{root}/in8",
                            f"{root}/in10",
                            f"{root}/in16",
                            f"{root}/writeValue",
                            f"{root}/out",
                            f"{root}/set({value_text})",
                            f"{root}/set?actionArg={value_text}",
                            f"{root}/override({value_text})",
                            f"{root}/override?actionArg={value_text}",
                            f"{root}/emergencyOverride({value_text})",
                            f"{root}/emergencyOverride?actionArg={value_text}",
                            f"{root}/set",
                            f"{root}/override",
                            f"{root}/emergencyOverride",
                        ]
                    )
                    break
        candidates.append(primary_set)

        deduped: list[str] = []
        seen: set[str] = set()
        for path in candidates:
            if path in seen:
                continue
            seen.add(path)
            deduped.append(path)
        return deduped

    def object_for_point(self, point: PointName) -> AnalogValueObject:
        obj = self._heat_obj if point == "heat" else self._cool_obj
        if obj is None:  # pragma: no cover - defensive guard
            raise RuntimeError("BACnet objects are not initialized yet")
        return obj

    async def run(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._init_bacnet_objects()
        self._start_status_server()

        local_address = IPv4Address(f"{self.cfg.bacnet.bind_ip}:{self.cfg.bacnet.port}")
        self.app = MirrorApplication(self, local_address)
        self.app.add_object(self._room_temp_obj)
        self.app.add_object(self._heat_obj)
        self.app.add_object(self._cool_obj)

        logger.info(
            "Demo A2 BACnet mirror listening on %s:%s (device_id=%s)",
            self.cfg.bacnet.bind_ip,
            self.cfg.bacnet.port,
            self.cfg.bacnet.device_id,
        )
        logger.info(
            "Objects exposed: AI %s, AV %s, AV %s",
            self.cfg.bacnet.room_temp_ai_instance,
            self.cfg.bacnet.occ_heat_sp_av_instance,
            self.cfg.bacnet.occ_cool_sp_av_instance,
        )

        poll_task = asyncio.create_task(self._poll_loop(), name="demo_a2_poll_loop")
        try:
            await self._stop_event.wait()
        finally:
            poll_task.cancel()
            try:
                await poll_task
            except asyncio.CancelledError:
                pass
            self._shutdown_status_server()
            if self.app:
                try:
                    self.app.close()
                except Exception:  # pragma: no cover - best effort
                    logger.exception("Error closing BACnet application")

    def stop(self) -> None:
        loop = self._loop
        if not loop or self._stop_event.is_set():
            return
        if loop.is_closed():
            return
        try:
            loop.call_soon_threadsafe(self._stop_event.set)
        except RuntimeError:
            # asyncio.run() may already have closed the loop during Ctrl+C shutdown.
            return

    def _init_bacnet_objects(self) -> None:
        # bacpypes3 object init schedules async tasks, so this must run with an active loop.
        self.device = build_local_device(self.cfg.bacnet.device_id, self.cfg.bacnet.device_name)
        self._room_temp_obj = AnalogInputObject(
            objectIdentifier=("analogInput", self.cfg.bacnet.room_temp_ai_instance),
            objectName="Room Temperature",
            presentValue=0.0,
            units=EngineeringUnits("degreesFahrenheit"),
        )
        self._heat_obj = AnalogValueObject(
            objectIdentifier=("analogValue", self.cfg.bacnet.occ_heat_sp_av_instance),
            objectName="Occupied Heating Setpoint",
            presentValue=0.0,
            units=EngineeringUnits("degreesFahrenheit"),
        )
        self._cool_obj = AnalogValueObject(
            objectIdentifier=("analogValue", self.cfg.bacnet.occ_cool_sp_av_instance),
            objectName="Occupied Cooling Setpoint",
            presentValue=0.0,
            units=EngineeringUnits("degreesFahrenheit"),
        )

    async def _poll_loop(self) -> None:
        interval = max(0.1, self.cfg.service.poll_interval_sec)
        while True:
            start = time.monotonic()
            await self._poll_once()
            elapsed = time.monotonic() - start
            await asyncio.sleep(max(0.0, interval - elapsed))

    async def _poll_once(self) -> None:
        errors: list[str] = []
        try:
            await asyncio.to_thread(self.client.ensure_login, self.cfg.ord.room_temp_out)
        except Exception as exc:  # noqa: BLE001 - keep polling loop resilient
            message = f"auth: {exc}"
            errors.append(message)
            self.state.mark_error(message, self.client.login_ok)
            logger.warning("Niagara auth failed for poll cycle: %s", exc)
            self.state.update_poll(
                room_temp_f=None,
                occ_heat_sp_f=None,
                occ_cool_sp_f=None,
                login_ok=self.client.login_ok,
                error=message,
            )
            return

        room_value = await self._safe_read(
            self.cfg.ord.room_temp_out,
            "room_temp_out",
            errors,
            ensure_auth=False,
            allow_reauth=False,
        )
        heat_value = await self._safe_read(
            self.cfg.ord.occ_heat_sp_out,
            "occ_heat_sp_out",
            errors,
            ensure_auth=False,
            allow_reauth=False,
        )
        cool_value = await self._safe_read(
            self.cfg.ord.occ_cool_sp_out,
            "occ_cool_sp_out",
            errors,
            ensure_auth=False,
            allow_reauth=False,
        )

        if room_value is not None:
            if self._room_temp_obj is None:  # pragma: no cover - defensive guard
                raise RuntimeError("Room temperature object is not initialized")
            self._room_temp_obj.presentValue = room_value
            self.state.update_room_temp(room_value)
        if heat_value is not None:
            if self._heat_obj is None:  # pragma: no cover - defensive guard
                raise RuntimeError("Heat setpoint object is not initialized")
            self._heat_obj.presentValue = heat_value
            self.state.update_point("heat", heat_value)
            self._good_values["heat"] = heat_value
        if cool_value is not None:
            if self._cool_obj is None:  # pragma: no cover - defensive guard
                raise RuntimeError("Cool setpoint object is not initialized")
            self._cool_obj.presentValue = cool_value
            self.state.update_point("cool", cool_value)
            self._good_values["cool"] = cool_value

        error_text = "; ".join(errors) if errors else None
        if error_text:
            logger.warning("Poll cycle completed with errors: %s", error_text)
        self.state.update_poll(
            room_temp_f=room_value,
            occ_heat_sp_f=heat_value,
            occ_cool_sp_f=cool_value,
            login_ok=self.client.login_ok,
            error=error_text,
        )

    async def _safe_read(
        self,
        ord_path: str,
        label: str,
        errors: list[str],
        ensure_auth: bool = True,
        allow_reauth: bool = True,
    ) -> float | None:
        try:
            real = await asyncio.to_thread(
                self.client.read_real,
                ord_path,
                ensure_auth,
                allow_reauth,
            )
            return real.value
        except Exception as exc:  # noqa: BLE001 - per-point best-effort behavior
            message = f"{label}: {exc}"
            errors.append(message)
            self.state.mark_error(message, self.client.login_ok)
            logger.warning("Niagara read failed (%s): %s", label, exc)
            return None

    async def handle_bacnet_write(self, point: PointName, value: float) -> WriteOutcome:
        if value < self.cfg.bacnet.write_min_f or value > self.cfg.bacnet.write_max_f:
            message = (
                f"Rejected {point} write {value}: outside {self.cfg.bacnet.write_min_f}"
                f"-{self.cfg.bacnet.write_max_f}"
            )
            logger.warning(message)
            self.state.set_last_write(
                point=point,
                value=value,
                ok=False,
                status=None,
                error=message,
            )
            return WriteOutcome(
                ok=False,
                error_code="valueOutOfRange",
                message=message,
                status=None,
            )

        now = time.monotonic()
        min_gap = max(0.0, self.cfg.bacnet.write_rate_limit_sec)
        last = self._last_write_mono.get(point)
        if last is not None and (now - last) < min_gap:
            message = f"Rejected {point} write {value}: rate limited ({min_gap}s)"
            logger.warning(message)
            self.state.set_last_write(
                point=point,
                value=value,
                ok=False,
                status=None,
                error=message,
            )
            return WriteOutcome(
                ok=False,
                error_code="writeAccessDenied",
                message=message,
                status=None,
            )

        previous_value = self._good_values.get(point)
        if previous_value is None:
            previous_value = self.state.current_point(point)
        path_errors: list[str] = []
        for ord_path in self._write_path_candidates(point, value):
            try:
                path_l = ord_path.lower()
                if (
                    (ord_path.endswith(")") and "(" in ord_path)
                    or ("?actionarg=" in path_l)
                    or path_l.endswith("/set")
                    or path_l.endswith("/override")
                    or path_l.endswith("/emergencyoverride")
                ):
                    attempt = await asyncio.to_thread(
                        self.client.invoke_action_ord,
                        ord_path,
                    )
                else:
                    attempt = await asyncio.to_thread(self.client.write_real, ord_path, value)
            except Exception as exc:  # noqa: BLE001 - keep service alive
                attempt = WriteResult(
                    ok=False,
                    status=None,
                    error=f"raised: {exc}",
                    response_body=None,
                )
            if attempt.ok:
                confirmed_value, confirm_error = await self._confirm_write_applied(
                    point,
                    value,
                    focus_path=ord_path,
                    attempts=12,
                    sleep_sec=0.5,
                )
                if confirm_error is None:
                    applied_value = confirmed_value if confirmed_value is not None else value
                    return self._complete_successful_write(
                        point=point,
                        requested_value=value,
                        applied_value=applied_value,
                        now=now,
                        attempt=attempt,
                        path=ord_path,
                    )

                execute_ord = await self._trigger_proxy_execute_if_applicable(ord_path)
                if execute_ord is not None:
                    confirmed_value, confirm_error = await self._confirm_write_applied(
                        point,
                        value,
                        focus_path=ord_path,
                        attempts=12,
                        sleep_sec=0.5,
                    )
                    if confirm_error is None:
                        applied_value = confirmed_value if confirmed_value is not None else value
                        return self._complete_successful_write(
                            point=point,
                            requested_value=value,
                            applied_value=applied_value,
                            now=now,
                            attempt=attempt,
                            path=ord_path,
                            context=f"after proxy execute {execute_ord}",
                        )

                if self._is_deferred_write_candidate(ord_path):
                    logger.info(
                        "Niagara write pending confirmation point=%s path=%s status=%s; extending confirmation window",
                        point,
                        ord_path,
                        attempt.status,
                    )
                    confirmed_value, confirm_error = await self._confirm_write_applied(
                        point,
                        value,
                        focus_path=ord_path,
                        attempts=90,
                        sleep_sec=1.0,
                    )
                    if confirm_error is None:
                        applied_value = confirmed_value if confirmed_value is not None else value
                        return self._complete_successful_write(
                            point=point,
                            requested_value=value,
                            applied_value=applied_value,
                            now=now,
                            attempt=attempt,
                            path=ord_path,
                            context="after extended confirmation window",
                        )

                err = (
                    f"accepted-but-unconfirmed on {ord_path} status={attempt.status}: "
                    f"{confirm_error}"
                )
                path_errors.append(err)
                continue
            err = attempt.error or f"status={attempt.status}"
            path_errors.append(f"{ord_path}: {err}")

        combined_error = "Niagara write failed or remained unapplied on all candidate paths"
        if path_errors:
            combined_error = f"{combined_error} ({'; '.join(path_errors)})"
        logger.warning("Niagara write failed point=%s value=%s: %s", point, value, combined_error)
        self._revert_point(point, previous_value)
        self.state.set_last_write(
            point=point,
            value=value,
            ok=False,
            status=None,
            error=combined_error,
        )
        self.state.mark_error(combined_error, self.client.login_ok)
        return WriteOutcome(
            ok=False,
            error_code="communicationFailure",
            message=combined_error,
            status=None,
        )

    async def _confirm_write_applied(
        self,
        point: PointName,
        requested_value: float,
        focus_path: str | None = None,
        attempts: int = 10,
        sleep_sec: float = 0.5,
    ) -> tuple[float | None, str | None]:
        ord_out = self.ord_out_for_point(point)
        read_paths = [ord_out]
        if focus_path:
            normalized_focus = focus_path.split("?", 1)[0]
            normalized_focus = re.sub(r"/(set|override|emergencyOverride)\([^)]*\)$", "", normalized_focus)
            read_paths.insert(0, normalized_focus)
        for suffix in ("/set", "/out"):
            if ord_out.endswith(suffix):
                root = ord_out[: -len(suffix)]
                read_paths.extend(
                    [
                        f"{root}/writeValue",
                        f"{root}/proxyExt/writeValue",
                        f"{root}/in8",
                        f"{root}/in10",
                        f"{root}/in16",
                        f"{root}/out",
                        f"{root}/proxyExt/in8",
                        f"{root}/proxyExt/in10",
                        f"{root}/proxyExt/in16",
                    ]
                )
                break
        read_paths = list(dict.fromkeys(read_paths))
        tolerance = 0.11
        last_observed: float | None = None
        last_error: str | None = None

        for _attempt in range(max(1, attempts)):
            observed: list[str] = []
            for read_path in read_paths:
                try:
                    real = await asyncio.to_thread(self.client.read_real, read_path)
                    last_observed = real.value
                    observed.append(f"{read_path}={real.value}")
                    if abs(real.value - requested_value) <= tolerance:
                        return real.value, None
                except Exception as exc:  # noqa: BLE001 - keep write flow resilient
                    observed.append(f"{read_path}=error:{exc}")
            if observed:
                last_error = (
                    "Write not applied yet "
                    f"(requested={requested_value}; observed={', '.join(observed)})"
                )
            else:
                last_error = "Write readback failed (no readable paths)"
            await asyncio.sleep(max(0.05, sleep_sec))

        if last_error is None:
            last_error = "Write confirmation failed"
        return last_observed, last_error

    def _is_deferred_write_candidate(self, ord_path: str) -> bool:
        base = ord_path.split("?", 1)[0].lower()
        if base.endswith("/proxyext/writevalue"):
            return True
        if re.search(r"/in(8|10|16)$", base) is not None:
            return True
        return False

    def _proxy_execute_candidates(self, ord_path: str) -> list[str]:
        base = ord_path.split("?", 1)[0]
        base = re.sub(r"/(set|override|emergencyOverride)\([^)]*\)$", "", base)
        base = re.sub(r"/(set|override|emergencyOverride)$", "", base)
        base = re.sub(r"/proxyExt/in\d+$", "", base)
        base = re.sub(r"/in\d+$", "", base)
        for suffix in ("/proxyExt/writeValue", "/writeValue", "/out"):
            if base.endswith(suffix):
                base = base[: -len(suffix)]
                break
        if not base:
            return []
        candidates = [f"{base}/proxyExt/execute()", f"{base}/proxyExt/execute"]
        deduped: list[str] = []
        seen: set[str] = set()
        for candidate in candidates:
            if candidate in seen:
                continue
            seen.add(candidate)
            deduped.append(candidate)
        return deduped

    async def _trigger_proxy_execute_if_applicable(self, ord_path: str) -> str | None:
        path_l = ord_path.lower()
        if not (
            path_l.endswith("/proxyext/writevalue")
            or re.search(r"/in\d+$", path_l) is not None
            or path_l.endswith("/set")
            or path_l.endswith("/override")
            or path_l.endswith("/emergencyoverride")
        ):
            return None

        for execute_ord in self._proxy_execute_candidates(ord_path):
            try:
                result = await asyncio.to_thread(self.client.invoke_action_ord, execute_ord)
            except Exception as exc:  # noqa: BLE001 - keep write flow resilient
                logger.info("Niagara proxy execute raised ord=%s error=%s", execute_ord, exc)
                continue
            if result.ok:
                logger.info("Niagara proxy execute ok ord=%s", execute_ord)
                return execute_ord
            logger.info(
                "Niagara proxy execute failed ord=%s status=%s error=%s",
                execute_ord,
                result.status,
                result.error,
            )
        return None

    def _complete_successful_write(
        self,
        *,
        point: PointName,
        requested_value: float,
        applied_value: float,
        now: float,
        attempt: WriteResult,
        path: str,
        context: str | None = None,
    ) -> WriteOutcome:
        self._last_write_mono[point] = now
        self._good_values[point] = applied_value
        self.object_for_point(point).presentValue = applied_value
        self.state.update_point(point, applied_value)
        self.state.set_last_write(
            point=point,
            value=applied_value,
            ok=True,
            status=attempt.status,
            error=None,
        )
        context_suffix = f" ({context})" if context else ""
        logger.info(
            "Niagara write ok%s point=%s requested=%s applied=%s status=%s path=%s",
            context_suffix,
            point,
            requested_value,
            applied_value,
            attempt.status,
            path,
        )
        return WriteOutcome(ok=True, error_code=None, message=None, status=attempt.status)

    def _revert_point(self, point: PointName, value: float | None) -> None:
        if value is None:
            return
        self.object_for_point(point).presentValue = value
        self.state.update_point(point, value)
        logger.info("Reverted %s Present_Value to %s after failed write", point, value)

    def nudge_from_http(self, point: PointName, delta: float) -> WriteOutcome:
        current = self.state.current_point(point)
        if current is None:
            return WriteOutcome(
                ok=False,
                error_code="communicationFailure",
                message=f"No current value available for point '{point}'",
                status=None,
            )
        target = current + delta
        return self.write_from_http(point, target)

    def write_from_http(self, point: PointName, value: float) -> WriteOutcome:
        if self._loop is None:
            return WriteOutcome(
                ok=False,
                error_code="communicationFailure",
                message="Service loop is not initialized",
                status=None,
            )
        future = asyncio.run_coroutine_threadsafe(
            self.handle_bacnet_write(point, value),
            self._loop,
        )
        try:
            return future.result(timeout=max(30.0, self.cfg.niagara.timeout_sec + 2.0))
        except Exception as exc:  # noqa: BLE001 - HTTP layer should not crash service
            logger.warning("HTTP write failed for %s: %s", point, exc)
            return WriteOutcome(
                ok=False,
                error_code="communicationFailure",
                message=str(exc),
                status=None,
            )

    def health_payload(self) -> dict[str, Any]:
        payload = self.state.snapshot()
        payload["login_reason"] = self.client.login_reason
        payload["login_scheme"] = self.client.login_scheme
        payload["uptime_sec"] = round(time.monotonic(), 3)
        return payload

    def _start_status_server(self) -> None:
        handler_cls = _build_status_handler(self)
        self._status_server = ThreadingHTTPServer(
            (self.cfg.status_http.bind, self.cfg.status_http.port),
            handler_cls,
        )
        self._status_thread = threading.Thread(
            target=self._status_server.serve_forever,
            name="demo_a2_status_http",
            daemon=True,
        )
        self._status_thread.start()
        logger.info(
            "Status HTTP listening on http://%s:%s",
            self.cfg.status_http.bind,
            self.cfg.status_http.port,
        )

    def _shutdown_status_server(self) -> None:
        if not self._status_server:
            return
        try:
            self._status_server.shutdown()
            self._status_server.server_close()
        finally:
            self._status_server = None
        if self._status_thread and self._status_thread.is_alive():
            self._status_thread.join(timeout=2.0)
        self._status_thread = None


def _build_status_handler(service: MirrorService) -> type[BaseHTTPRequestHandler]:
    class StatusHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 - stdlib signature
            parsed = urlparse(self.path)
            if parsed.path == "/health":
                self._write_json(HTTPStatus.OK, service.health_payload())
                return
            if parsed.path == "/":
                self._write_html(self._render_home())
                return
            self._write_json(HTTPStatus.NOT_FOUND, {"error": "not found"})

        def do_POST(self) -> None:  # noqa: N802 - stdlib signature
            parsed = urlparse(self.path)
            if parsed.path not in {"/nudge", "/write"}:
                self._write_json(HTTPStatus.NOT_FOUND, {"error": "not found"})
                return

            params = parse_qs(parsed.query)
            point_raw = (params.get("point") or [""])[0].strip().lower()
            if point_raw not in {"heat", "cool"}:
                self._write_json(HTTPStatus.BAD_REQUEST, {"error": "point must be heat or cool"})
                return

            if parsed.path == "/nudge":
                delta_raw = (params.get("delta") or [""])[0].strip()
                try:
                    delta = float(delta_raw)
                except ValueError:
                    self._write_json(HTTPStatus.BAD_REQUEST, {"error": "delta must be numeric"})
                    return

                outcome = service.nudge_from_http(point_raw, delta)  # type: ignore[arg-type]
                payload: dict[str, Any] = {
                    "ok": outcome.ok,
                    "point": point_raw,
                    "delta": delta,
                    "status": outcome.status,
                    "error": outcome.message,
                }
            else:
                value_raw = (params.get("value") or [""])[0].strip()
                try:
                    value = float(value_raw)
                except ValueError:
                    self._write_json(HTTPStatus.BAD_REQUEST, {"error": "value must be numeric"})
                    return
                outcome = service.write_from_http(point_raw, value)  # type: ignore[arg-type]
                payload = {
                    "ok": outcome.ok,
                    "point": point_raw,
                    "value": value,
                    "status": outcome.status,
                    "error": outcome.message,
                }

            if outcome.ok:
                status = HTTPStatus.OK
            elif outcome.error_code in {"valueOutOfRange", "writeAccessDenied"}:
                status = HTTPStatus.BAD_REQUEST
            else:
                status = HTTPStatus.BAD_GATEWAY
            self._write_json(
                status,
                payload,
            )

        def log_message(self, format: str, *args: Any) -> None:  # noqa: A003
            logger.info("status_http %s - %s", self.address_string(), format % args)

        def _write_json(self, status: HTTPStatus, payload: dict[str, Any]) -> None:
            body = json.dumps(payload, indent=2, sort_keys=True).encode("utf-8")
            self.send_response(int(status))
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _write_html(self, html: str) -> None:
            body = html.encode("utf-8")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _render_home(self) -> str:
            health = service.health_payload()
            last_write = health.get("last_write")
            last_write_text = json.dumps(last_write, indent=2) if last_write else "None"
            write_min = service.cfg.bacnet.write_min_f
            write_max = service.cfg.bacnet.write_max_f
            heat_value = health.get("occ_heat_sp_f")
            cool_value = health.get("occ_cool_sp_f")
            heat_input = "" if heat_value is None else f"{float(heat_value):.1f}"
            cool_input = "" if cool_value is None else f"{float(cool_value):.1f}"
            return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8"/>
  <title>Demo A2 Niagara Mirror</title>
  <style>
    body {{ font-family: Segoe UI, Tahoma, sans-serif; margin: 24px; background: #f4f6fb; color: #1f2937; }}
    .card {{ background: #ffffff; border: 1px solid #dbe3f0; border-radius: 8px; padding: 16px; max-width: 860px; }}
    code, pre {{ background: #eef2ff; padding: 4px 6px; border-radius: 4px; }}
    .row {{ margin-bottom: 8px; }}
    .point {{ margin-top: 10px; padding: 10px; border: 1px solid #dbe3f0; border-radius: 8px; background: #f8faff; }}
    .point-line {{ display: flex; flex-wrap: wrap; align-items: center; gap: 8px; }}
    .point-name {{ min-width: 120px; font-weight: 700; }}
    .point-value {{ min-width: 90px; }}
    .value-input {{ width: 92px; padding: 6px; border: 1px solid #b3c2de; border-radius: 6px; }}
    button {{ border: 1px solid #93a7cf; background: #e8efff; color: #1f2937; padding: 8px 10px; border-radius: 6px; cursor: pointer; font-size: 14px; }}
    button:hover {{ background: #d9e6ff; }}
    .hint {{ color: #475569; font-size: 13px; }}
  </style>
  <script>
    function showResult(body) {{
      const out = document.getElementById('nudge_result');
      out.textContent = JSON.stringify(body, null, 2);
    }}

    function adjustValue(point, delta) {{
      const input = document.getElementById(point + '_value');
      if (!input) return;
      const current = Number.parseFloat(input.value);
      const base = Number.isFinite(current) ? current : 0.0;
      const next = base + delta;
      const minVal = Number.parseFloat(input.min);
      const maxVal = Number.parseFloat(input.max);
      let clamped = next;
      if (Number.isFinite(minVal)) clamped = Math.max(minVal, clamped);
      if (Number.isFinite(maxVal)) clamped = Math.min(maxVal, clamped);
      input.value = clamped.toFixed(1);
    }}

    async function writePoint(point) {{
      const input = document.getElementById(point + '_value');
      const value = input ? input.value : '';
      const url = '/write?point=' + encodeURIComponent(point) + '&value=' + encodeURIComponent(value);
      const r = await fetch(url, {{ method: 'POST' }});
      const body = await r.json();
      showResult(body);
    }}
  </script>
</head>
<body>
  <div class="card">
    <h1>Demo A2 Niagara ORD Mirror</h1>
    <div class="row"><strong>login_ok:</strong> {health.get("login_ok")}</div>
    <div class="row"><strong>login_reason:</strong> {health.get("login_reason")}</div>
    <div class="row"><strong>login_scheme:</strong> {health.get("login_scheme")}</div>
    <div class="row"><strong>last_poll_ts:</strong> {health.get("last_poll_ts")}</div>
    <div class="row"><strong>room_temp_f:</strong> {health.get("room_temp_f")}</div>
    <div class="row"><strong>occ_heat_sp_f:</strong> {health.get("occ_heat_sp_f")}</div>
    <div class="row"><strong>occ_cool_sp_f:</strong> {health.get("occ_cool_sp_f")}</div>
    <div class="row"><strong>last_error:</strong> {health.get("last_error")}</div>

    <div class="point">
      <div class="point-line">
        <span class="point-name">Heat SP</span>
        <span class="point-value">{health.get("occ_heat_sp_f")} F</span>
        <button onclick="adjustValue('heat', -0.5)">-</button>
        <button onclick="adjustValue('heat', 0.5)">+</button>
        <input id="heat_value" class="value-input" type="number" step="0.1" min="{write_min}" max="{write_max}" value="{heat_input}" />
        <button onclick="writePoint('heat')">Write</button>
      </div>
    </div>

    <div class="point">
      <div class="point-line">
        <span class="point-name">Cool SP</span>
        <span class="point-value">{health.get("occ_cool_sp_f")} F</span>
        <button onclick="adjustValue('cool', -0.5)">-</button>
        <button onclick="adjustValue('cool', 0.5)">+</button>
        <input id="cool_value" class="value-input" type="number" step="0.1" min="{write_min}" max="{write_max}" value="{cool_input}" />
        <button onclick="writePoint('cool')">Write</button>
      </div>
    </div>

    <div class="hint">Writable points are numeric real values in range {write_min}-{write_max}F. Writes are sent only when you click Write.</div>
    <h2>nudge_result</h2>
    <pre id="nudge_result">None</pre>
    <h2>last_write</h2>
    <pre>{last_write_text}</pre>
    <p>Health JSON: <a href="/health">/health</a></p>
    <p>Nudge: <code>POST /nudge?point=heat|cool&delta=1</code></p>
    <p>Write: <code>POST /write?point=heat|cool&value=72.0</code></p>
  </div>
</body>
</html>"""

    return StatusHandler


def _load_mapping(path: Path) -> dict[str, Any]:
    if path.suffix.lower() == ".json":
        with path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
        if not isinstance(data, dict):
            raise ValueError("Config JSON must be an object")
        return data

    if yaml is None:
        raise RuntimeError(
            "PyYAML is not installed; use a JSON config file or install PyYAML"
        )
    with path.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    if not isinstance(data, dict):
        raise ValueError("Config YAML must be a mapping")
    return data


def _required_str(data: dict[str, Any], key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"Missing required string config key: {key}")
    return value.strip()


def load_demo_config(path: str | Path) -> DemoA2Config:
    config_path = Path(path)
    raw = _load_mapping(config_path)

    raw_niagara = raw.get("niagara", {})
    raw_ord = raw.get("ord", {})
    raw_bacnet = raw.get("bacnet", {})
    raw_service = raw.get("service", {})
    raw_status = raw.get("status_http", {})
    if not all(isinstance(section, dict) for section in (raw_niagara, raw_ord, raw_bacnet, raw_service, raw_status)):
        raise ValueError("niagara/ord/bacnet/service/status_http sections must be mappings")

    raw_login = raw_niagara.get("login", {})
    if not isinstance(raw_login, dict):
        raise ValueError("niagara.login must be a mapping")

    login_options = NiagaraLoginOptions(
        endpoint_path=str(raw_login.get("login_path", raw_login.get("endpoint_path", "/login"))),
        content_mode=str(raw_login.get("content_mode", "auto")),
        auth_mode=str(raw_login.get("auth_mode", "auto")),
        client_first_field=str(raw_login.get("client_first_field", "clientFirstMessage")),
        server_first_field=str(raw_login.get("server_first_field", "serverFirstMessage")),
        client_final_field=str(raw_login.get("client_final_field", "clientFinalMessage")),
        server_final_field=str(raw_login.get("server_final_field", "serverFinalMessage")),
        username_field=str(raw_login.get("username_field", "username")),
        password_field=str(raw_login.get("password_field", "password")),
        scheme_field=str(raw_login.get("scheme_field", "scheme")),
        token_field=str(raw_login.get("token_field", "token")),
        cookie_postfix_field=str(raw_login.get("cookie_postfix_field", "cookiePostfix")),
        support_action_field=str(raw_login.get("support_action_field", "action")),
        support_action_first=str(
            raw_login.get("support_action_first", "sendClientFirstMessage")
        ),
        support_action_final=str(
            raw_login.get("support_action_final", "sendClientFinalMessage")
        ),
        support_content_type=str(
            raw_login.get("support_content_type", "application/x-niagara-login-support")
        ),
        hash_algorithms=tuple(
            str(item) for item in raw_login.get("hash_algorithms", ["sha256", "sha1"])
        ),
    )

    niagara_cfg = NiagaraConfig(
        host=_required_str(raw_niagara, "host"),
        scheme=str(raw_niagara.get("scheme", "http")),
        username_env=str(raw_niagara.get("username_env", "NIAGARA_USER")),
        password_env=str(raw_niagara.get("password_env", "NIAGARA_PASS")),
        session_env=str(raw_niagara.get("session_env", "NIAGARA_SESSION")),
        timeout_sec=float(raw_niagara.get("timeout_sec", 10.0)),
        login_options=login_options,
    )
    ord_cfg = OrdConfig(
        room_temp_out=_required_str(raw_ord, "room_temp_out"),
        occ_heat_sp_out=_required_str(raw_ord, "occ_heat_sp_out"),
        occ_cool_sp_out=_required_str(raw_ord, "occ_cool_sp_out"),
        occ_heat_sp_set=_required_str(raw_ord, "occ_heat_sp_set"),
        occ_cool_sp_set=_required_str(raw_ord, "occ_cool_sp_set"),
    )
    bacnet_cfg = BacnetConfig(
        bind_ip=str(raw_bacnet.get("bind_ip", "0.0.0.0")),
        port=int(raw_bacnet.get("port", 47808)),
        device_id=int(raw_bacnet.get("device_id", 5001)),
        device_name=str(raw_bacnet.get("device_name", "Niagara ORD Mirror")),
        room_temp_ai_instance=int(raw_bacnet.get("room_temp_ai_instance", 1101)),
        occ_heat_sp_av_instance=int(raw_bacnet.get("occ_heat_sp_av_instance", 1201)),
        occ_cool_sp_av_instance=int(raw_bacnet.get("occ_cool_sp_av_instance", 1202)),
        write_min_f=float(raw_bacnet.get("write_min_f", 40.0)),
        write_max_f=float(raw_bacnet.get("write_max_f", 95.0)),
        write_rate_limit_sec=float(raw_bacnet.get("write_rate_limit_sec", 1.0)),
    )
    service_cfg = ServiceConfig(
        poll_interval_sec=float(raw_service.get("poll_interval_sec", 0.5))
    )
    status_cfg = StatusHttpConfig(
        bind=str(raw_status.get("bind", "127.0.0.1")),
        port=int(raw_status.get("port", 8090)),
    )
    return DemoA2Config(
        niagara=niagara_cfg,
        ord=ord_cfg,
        bacnet=bacnet_cfg,
        service=service_cfg,
        status_http=status_cfg,
    )


def _build_client(cfg: DemoA2Config) -> NiagaraClient:
    username = os.getenv(cfg.niagara.username_env)
    password = os.getenv(cfg.niagara.password_env)
    session_cookie = os.getenv(cfg.niagara.session_env)
    if session_cookie:
        logger.info("Using Niagara session cookie injection mode from %s", cfg.niagara.session_env)
    elif not username or not password:
        logger.warning(
            "No Niagara session cookie provided and credentials are missing (%s/%s)",
            cfg.niagara.username_env,
            cfg.niagara.password_env,
        )

    return NiagaraClient(
        host=cfg.niagara.host,
        scheme=cfg.niagara.scheme,
        username=username,
        password=password,
        session_cookie=session_cookie,
        timeout_sec=cfg.niagara.timeout_sec,
        login_options=cfg.niagara.login_options,
        probe_ord_path=cfg.ord.room_temp_out,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Demo A2: Niagara /ord to BACnet/IP mirror without Niagara BACnet license",
    )
    parser.add_argument(
        "--config",
        default="configs/demo_a2.yaml",
        help="Path to YAML/JSON config (default: configs/demo_a2.yaml)",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        help="Python log level (default: INFO)",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    if sys.platform.startswith("win") and hasattr(asyncio, "WindowsSelectorEventLoopPolicy"):
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

    cfg = load_demo_config(args.config)
    service = MirrorService(cfg=cfg, client=_build_client(cfg))

    try:
        asyncio.run(service.run())
    except KeyboardInterrupt:
        logger.info("Shutting down Demo A2 mirror")
        service.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
