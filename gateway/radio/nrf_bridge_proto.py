from __future__ import annotations

import struct
from dataclasses import dataclass
from enum import IntEnum
from typing import Optional
import binascii

MAGIC = b"VW"
VERSION = 1

class FrameType(IntEnum):
    HELLO = 0x01
    HELLO_RESP = 0x02
    RX_FRAME = 0x03
    TX_RAW = 0x04  # Reserved
    STATE = 0x05
    ERROR = 0x06
    GET_STATS = 0x07
    GET_STATS_RESP = 0x08

@dataclass
class NrfFrame:
    frame_type: FrameType
    flags: int
    seq: int
    payload: bytes

    def encode(self) -> bytes:
        header = struct.pack("<2sBBBBH", MAGIC, VERSION, self.frame_type.value, self.flags, self.seq, len(self.payload))
        data = header + self.payload
        crc = binascii.crc32(data) & 0xFFFFFFFF
        return data + struct.pack("<I", crc)

    @classmethod
    def decode(cls, data: bytes) -> Optional[NrfFrame]:
        if len(data) < 12:
            return None
        header_tuple = struct.unpack("<2sBBBBH", data[:8])
        if header_tuple[0] != MAGIC or header_tuple[1] != VERSION:
            return None
        header_tuple = struct.unpack("<2sBBBBH", data[:8])
        length = header_tuple[5]
        if len(data) != 8 + length + 4:
            return None
        
        payload = data[8:8+length]
        crc_actual = binascii.crc32(data[:-4]) & 0xFFFFFFFF
        crc_expected = struct.unpack("<I", data[-4:])[0]
        if crc_actual != crc_expected:
            raise ValueError("CRC mismatch")
            
        return cls(
            frame_type=FrameType(header_tuple[2]),
            flags=header_tuple[3],
            seq=header_tuple[4],
            payload=payload
        )

@dataclass
class RxFramesPayload:
    timestamp_us: int
    channel: int
    rssi_dbm: int
    lqi: int
    raw_psdu: bytes

    @classmethod
    def decode(cls, data: bytes) -> RxFramesPayload:
        ts, ch, rssi, lqi = struct.unpack("<QBbb", data[:11])
        psdu = data[11:]
        return cls(timestamp_us=ts, channel=ch, rssi_dbm=rssi, lqi=lqi, raw_psdu=psdu)

    def encode(self) -> bytes:
        return struct.pack("<QBbb", self.timestamp_us, self.channel, self.rssi_dbm, self.lqi) + self.raw_psdu
