from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

from .model import GatewayConfig

LOGGER = logging.getLogger(__name__)


def normalize_eui64(eui64: str | None) -> str | None:
    if eui64 is None:
        return None
    text = eui64.strip().lower().replace("-", "").replace(":", "")
    if text.startswith("0x"):
        text = text[2:]
    if len(text) != 16 or any(char not in "0123456789abcdef" for char in text):
        return eui64.strip().lower()
    return ":".join(text[idx : idx + 2] for idx in range(0, 16, 2))


@dataclass
class IdentityRecord:
    thermostat_name: str
    comm_addr: int
    eui64: Optional[str] = None
    device_label: Optional[str] = None
    current_short_addr: Optional[int] = None

    @property
    def stable_id(self) -> str:
        return self.eui64 or f"thermostat:{self.thermostat_name}"


class IdentityRegistry:
    def __init__(self) -> None:
        self._by_stable_id: dict[str, IdentityRecord] = {}
        self._by_eui64: dict[str, IdentityRecord] = {}
        self._by_short_addr: dict[int, IdentityRecord] = {}

    @classmethod
    def from_config(cls, cfg: GatewayConfig) -> IdentityRegistry:
        registry = cls()
        for name, thermostat in cfg.thermostats.items():
            registry.register_thermostat(
                thermostat_name=name,
                comm_addr=thermostat.comm_addr,
                eui64=thermostat.ieee,
                device_label=thermostat.logical_id,
            )
        return registry

    def register_thermostat(
        self,
        *,
        thermostat_name: str,
        comm_addr: int,
        eui64: str | None = None,
        short_addr: int | None = None,
        device_label: str | None = None,
    ) -> IdentityRecord:
        normalized_eui = normalize_eui64(eui64)
        stable_id = normalized_eui or f"thermostat:{thermostat_name}"
        record = IdentityRecord(
            thermostat_name=thermostat_name,
            comm_addr=comm_addr,
            eui64=normalized_eui,
            device_label=device_label,
        )
        self._by_stable_id[stable_id] = record
        if normalized_eui is not None:
            self._by_eui64[normalized_eui] = record
        if short_addr is not None:
            self._set_short_addr(record, short_addr)
        return record

    def lookup(
        self,
        *,
        eui64: str | None = None,
        short_addr: int | None = None,
    ) -> IdentityRecord | None:
        if eui64 is not None:
            record = self.lookup_by_eui64(eui64)
            if record is not None:
                return record
        if short_addr is not None:
            return self.lookup_by_short(short_addr)
        return None

    def lookup_by_eui64(self, eui64: str) -> IdentityRecord | None:
        normalized = normalize_eui64(eui64)
        if normalized is None:
            return None
        return self._by_eui64.get(normalized)

    def lookup_by_short(self, short_addr: int) -> IdentityRecord | None:
        return self._by_short_addr.get(short_addr)

    def observe(
        self,
        *,
        eui64: str | None = None,
        short_addr: int | None = None,
    ) -> IdentityRecord | None:
        record = self.lookup(eui64=eui64, short_addr=short_addr)
        if record is None:
            return None
        if eui64 is not None and record.eui64 is None:
            normalized = normalize_eui64(eui64)
            if normalized is not None:
                record.eui64 = normalized
                self._by_eui64[normalized] = record
        if short_addr is not None:
            self._set_short_addr(record, short_addr)
        return record

    def has_records(self) -> bool:
        return bool(self._by_stable_id)

    def _set_short_addr(self, record: IdentityRecord, short_addr: int) -> None:
        old_short = record.current_short_addr
        if old_short == short_addr:
            self._by_short_addr[short_addr] = record
            return
        if old_short is not None:
            self._by_short_addr.pop(old_short, None)
            LOGGER.info(
                "short address changed",
                extra={
                    "eui64": record.eui64,
                    "thermostat_name": record.thermostat_name,
                    "old_short_addr": f"0x{old_short:04X}",
                    "new_short_addr": f"0x{short_addr:04X}",
                },
            )
        record.current_short_addr = short_addr
        self._by_short_addr[short_addr] = record
