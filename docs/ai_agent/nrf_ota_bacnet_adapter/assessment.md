# Read Assessment

## Correct Current Path

The current source of truth has shifted from the older JACE-first plan to the nRF OTA receive path:

```text
legacy Viconics W thermostat
-> over-the-air proprietary Viconics Zigbee/APS traffic
-> nRF52/nRF52840 USB RX bridge
-> gateway/radio/live_adapter.py
-> gateway/ota decoder
-> NEW nRF OTA source adapter
-> PointRegistry / model
-> gateway/bacnet_server.py
-> BACnet/IP client
```

The nRF receive/decode pieces exist, but decoded nRF live OTA events are not wired into the BACnet `PointRegistry` yet. That is the current missing link.

## Important Correction

The older JACE Ethernet bridge plan is still useful as a safe fallback/proof path. With the current nRF USB hardware, the real next step for:

```text
thermostat -> your radio path -> your code -> BACnet from Pi
```

is not `JaceEthernetClient`.

It is:

```text
OtaPointEvent -> canonical/BACnet point mapping -> PointRegistry update -> BACnet ReadProperty
```

The nRF firmware is currently RX-only, so this proves passive live mirroring first. It does not yet prove full replacement of the JACE coordinator, because polling, writing, joining, and active control require a safe TX/coordinator path later.

Hard rule: do not transmit on live PAN `0x00D2` or channel `15`.

## Known OTA Facts

The Viconics traffic is not standard Zigbee2MQTT-ready. The captures show:

| Item | Value |
| --- | --- |
| Channel | `15` |
| PAN | `0x00D2` |
| Coordinator short | `0x0000` |
| Thermostat shorts | `0x0001`, `0x143E` |
| APS profile | `0xC1E4` |
| Cluster | `0x0002` |
| Gateway endpoint | `0x0A` |
| Thermostat endpoint | `0x32` |
| Commands | `cmd 0/1` identify, `cmd 2/3` point data + ACK |

Use EUI-64 as the stable identity when available. Short addresses are volatile and may be used only as temporary cache/evidence.

## Best Next Implementation Step

Build a narrow adapter, preferably:

```text
gateway/radio/ota_registry_adapter.py
```

Alternative if the codebase later introduces source adapters:

```text
gateway/sources/nrf_ota_source.py
```

Its job:

```text
OtaPointEvent(canonical_point="occupied_cool_setpoint", value=69.0)
-> map to configured BACnet/model point
-> update PointRegistry
-> BACnet ReadProperty returns 69.0
```

Do not:

- change the nRF firmware
- put nRF/OTA parsing inside `bacnet_server.py`
- add TX
- promote unconfirmed mappings

The confirmed YAML point map currently supports only:

```text
occupied_heat_setpoint
occupied_cool_setpoint
```

Start with those for nRF-first proof. `roomTemp` exists in the vendor/Niagara point catalog, but its OTA mapping is not confirmed in the current YAML map.

## Local vs Public Repo State

The local handoff/docs are ahead of the public GitHub repository state.

Verified on 2026-06-25, the public `gabo2212/bacgateway` repository still appears to be an older/simple Python repo with root folders such as `.vscode`, `config`, `gateway`, `proto`, `spec`, `tests`, and `tools`, plus README instructions focused on the serial VWG probe/gateway path. Reference: <https://github.com/gabo2212/bacgateway>.

Treat the local `Reverse/bac-gateway/docs/ai_agent/` handoff files as the richer current working context.

## Path Naming Note

The active editor tab may show `gateway/ota/point_map.yaml`, but the actual current local source path is:

```text
gateway/ota/pointmap.yaml
```

Use `pointmap.yaml` unless the repo later adds a different file intentionally.
