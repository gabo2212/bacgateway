from __future__ import annotations

from dataclasses import dataclass, field

from .app import OtaMsg


@dataclass(frozen=True)
class ObservedKey:
    prefix: int
    code: int
    rest_len: int
    cmd_id: int


@dataclass
class ObservedStats:
    count: int
    first_t: float
    last_t: float
    sample_rest_hex: str
    kinds: set[str] = field(default_factory=set)


def update_registry(reg: dict[ObservedKey, ObservedStats], msg: OtaMsg) -> None:
    if msg.prefix is None or msg.code is None:
        return
    rest_len = len(msg.raw_rest_hex) // 2
    key = ObservedKey(
        prefix=msg.prefix,
        code=msg.code,
        rest_len=rest_len,
        cmd_id=msg.cmd_id,
    )
    entry = reg.get(key)
    if entry is None:
        reg[key] = ObservedStats(
            count=1,
            first_t=msg.t_rel,
            last_t=msg.t_rel,
            sample_rest_hex=msg.raw_rest_hex,
            kinds={msg.kind},
        )
        return
    entry.count += 1
    entry.first_t = min(entry.first_t, msg.t_rel)
    entry.last_t = max(entry.last_t, msg.t_rel)
    entry.kinds.add(msg.kind)
