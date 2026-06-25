# Thermostat to nRF to BACnet Handoff

Updated: 2026-06-25

## Goal

Build a path where legacy proprietary Viconics W thermostats are read by an nRF52/nRF52840 USB radio bridge, decoded by this Python codebase, and exposed as BACnet/IP from a Raspberry Pi.

Target end-state:

```text
Viconics W thermostat
-> IEEE 802.15.4 / Zigbee NWK / Zigbee APS vendor profile over the air
-> nRF52840 USB bridge
-> gateway/radio live adapter
-> gateway/ota decoder and point map
-> canonical point registry/model
-> gateway/bacnet_server.py
-> BACnet/IP client
```

Current implemented state is receive/decode only for the nRF path. The repo has a BACnet server and a vendor-radio serial path, but the nRF live OTA events are not yet wired into the BACnet `PointRegistry`.

## Executive Summary

This is not a normal Zigbee2MQTT integration. The captures show standard-ish lower layers but a Viconics vendor application profile:

- IEEE 802.15.4 on channel `15`
- PAN ID `0x00D2`
- Zigbee NWK and APS framing visible
- APS profile `0xC1E4`
- APS cluster `0x0002`
- endpoint pair: gateway `0x0A`, thermostat `0x32`
- observed coordinator/gateway short address: `0x0000`
- observed thermostat short addresses: `0x0001` and `0x143E`

The strongest current route is:

1. Keep nRF receive-only against the live system.
2. Prove live RX frames decode exactly like offline pcap frames.
3. Wire decoded OTA events into a source adapter that updates BACnet-visible points.
4. Only after isolated-bench gates, consider any TX work on a spare thermostat and lab-only PAN/channel.

## Confirmed Radio Facts

From `WiresharkN/viconics_w_ota_reverse_engineering_passes_1_4.md`, `WiresharkN/wireshark_passes_1_3_summary.txt`, `docs/ota/README.md`, and the OTA parser tests:

| Fact | Value |
| --- | --- |
| PHY | IEEE 802.15.4 at 2.4 GHz |
| Capture channel | `15` |
| Live PAN | `0x00D2` decimal 210 |
| Coordinator short address | `0x0000` |
| Thermostat short addresses seen | `0x0001`, `0x143E` |
| DEVICE2 EUI-64 seen in app payload | `1d:35:08:04:32:20:31:04` |
| RTC EUI-64 seen in app payload | `1d:35:08:02:07:43:61:04` |
| APS profile | `0xC1E4` |
| APS cluster | `0x0002` |
| Gateway endpoint | `0x0A` |
| Thermostat endpoint | `0x32` |
| NWK security in current captures | not observed |
| Application command IDs | `cmd 0/1` identify, `cmd 2/3` point data and ACK |

Operational rule: use EUI-64 as canonical identity when available. Short addresses can change after rejoin and should not be the long-term identity key.

## Application Protocol Shape

The application payload is ZCL-like:

```text
frame_control | optional manufacturer_code | transaction_seq | cmd_id | rest
```

The current parser handles:

- manufacturer-specific bit in frame control by skipping the 2-byte manufacturer code
- `cmd 0`: identify request
- `cmd 1`: identify response
- `cmd 2`: point data
- `cmd 3`: ACK / write response

Important decode rules from `gateway/ota/app.py`:

- `cmd 2`, rest length `4`: `prefix | code | u16_be / 10.0`, unless the point map marks `(prefix, code)` as enum.
- `cmd 2`, rest length `3`: `prefix | code | u8_value`.
- `cmd 2`, other rest lengths: preserved as `unknown`.
- `cmd 3`, rest length at least `3` and byte 3 equals `0x00`: ACK.
- `cmd 3`, short rest: preserved as `unknown` with `reason=short_rest`.
- report codes are normalized to write codes through `PointMap.canonicalize()`.

Typical write burst seen in captures:

```text
1. gateway -> thermostat: cmd 2 write
2. thermostat -> gateway: cmd 3 ACK
3. thermostat -> gateway: cmd 2 report/confirm
4. gateway -> thermostat: cmd 3 ACK of report
```

Do not confuse:

- transport ACK from the USB bridge
- 802.15.4 MAC ACK
- vendor APS ACK (`cmd 3`)

They are separate layers and must be logged/tested separately.

## Point Mappings

### Current YAML Source Used by Live Parser

`gateway/ota/pointmap.yaml` currently contains two confirmed mappings:

| Prefix | Write code | Report code | Point | Kind | Evidence |
| --- | --- | --- | --- | --- | --- |
| `0x08` | `0x49` | `0x0A` | `occupied_heat_setpoint` | `analog_x10` | Exp1 |
| `0x08` | `0x4B` | `0x2D` | `occupied_cool_setpoint` | `analog_x10` | Exp2 |

