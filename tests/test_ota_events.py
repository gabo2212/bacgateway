from gateway.ota.events import OtaDeviceIdentity, OtaPointEvent, OtaAckEvent, OtaUnknownEvent

def test_ota_device_identity():
    ident = OtaDeviceIdentity(eui64="00:11:22:33:44:55:66:77", short_addr=0x143E)
    assert ident.eui64 == "00:11:22:33:44:55:66:77"
    assert ident.short_addr == 0x143E
    assert ident.device_label is None

def test_ota_point_event():
    ident = OtaDeviceIdentity(short_addr=0x143E)
    evt = OtaPointEvent(
        identity=ident,
        prefix=0x08,
        code=0x49,
        canonical_point="occupied_heat_setpoint",
        kind="analog_x10",
        value=72.0
    )
    assert evt.value == 72.0
    assert evt.prefix == 0x08

def test_ota_ack_event():
    ident = OtaDeviceIdentity(short_addr=0x143E)
    evt = OtaAckEvent(
        identity=ident,
        prefix=0x08,
        code=0x49,
        canonical_point="occupied_heat_setpoint",
        extra_hex="01"
    )
    assert evt.extra_hex == "01"

def test_ota_unknown_event():
    ident = OtaDeviceIdentity(short_addr=0x143E)
    evt = OtaUnknownEvent(
        identity=ident,
        prefix=0x08,
        code=0x49,
        raw_rest_hex="0849ff",
        reason="short_rest"
    )
    assert evt.reason == "short_rest"
