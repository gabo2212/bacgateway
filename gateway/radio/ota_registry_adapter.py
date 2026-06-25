from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable, Protocol

from gateway.identity_registry import IdentityRecord, IdentityRegistry
from gateway.ota.events import OtaAckEvent, OtaPointEvent, OtaUnknownEvent

LOGGER = logging.getLogger(__name__)

OtaEvent = OtaPointEvent | OtaAckEvent | OtaUnknownEvent


class PointRegistryLike(Protocol):
    def runtimes(self) -> Iterable[Any]:
        ...

    def update_from_radio(
        self,
        comm_addr: int,
        point_addr: int,
        value: Any | None,
        status: int | None,
        link_quality: int | None,
        ok: bool,
    ) -> None:
        ...


@dataclass(frozen=True)
class PointTarget:
    canonical_point: str
    aliases: frozenset[str]
    point_addrs: frozenset[int]
    bacnet_objects: frozenset[tuple[str, int]]


def _normalize_name(value: str | None) -> str:
    if not value:
        return ""
    return "".join(char for char in value.lower() if char.isalnum())


POINT_TARGETS: dict[str, PointTarget] = {
    "occupied_cool_setpoint": PointTarget(
        canonical_point="occupied_cool_setpoint",
        aliases=frozenset(
            _normalize_name(alias)
            for alias in (
                "occupied_cool_setpoint",
                "occ_cool_setpoint",
                "OccupiedCoolingSetpoint",
                "OccCoolSetpoint",
                "cool_setpoint",
                "cooling_setpoint",
            )
        ),
        point_addrs=frozenset({0x1005}),
        bacnet_objects=frozenset({("analogValue", 1005)}),
    ),
    "occupied_heat_setpoint": PointTarget(
        canonical_point="occupied_heat_setpoint",
        aliases=frozenset(
            _normalize_name(alias)
            for alias in (
                "occupied_heat_setpoint",
                "occ_heat_setpoint",
                "OccupiedHeatingSetpoint",
                "OccHeatSetpoint",
                "heat_setpoint",
                "heating_setpoint",
            )
        ),
        point_addrs=frozenset({0x1006}),
        bacnet_objects=frozenset({("analogValue", 1006)}),
    ),
}

ALIAS_TO_CANONICAL: dict[str, str] = {
    alias: canonical
    for canonical, target in POINT_TARGETS.items()
    for alias in target.aliases
}