These are the mappings the live adapter can label today when using the YAML loader.

### Richer Legacy JSON Catalog

`gateway/ota/pointmap.json` still contains a broader discovered catalog:

- family `0x08` report-to-write entries including `0x0A -> 0x49`, `0x2D -> 0x4B`, `0x45 -> 0x4C`, plus multiple `0xC*`/`0xD*` report mappings.
- family `0x0A` report-to-write entries including `0xC2 -> 0x29`, `0xCA -> 0x2A`, `0xD3 -> 0x2B`, `0xDD -> 0x2C`, `0xEB -> 0x2D`.
- 77 observed point entries with labels such as `occupied_heat_setpoint`, `occupied_cool_setpoint`, `heat_setpoint_candidate_2`, `sp_*`, `occ_*`, and `auto_*`.

Important gap: the docs mention `0x08:0x4C` write to `0x08:0x45` report as an Exp3 candidate, and `pointmap.json` contains it, but `pointmap.yaml` does not currently include it. Any agent should treat that as a YAML migration/review task, not as already active in live parsing.

### Niagara / Vendor Serial Point Catalog

`spec/points_catalog.csv` has 271 rows extracted from Viconics/Niagara source material. Useful first-slice points include:

| Point address | Logical name | Type | Scale | Units |
| --- | --- | --- | --- | --- |
| `0x1000` | `RoomTemp` / `ReturnAirTemp` variants | scaled-int | 10 | Fahrenheit |
| `0x1005` | `OccCoolSetpoint` | scaled-int | 10 | Fahrenheit |
| `0x1006` | `OccHeatSetpoint` | scaled-int | 10 | Fahrenheit |
| `0x100F` | `OccCommand` | enum | 1 | none |
| `0x101B` | `EffectiveOcc` | enum | 1 | none |

Do not assume these `0x100*` vendor-radio point addresses equal OTA `(prefix, code)` pairs. They are different protocol layers. A future source adapter must map OTA semantic names to the canonical/BACnet point names.

## nRF Receive Bridge

Current firmware tree:

```text
firmware/nrf_vwg_bridge_rx/
```

Current host files:

```text
gateway/radio/nrf_bridge_proto.py
gateway/radio/nrf_bridge_client.py
gateway/radio/live_adapter.py
tools/nrf_live_rx.py
```

Transport frame:

```text
magic(2) | version(1) | type(1) | flags(1) | seq(1) | length(2) | payload | crc32(4)
```

Constants:

- magic: ASCII `VW`
- version: `1`
- CRC: standard CRC-32 over header plus payload
- `HELLO_RESP` capability string: `proto=1;name=nrf_vwg_bridge_rx;fw=0.1.0;cap=RX_ONLY`
- `RX_FRAME` payload: `timestamp_us(8 LE) | channel(1) | rssi_dbm(1 signed) | lqi(1 signed) | raw_psdu(N)`

Firmware behavior:

- uses Nordic `nrf_802154`
- default channel is `15`
- sets promiscuous receive
- streams raw length-prefixed PSDU bytes over USB CDC ACM
- no shell, no network stack, no association/join behavior
- no transmit path in source

Host behavior:

- `NrfBridgeClient` reads binary frames from serial.
- `RxFramesPayload.decode()` extracts timestamp/channel/RSSI/LQI/raw PSDU.
- `LiveAdapter.process_rx_frame()` parses MAC -> NWK -> APS -> OTA app and returns one of:
  - `OtaPointEvent`
  - `OtaAckEvent`
  - `OtaUnknownEvent`

Smoke command:

```powershell
python tools\nrf_live_rx.py --port COM7 --channel 15 --limit 50
```

Linux:

```bash
python tools/nrf_live_rx.py --port /dev/ttyACM0 --channel 15 --limit 50
```

The command currently prints raw `RX_FRAME` summaries and decoded events. It does not push those values into the BACnet server.

## BACnet Path

Current BACnet implementation:

```text
gateway/bacnet_server.py
gateway/model.py
points.yaml
spec/points_catalog.csv
spec/enums.yaml
```

`points.yaml` currently exposes one thermostat-like slice:

| Config key | Niagara name | Point address | BACnet object |
| --- | --- | --- | --- |
| `room_temperature` | `RoomTemperature` | `0x1000` | `analogInput,1000` |
| `occ_cool_setpoint` | `OccupiedCoolingSetpoint` | `0x1005` | `analogValue,1005` |
| `pi_cooling_demand` | `PICoolingDemand` | `0x10BB` | `analogInput,1099` |

The server is currently built around `PointRegistry.update_from_radio(comm_addr, point_addr, ...)`, which matches the vendor radio serial path. For nRF OTA live events, an adapter is still needed:

```text
OtaPointEvent(canonical_point='occupied_cool_setpoint', value=69.0, identity=...)
-> map semantic OTA point to configured BACnet PointRuntime
-> update PointRegistry
-> BACnet ReadProperty returns presentValue
```

