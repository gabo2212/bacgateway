from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import BinaryIO, Iterator, Optional

LOGGER = logging.getLogger(__name__)

_SHB = 0x0A0D0D0A
_IDB = 0x00000001
_EPB = 0x00000006
_SHB_BYTES = b"\x0a\x0d\x0d\x0a"
_BOM_BE = b"\x1a\x2b\x3c\x4d"
_BOM_LE = b"\x4d\x3c\x2b\x1a"
_DEFAULT_TSRESOL = 1e-6


def _log_debug(event: str, **fields: object) -> None:
    if LOGGER.isEnabledFor(logging.DEBUG):
        LOGGER.debug(event, extra={"event": event, **fields})


@dataclass(frozen=True)
class PcapNgPacket:
    timestamp: float
    data: bytes
    iface_id: int
    caplen: int
    origlen: int


@dataclass(frozen=True)
class _InterfaceInfo:
    link_type: int
    ts_resol: float
    ts_offset: float


class PcapNgReader:
    def __init__(self, path: str) -> None:
        self._path = path

    def packets(self) -> Iterator[PcapNgPacket]:
        with open(self._path, "rb") as handle:
            endian = "little"
            interfaces: dict[int, _InterfaceInfo] = {}
            while True:
                block_type_bytes = _read_exact(handle, 4)
                if block_type_bytes is None:
                    break
                block_len_bytes = _read_exact(handle, 4)
                if block_len_bytes is None:
                    break

                if block_type_bytes == _SHB_BYTES:
                    block_type = _SHB
                    bom_bytes = _read_exact(handle, 4)
                    if bom_bytes is None:
                        break
                    if bom_bytes == _BOM_LE:
                        endian = "little"
                    elif bom_bytes == _BOM_BE:
                        endian = "big"
                    else:
                        _log_debug("pcapng.shb_bom_mismatch", bom=bom_bytes.hex())
                    block_len = int.from_bytes(block_len_bytes, endian)
                    if block_len < 12:
                        _log_debug("pcapng.shb_invalid_length", length=block_len)
                        break
                    remaining = block_len - 12
                    if remaining < 0:
                        _log_debug("pcapng.shb_negative_remaining", remaining=remaining)
                        break
                    if _read_exact(handle, remaining) is None:
                        break
                    continue

                block_type = int.from_bytes(block_type_bytes, endian)
                block_len = int.from_bytes(block_len_bytes, endian)
                if block_len < 12:
                    _log_debug("pcapng.block_invalid_length", length=block_len, type=block_type)
                    break
                body = _read_exact(handle, block_len - 8)
                if body is None:
                    break

                if block_type == _IDB:
                    interface = _parse_idb(body, endian)
                    if interface is None:
                        continue
                    iface_id = len(interfaces)
                    interfaces[iface_id] = interface
                    continue

                if block_type == _EPB:
                    packet = _parse_epb(body, endian, interfaces)
                    if packet is None:
                        continue
                    if packet.iface_id not in interfaces:
                        continue
                    yield packet
                    continue

                # Ignore other block types.


def _read_exact(handle: BinaryIO, size: int) -> Optional[bytes]:
    if size <= 0:
        return b""
    data = handle.read(size)
    if len(data) != size:
        return None
    return data


def _parse_idb(body: bytes, endian: str) -> Optional[_InterfaceInfo]:
    if len(body) < 12:
        _log_debug("pcapng.idb_too_short", length=len(body))
        return None
    link_type = int.from_bytes(body[0:2], endian)
    options = body[8:-4] if len(body) >= 12 else b""
    ts_resol = _DEFAULT_TSRESOL
    ts_offset = 0.0
    for code, value in _iter_options(options, endian):
        if code == 9 and value:
            ts_resol = _decode_ts_resol(value[0])
        elif code == 14 and len(value) >= 8:
            ts_offset = float(int.from_bytes(value[:8], endian, signed=True))
    return _InterfaceInfo(link_type=link_type, ts_resol=ts_resol, ts_offset=ts_offset)


def _parse_epb(
    body: bytes, endian: str, interfaces: dict[int, _InterfaceInfo]
) -> Optional[PcapNgPacket]:
    if len(body) < 24:
        _log_debug("pcapng.epb_too_short", length=len(body))
        return None
    iface_id = int.from_bytes(body[0:4], endian)
    ts_high = int.from_bytes(body[4:8], endian)
    ts_low = int.from_bytes(body[8:12], endian)
    caplen = int.from_bytes(body[12:16], endian)
    origlen = int.from_bytes(body[16:20], endian)
    offset = 20
    if offset + caplen > len(body) - 4:
        _log_debug(
            "pcapng.epb_caplen_mismatch",
            caplen=caplen,
            body_length=len(body),
        )
        return None
    packet_data = bytes(body[offset : offset + caplen])
    offset += caplen
    pad = (4 - (caplen % 4)) % 4
    offset += pad
    if offset > len(body) - 4:
        _log_debug("pcapng.epb_padding_overflow", offset=offset, body_length=len(body))
        return None
    iface = interfaces.get(iface_id)
    if iface is None:
        _log_debug("pcapng.epb_unknown_iface", iface_id=iface_id)
        return None
    timestamp_units = (ts_high << 32) | ts_low
    timestamp = (timestamp_units * iface.ts_resol) + iface.ts_offset
    return PcapNgPacket(
        timestamp=timestamp,
        data=packet_data,
        iface_id=iface_id,
        caplen=caplen,
        origlen=origlen,
    )


def _iter_options(options: bytes, endian: str) -> Iterator[tuple[int, bytes]]:
    offset = 0
    while offset + 4 <= len(options):
        code = int.from_bytes(options[offset : offset + 2], endian)
        length = int.from_bytes(options[offset + 2 : offset + 4], endian)
        offset += 4
        if code == 0:
            break
        if offset + length > len(options):
            break
        value = options[offset : offset + length]
        offset += length
        pad = (4 - (length % 4)) % 4
        if offset + pad > len(options):
            break
        offset += pad
        yield code, value


def _decode_ts_resol(raw: int) -> float:
    if raw & 0x80:
        exp = raw & 0x7F
        return 2.0 ** (-exp)
    return 10.0 ** (-raw)
