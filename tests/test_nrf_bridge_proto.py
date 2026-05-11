from gateway.radio.nrf_bridge_proto import NrfFrame, FrameType, RxFramesPayload
import pytest
import struct

def test_nrf_frame_encode_decode():
    payload = b"test_payload"
    frame = NrfFrame(frame_type=FrameType.HELLO_RESP, flags=0x01, seq=42, payload=payload)
    encoded = frame.encode()
    
    decoded = NrfFrame.decode(encoded)
    assert decoded is not None
    assert decoded.frame_type == FrameType.HELLO_RESP
    assert decoded.flags == 0x01
    assert decoded.seq == 42
    assert decoded.payload == payload

def test_nrf_frame_invalid_crc():
    payload = b"test_payload"
    frame = NrfFrame(frame_type=FrameType.HELLO_RESP, flags=0x01, seq=42, payload=payload)
    encoded = frame.encode()
    
    # Corrupt payload
    corrupted = bytearray(encoded)
    corrupted[9] = corrupted[9] ^ 0xFF
    
    with pytest.raises(ValueError, match="CRC mismatch"):
        NrfFrame.decode(bytes(corrupted))

def test_nrf_frame_too_short():
    assert NrfFrame.decode(b"short") is None

def test_rx_frames_payload_encode_decode():
    payload = RxFramesPayload(
        timestamp_us=1234567890,
        channel=15,
        rssi_dbm=-65,
        lqi=100,
        raw_psdu=b"\x01\x02\x03\x04"
    )
    encoded = payload.encode()
    decoded = RxFramesPayload.decode(encoded)
    
    assert decoded.timestamp_us == payload.timestamp_us
    assert decoded.channel == payload.channel
    assert decoded.rssi_dbm == payload.rssi_dbm
    assert decoded.lqi == payload.lqi
    assert decoded.raw_psdu == payload.raw_psdu