Recommended next implementation boundary:

- Add a source adapter that consumes `OtaEvent` objects and updates the canonical registry.
- Keep BACnet server code unaware of nRF/OTA frame details.
- Add tests with synthetic `OtaPointEvent` and `OtaAckEvent` objects.

## Workspace Evidence Outside `Reverse/bac-gateway`

Useful top-level material:

```text
WiresharkN/
Manuals/
Reverse/vwirelessTstat_src/
Reverse/vwirelessGateway_src/
Reverse/vwirelessTstatDevices_src/
nrf-sniffer/nRF-Sniffer-for-802.15.4/
zigbee2mqtt/
vwirelessGateway.jar
vwirelessTstat.jar
vwirelessTstatDevices.jar
```

Important notes:

- `WiresharkN/` contains raw `.pcapng` captures and action logs used by the decoder and reports.
- `Manuals/planforlivecon` is the strategic source of truth for safety gates and phases.
- `Manuals/deep-research-report.md` explains why a raw nRF custom bridge is preferred over a stock Zigbee coordinator.
- `Reverse/vwirelessTstat_src/` and related source trees contain decompiled Niagara/Viconics Java classes.
- `nrf-sniffer/` contains Nordic's nRF Sniffer for 802.15.4 firmware/extcap material for passive capture.
- `zigbee2mqtt/` is useful as a bridge architecture reference only, not as a direct integration path.

## Vendor Serial Path vs nRF OTA Path

Do not mix these two:

### Track B: Vendor radio serial module

Implemented reference files:

```text
gateway/vwg_serial.py
gateway/radio/session.py
gateway/codec.py
proto/codec.py
tools/vwg_probe.py
```

Known host serial facts from decompiled sources and repo code:

- 57600 baud, 8 data bits, no parity, 1 stop bit.
- Start bytes: request `0x40` (`@`), response `0x3C` (`<`).
- Byte-sum CRC.
- RF module message types:
  - `0x0F00` start network
  - `0x0F01` configure/read network
  - `0x0F02` duplicate comm
  - `0x0F03` identify
- Decompiled defaults include 35 ms inter-message delay, 8000 ms response timeout, 3 retries.

This is the lowest-risk production fallback if an owned VWG/JACE radio module becomes available.

### Track C: nRF raw OTA path

Implemented receive-only files:

```text
firmware/nrf_vwg_bridge_rx/
gateway/radio/nrf_bridge_*.py
gateway/radio/live_adapter.py
tools/nrf_live_rx.py
```

This is a raw receive/decode path for proprietary Viconics APS frames. It is not a vendor serial module replacement yet, and it is not a coordinator yet.

## Gaps to Solve

1. Live nRF events are not wired into `PointRegistry` / BACnet output.
2. `LiveAdapter` does not currently attach EUI-64 identity from identify responses or an identity registry.
3. `pointmap.yaml` is narrower than `pointmap.json` and current docs; candidate/legacy mapping migration needs review.
4. `tools/ota_validate.py`, `ota_labelgen.py`, and `ota_pointmap_edit.py` are still JSON-oriented.
5. Room temperature OTA mapping is not confirmed in the current YAML point map.
6. Auto/release behavior is still unknown.
7. No active TX should be attempted until Phase 1 RX parity and Phase 2 nRF-to-nRF bench gates pass.

## Recommended First Agent Tasks

1. Run current tests and help commands:

```powershell
cd Reverse\bac-gateway
python -m pytest tests -q
python tools\nrf_live_rx.py --help
```

2. Build a narrow source adapter test:

```text
synthetic OtaPointEvent('occupied_cool_setpoint', value=69.0)
-> update registry point configured as OccupiedCoolingSetpoint / 0x1005 / analogValue,1005
-> BACnet object present value changes
```

3. Add a small identity registry:

```text
EUI-64 -> current short_addr -> device_label -> configured thermostat
```

4. Decide whether to migrate the richer `pointmap.json` entries into YAML using evidence rules, starting with the documented Exp3 candidate but not promoting it to confirmed without matching docs.

5. Produce a live RX parity report:

```text
nRF live RX window
parallel pcap window
same decoded OTA events
unknowns preserved, not dropped
```

## Safety Rules for Any Future TX

- Never transmit on live PAN `0x00D2` or channel `15`.
- Never create a second coordinator on the live Viconics network.
- Use a spare thermostat and lab-only PAN/channel for active tests.
- Prefer stand-alone PAN range `251-500` for lab active tests.
- Keep a second sniffer running for every thermostat-bound TX.
- If NWK/APS security or frame counters appear, stop and design key/counter handling before replay or synthesis.
- A write is not successful until vendor ACK, report/readback, and BACnet-visible state all agree.
