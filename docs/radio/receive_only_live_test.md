# Phase 1A/1B: Receive-Only Live Test

This document outlines the first live-radio milestone. Phase 1A is the Python
host side; Phase 1B is the nRF52840 receive-only firmware bridge. The goal is
to prove we can receive live legacy Viconics traffic and turn it into
gateway-ready thermostat events using an nRF board, without transmitting.

## Hardware Setup

1. An nRF52840 development board flashed with `firmware/nrf_vwg_bridge_rx`.
2. The board is connected via USB to the host computer running the BACgateway toolkit.
3. The board is physically close enough to the live PAN to receive frames.

## Safety Rules

- **Do not transmit.**
- **Do not start a second coordinator.** The JACE remains the sole coordinator for the live PAN (`0x00D2`).
- **Do not modify the live PAN.**

## Expected Command

Run the CLI tool to connect to the nRF bridge and stream decoded events:

```powershell
python tools\nrf_live_rx.py --port COM7 --channel 15 --limit 50
```

(Adjust `--port` to match your OS: `COM7`, `/dev/ttyACM0`, etc.)

Linux equivalent:

```bash
python tools/nrf_live_rx.py --port /dev/ttyACM0 --channel 15 --limit 50
```

## Expected Output

You should see live IEEE 802.15.4 frames parsed through the OTA decoder and emitted as canonical events:

```text
WARNING: This is a receive-only live bridge. Active TX is NOT enabled.
Connecting to COM7 on channel 15...
RX_FRAME channel=15 rssi=-62 lqi=105 len=63
Event: OtaPointEvent(identity=OtaDeviceIdentity(eui64=None, short_addr=5182, device_label=None), prefix=8, code=73, canonical_point='occupied_heat_setpoint', kind='analog_x10', value=69.0, enum_label=None)
RX_FRAME channel=15 rssi=-61 lqi=108 len=57
Event: OtaAckEvent(identity=OtaDeviceIdentity(eui64=None, short_addr=5182, device_label=None), prefix=8, code=73, canonical_point='occupied_heat_setpoint', extra_hex=None)
...
```

If the format is `--format jsonl`, both raw frame summaries and decoded events
will be JSON objects.
Unknown frames will be emitted as `OtaUnknownEvent` and must not be dropped.

See `docs/radio/nrf_firmware_bringup.md` for build and flash commands.
