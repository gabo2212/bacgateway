# nRF OTA to BACnet Adapter Packet

This folder packages the next implementation handoff for the current missing link:

```text
OtaPointEvent
-> canonical/BACnet point mapping
-> PointRegistry update
-> BACnet ReadProperty
```

It is docs-only. It does not implement the adapter, change firmware, add TX, or change point mappings.

## Read Order

1. `assessment.md` - corrected current path, known facts, and why this is not JACE-first work.
2. `implementation_prompt.md` - agent-ready prompt for implementing the adapter.
3. `milestones.md` - proof ladder from synthetic registry update to later isolated TX work.
4. Existing parent docs:
   - `../thermostat_nrf_bacnet_handoff.md`
   - `../evidence_index.md`
   - `../../radio/nrf_bridge_protocol.md`
   - `../../ota/known_ota_mappings.md`

## Hard Constraints

- Do not modify nRF firmware.
- Do not add TX support.
- Do not transmit on live PAN `0x00D2` or channel `15`.
- Do not start a Zigbee coordinator.
- Do not add Zigbee2MQTT assumptions.
- Do not promote unconfirmed point mappings.
- Keep BACnet server internals unaware of OTA frame details.
- Keep transport ACK, MAC ACK, and vendor APS ACK conceptually separate.

## Path Correction

The current nRF hardware path is:

```text
legacy Viconics W thermostat
-> proprietary Viconics Zigbee/APS OTA traffic
-> nRF52/nRF52840 USB RX bridge
-> gateway/radio/live_adapter.py
-> gateway/ota decoder
-> future nRF OTA source adapter
-> PointRegistry / model
-> gateway/bacnet_server.py
-> BACnet/IP client
```

The JACE Ethernet bridge remains a safe fallback/proof path, but it is not the next implementation step for the nRF USB radio path.
