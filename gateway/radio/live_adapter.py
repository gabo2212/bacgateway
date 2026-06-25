from __future__ import annotations

import logging
from typing import Optional, Union

from ..ota.app import parse_ota_msg
from ..ota.events import OtaDeviceIdentity, OtaPointEvent, OtaAckEvent, OtaUnknownEvent
from ..ota.ieee802154 import parse_mac_frame
from ..ota.pointmap import PointMap, load_pointmap
from ..ota.zigbee import parse_aps_frame, parse_nwk_frame
from .nrf_bridge_proto import RxFramesPayload

LOGGER = logging.getLogger(__name__)

OtaEvent = Union[OtaPointEvent, OtaAckEvent, OtaUnknownEvent]

class LiveAdapter:
    def __init__(self, point_map: Optional[PointMap] = None):
        self.point_map = point_map

    def process_rx_frame(self, rx_payload: RxFramesPayload) -> Optional[OtaEvent]:
        # Parse 802.15.4 MAC frame
        mac_frame = parse_mac_frame(rx_payload.raw_psdu)
        if not mac_frame:
            # Maybe it has a FCS trailer, try stripping it
            mac_frame = parse_mac_frame(rx_payload.raw_psdu[:-2])
            if not mac_frame:
                LOGGER.debug("Failed to parse MAC frame")
                return None

        # Parse Zigbee NWK frame
        nwk_frame = parse_nwk_frame(mac_frame.payload)
        if not nwk_frame:
            LOGGER.debug("Failed to parse NWK frame")
            return None

        # Parse Zigbee APS frame
        aps_frame = parse_aps_frame(nwk_frame.payload)
        if not aps_frame:
            LOGGER.debug("Failed to parse APS frame")
            return None

        # Parse OTA Message
        t_rel = rx_payload.timestamp_us / 1000000.0
        ota_msg = parse_ota_msg(
            t_rel=t_rel,
            nwk_src_short=nwk_frame.src,
            nwk_dst_short=nwk_frame.dst,
            aps_frame=aps_frame,
            point_map=self.point_map,
        )
        if not ota_msg:
            LOGGER.debug("Failed to parse OTA message")
            return None

        # Determine identity
        # eui64 might be known via some external map, but here we populate what we have
        identity = OtaDeviceIdentity(
            short_addr=ota_msg.device_short,
            device_label=None # Not doing full reverse lookup here unless we add it
        )

        if ota_msg.kind in ("analog_x10", "enum", "u8"):
            return OtaPointEvent(
                identity=identity,
                prefix=ota_msg.prefix,
                code=ota_msg.code,
                canonical_point=ota_msg.label or f"unknown_{ota_msg.prefix}_{ota_msg.code}",
                kind=ota_msg.kind,
                value=ota_msg.value,
                enum_label=None, # Could map enum if needed
                timestamp=t_rel,
                source="nrf_rx",
                channel=rx_payload.channel,
                rssi_dbm=rx_payload.rssi_dbm,
                lqi=rx_payload.lqi,
            )
        elif ota_msg.kind == "ack":
            return OtaAckEvent(
                identity=identity,
                prefix=ota_msg.prefix,
                code=ota_msg.code,
                canonical_point=ota_msg.label or f"unknown_{ota_msg.prefix}_{ota_msg.code}",
                extra_hex=ota_msg.ack_extra_hex,
                timestamp=t_rel,
                source="nrf_rx",
                channel=rx_payload.channel,
                rssi_dbm=rx_payload.rssi_dbm,
                lqi=rx_payload.lqi,
            )
        else:
            return OtaUnknownEvent(
                identity=identity,
                prefix=ota_msg.prefix,
                code=ota_msg.code,
                raw_rest_hex=ota_msg.raw_rest_hex,
                reason=ota_msg.reason,
                timestamp=t_rel,
                source="nrf_rx",
                channel=rx_payload.channel,
                rssi_dbm=rx_payload.rssi_dbm,
                lqi=rx_payload.lqi,
            )
