# Implementation Prompt

Use this prompt for a future coding agent working in `Reverse/bac-gateway`.

```text
You are working in Reverse/bac-gateway on the Viconics W thermostat -> nRF52840 RX bridge -> BACnet/IP gateway.

Goal:
Build the narrow missing adapter between decoded nRF OTA events and the existing BACnet PointRegistry.

Read first:
- docs/ai_agent/thermostat_nrf_bacnet_handoff.md
- docs/ai_agent/evidence_index.md
- gateway/radio/live_adapter.py
- gateway/ota/events.py
- gateway/ota/pointmap.py
- gateway/bacnet_server.py
- gateway/model.py
- points.yaml
- tests/test_live_adapter.py

Hard constraints:
- Do not modify nRF firmware.
- Do not add TX support.
- Do not transmit on live PAN 0x00D2 or channel 15.
- Do not start a Zigbee coordinator.
- Do not add Zigbee2MQTT assumptions.
- Do not promote unconfirmed point mappings.
- Keep BACnet server unaware of OTA frame details.
- Keep transport ACK, MAC ACK, and vendor APS ACK conceptually separate.
- Use type hints and structured logging.

Implement:
1. Create a small adapter module:
   gateway/radio/ota_registry_adapter.py

2. The adapter must consume gateway.ota.events objects:
   - OtaPointEvent
   - OtaAckEvent
   - OtaUnknownEvent if useful for logging only

3. For OtaPointEvent:
   - take event.canonical_point, event.value, timestamp/source/quality metadata where available
   - map semantic OTA names to configured PointRegistry entries
   - support at minimum:
     occupied_cool_setpoint -> OccupiedCoolingSetpoint / point address 0x1005 / analogValue,1005
     occupied_heat_setpoint -> OccupiedHeatingSetpoint / point address 0x1006 if present in config
   - tolerate config aliases such as occ_cool_setpoint, occupied_cool_setpoint, OccupiedCoolingSetpoint
   - do not assume OTA prefix/code equals vendor serial point address

4. Add a small identity mapping boundary:
   - accept current short address if EUI-64 is missing
   - prefer EUI-64 when available
   - do not make short address the permanent identity
   - if no thermostat mapping exists, log and ignore without crashing

5. Integrate only enough so a caller can do:
   adapter.apply_event(event)
   and the existing PointRegistry runtime value changes.

6. Add tests:
   tests/test_ota_registry_adapter.py

Test cases:
- synthetic OtaPointEvent(canonical_point="occupied_cool_setpoint", value=69.0)
  updates the configured BACnet point for OccupiedCoolingSetpoint / 0x1005.
- unknown canonical_point is logged/ignored, not crashed.
- OtaAckEvent does not update present_value as if it were a point value.
- short address identity works as temporary fallback.
- EUI-64 identity is preferred when supplied.
- BACnet server internals do not import OTA parser modules directly.

Run:
python -m pytest tests -q
python tools/nrf_live_rx.py --help

Acceptance criteria:
- All tests pass.
- No firmware files changed.
- No TX code added.
- No new coordinator logic added.
- Existing vendor serial path still works.
- Existing BACnet server can read the updated PointRegistry value after a synthetic OTA point event.
```

## Implementation Notes for the Agent

- Keep the adapter as a caller-owned object. Do not make `bacnet_server.py` import OTA parser modules.
- Prefer matching configured points by normalized semantic names first, then Niagara names, then known point addresses.
- The initial adapter can be synchronous and in-process; no queue or background thread is required for the synthetic proof.
- ACK and unknown events are useful for logs/metrics only. They must not update a BACnet present value.
- If `occupied_heat_setpoint` is absent from the current config, ignore it cleanly and prove `occupied_cool_setpoint` first.
