# Agent Task Brief

You are working in `Reverse/bac-gateway` on a Viconics W thermostat gateway.

Goal:

```text
legacy Viconics W thermostat -> nRF52840 USB radio bridge -> Python decoder/source adapter -> BACnet/IP from Raspberry Pi
```

Read first:

- `docs/ai_agent/thermostat_nrf_bacnet_handoff.md`
- `docs/ai_agent/evidence_index.md`
- `README.md`
- `docs/radio/nrf_bridge_protocol.md`
- `docs/ota/known_ota_mappings.md`
- `docs/ota/README.md`
- `captures/manifest.yaml`

Hard constraints:

- Do not transmit on live PAN `0x00D2` or channel `15`.
- Do not start a standard Zigbee coordinator on the live Viconics network.
- Current nRF firmware is RX-only; keep TX paths absent unless explicitly doing isolated bench Phase 2+ work.
- Do not invent or auto-promote mappings.
- Keep transport ACK, MAC ACK, and vendor APS ACK distinct.

Known protocol anchors:

- channel `15`
- PAN `0x00D2`
- coordinator short `0x0000`
- thermostat shorts observed `0x0001`, `0x143E`
- DEVICE2 EUI-64 `1d:35:08:04:32:20:31:04`
- RTC EUI-64 `1d:35:08:02:07:43:61:04`
- APS profile `0xC1E4`
- cluster `0x0002`
- gateway endpoint `0x0A`
- thermostat endpoint `0x32`
- `cmd 0/1` identify
- `cmd 2/3` point data and ACK
- analog value format: `prefix | code | u16_be(value * 10)`
- enum value format: `prefix | code | 0x00 | enum`
- ACK format: `prefix | code | 0x00`

Current code state:

- nRF receive firmware exists in `firmware/nrf_vwg_bridge_rx/`.
- nRF host codec/client/live adapter exist under `gateway/radio/`.
- live RX CLI is `tools/nrf_live_rx.py`.
- OTA parser stack exists under `gateway/ota/`.
- current YAML live point map has confirmed `occupied_heat_setpoint` and `occupied_cool_setpoint`.
- BACnet server exists in `gateway/bacnet_server.py`.
- nRF OTA events are not yet wired into the BACnet `PointRegistry`.

Suggested first implementation task:

Build a narrow nRF OTA source adapter that maps `OtaPointEvent` objects into the existing BACnet point registry without changing the nRF firmware or BACnet server internals.

Acceptance idea:

```text
synthetic OtaPointEvent(canonical_point='occupied_cool_setpoint', value=69.0)
-> configured point `OccupiedCoolingSetpoint` / `0x1005` / `analogValue,1005`
-> PointRegistry runtime updates to 69.0
-> BACnet ReadProperty path sees the updated value
```

Verification:

```powershell
python -m pytest tests -q
python tools\nrf_live_rx.py --help
```