class OtaRegistryAdapter:
    def __init__(
        self,
        registry: PointRegistryLike,
        *,
        identity_registry: IdentityRegistry | None = None,
        dump_unknown_codes: bool = False,
    ) -> None:
        self._registry = registry
        self._identity_registry = identity_registry
        self._dump_unknown_codes = dump_unknown_codes

    def apply_event(self, event: OtaEvent) -> bool:
        if isinstance(event, OtaPointEvent):
            return self._apply_point_event(event)
        if isinstance(event, OtaAckEvent):
            LOGGER.debug(
                "ota_ack_event",
                extra={
                    "canonical_point": event.canonical_point,
                    "prefix": _hex_or_none(event.prefix),
                    "code": _hex_or_none(event.code),
                },
            )
            return False
        if isinstance(event, OtaUnknownEvent):
            self._log_unknown_event(event)
            return False
        LOGGER.debug("unknown OTA event type", extra={"event_type": type(event).__name__})
        return False

    def _apply_point_event(self, event: OtaPointEvent) -> bool:
        target = self._target_for(event.canonical_point)
        if target is None:
            LOGGER.debug(
                "unknown canonical_point",
                extra={"canonical_point": event.canonical_point},
            )
            return False

        identity = self._resolve_identity(event)
        candidates = self._matching_runtimes(target, identity)
        if not candidates:
            LOGGER.debug(
                "no configured point",
                extra={
                    "canonical_point": target.canonical_point,
                    "eui64": event.identity.eui64,
                    "short_addr": _hex_or_none(event.identity.short_addr),
                },
            )
            return False
        if len(candidates) > 1:
            LOGGER.warning(
                "ambiguous configured point",
                extra={
                    "canonical_point": target.canonical_point,
                    "candidate_count": len(candidates),
                },
            )
            return False

        runtime = candidates[0]
        link_quality = getattr(event, "lqi", None)
        self._registry.update_from_radio(
            runtime.cfg.comm_addr,
            runtime.cfg.point_addr,
            event.value,
            None,
            link_quality,
            True,
        )
        LOGGER.info(
            "ota_point_applied",
            extra={
                "canonical_point": target.canonical_point,
                "value": event.value,
                "comm_addr": runtime.cfg.comm_addr,
                "point_addr": f"0x{runtime.cfg.point_addr:04X}",
            },
        )
        return True

    def _target_for(self, canonical_point: str) -> PointTarget | None:
        normalized = _normalize_name(canonical_point)
        canonical = ALIAS_TO_CANONICAL.get(normalized)
        if canonical is None:
            return None
        return POINT_TARGETS[canonical]

    def _resolve_identity(self, event: OtaPointEvent) -> IdentityRecord | None:
        if self._identity_registry is None:
            return None
        return self._identity_registry.observe(
            eui64=event.identity.eui64,
            short_addr=event.identity.short_addr,
        )

    def _matching_runtimes(
        self,
        target: PointTarget,
        identity: IdentityRecord | None,
    ) -> list[Any]:
        runtimes = list(self._registry.runtimes())
        matches: list[Any] = []
        for runtime in runtimes:
            cfg = runtime.cfg
            if identity is not None and cfg.comm_addr != identity.comm_addr:
                continue
            if self._runtime_matches(cfg, target):
                matches.append(runtime)
        return matches

    def _runtime_matches(self, cfg: Any, target: PointTarget) -> bool:
        names = (
            _normalize_name(getattr(cfg, "key", None)),
            _normalize_name(getattr(cfg, "niagara_name", None)),
            _normalize_name(getattr(cfg.bacnet, "object_name", None)),
        )
        if any(name in target.aliases for name in names):
            return True
        if getattr(cfg, "point_addr", None) in target.point_addrs:
            return True
        object_id = (
            getattr(cfg.bacnet, "object_type", None),
            getattr(cfg.bacnet, "instance", None),
        )
        return object_id in target.bacnet_objects

    def _log_unknown_event(self, event: OtaUnknownEvent) -> None:
        LOGGER.debug(
            "ota_unknown_event",
            extra={
                "prefix": _hex_or_none(event.prefix),
                "code": _hex_or_none(event.code),
                "reason": event.reason,
            },
        )
        if not self._dump_unknown_codes:
            return
        raw_bytes = _bytes_from_hex(event.raw_rest_hex)
        candidate_value = None
        if raw_bytes is not None and len(raw_bytes) >= 4:
            candidate_value = int.from_bytes(raw_bytes[2:4], "big") / 10.0
        LOGGER.info(
            "candidate_ota_point",
            extra={
                "timestamp": _event_timestamp(event),
                "short_addr": _hex_or_none(event.identity.short_addr),
                "eui64": event.identity.eui64,
                "prefix": _hex_or_none(event.prefix),
                "code": _hex_or_none(event.code),
                "raw_bytes": event.raw_rest_hex,
                "candidate_value_x10": candidate_value,
                "channel": getattr(event, "channel", None),
                "rssi_dbm": getattr(event, "rssi_dbm", None),
                "lqi": getattr(event, "lqi", None),
            },
        )


def _hex_or_none(value: int | None) -> str | None:
    if value is None:
        return None
    return f"0x{value:04X}" if value > 0xFF else f"0x{value:02X}"


def _bytes_from_hex(value: str) -> bytes | None:
    try:
        return bytes.fromhex(value)
    except ValueError:
        return None


def _event_timestamp(event: OtaUnknownEvent) -> str:
    timestamp = getattr(event, "timestamp", None)
    if timestamp is None:
        return datetime.now(timezone.utc).isoformat()
    return f"{float(timestamp):.6f}"
