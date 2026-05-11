from gateway.radio.live_adapter import LiveAdapter
from gateway.radio.nrf_bridge_proto import RxFramesPayload
from gateway.ota.events import OtaPointEvent, OtaAckEvent, OtaUnknownEvent
import struct

def build_raw_psdu(ota_payload: bytes) -> bytes:
    # Build APS
    aps = struct.pack("<BBHHBB", 0x00, 0x32, 0x0002, 0xC1E4, 0x0A, 0x00) + ota_payload
    
    # Build NWK (FC, DST, SRC, Radius, Seq)
    nwk = struct.pack("<HHHH", 0x0000, 0x143E, 0x0000, 0x1234) + aps
    
    # Build MAC
    # Frame control: Data frame (0x1), no security, short addresses
    # 0x01 | (2 << 10) | (2 << 14) -> 0x8801
    mac_fc = 0x8801
    seq = 0x12
    pan_id = 0x00D2
    dest = 0x0000
    src_pan = 0x00D2
    src = 0x143E
    mac = struct.pack("<HBH H HH", mac_fc, seq, pan_id, dest, src_pan, src) + nwk
    
    return mac

def test_live_adapter_point():
    adapter = LiveAdapter()
    
    # cmd2, analog_x10: 084902b2 -> prefix 08, code 49, 02b2 = 690 / 10 = 69.0
    ota_payload = b"\x00\x00\x02\x08\x49\x02\xb2"
    psdu = build_raw_psdu(ota_payload)
    
    rx_payload = RxFramesPayload(
        timestamp_us=1000000,
        channel=15,
        rssi_dbm=-50,
        lqi=100,
        raw_psdu=psdu
    )
    
    event = adapter.process_rx_frame(rx_payload)
    assert isinstance(event, OtaPointEvent)
    assert event.identity.short_addr == 0x143E
    assert event.prefix == 0x08
    assert event.code == 0x49
    assert event.kind == "analog_x10"
    assert event.value == 69.0

def test_live_adapter_ack():
    adapter = LiveAdapter()
    
    # cmd3, ack: 084900 -> prefix 08, code 49, ack (00)
    ota_payload = b"\x00\x00\x03\x08\x49\x00"
    psdu = build_raw_psdu(ota_payload)
    
    rx_payload = RxFramesPayload(
        timestamp_us=1000000,
        channel=15,
        rssi_dbm=-50,
        lqi=100,
        raw_psdu=psdu
    )
    
    event = adapter.process_rx_frame(rx_payload)
    assert isinstance(event, OtaAckEvent)
    assert event.prefix == 0x08
    assert event.code == 0x49
    assert event.extra_hex is None

def test_live_adapter_unknown():
    adapter = LiveAdapter()
    
    # cmd2, unknown length (5 bytes rest)
    ota_payload = b"\x00\x00\x02\x08\x49\x01\x02\x03"
    psdu = build_raw_psdu(ota_payload)
    
    rx_payload = RxFramesPayload(
        timestamp_us=1000000,
        channel=15,
        rssi_dbm=-50,
        lqi=100,
        raw_psdu=psdu
    )
    
    event = adapter.process_rx_frame(rx_payload)
    assert isinstance(event, OtaUnknownEvent)
    assert event.prefix == 0x08
    assert event.code == 0x49
