# Expert Council Report: Viconics / nRF / BACnet Live Connect

**Date:** 2026-06-25
**Scope:** Defensible technical path to live BACnet/IP mirroring of legacy Viconics W thermostats via nRF52840 USB receive bridge.

---

## 1. Executive Conclusion

### Is the current plan accurate?

**Yes, with one critical gap.** The plan is technically sound and the protocol reverse-engineering work is well-grounded. The firmware, host codec, OTA parser, and BACnet server layers all exist. The only missing link is a source adapter that routes decoded `OtaPointEvent` objects into the `PointRegistry`, which then makes values available to BACnet ReadProperty clients. Everything else — the radio, the USB bridge framing, the MAC/NWK/APS parser stack, the point map YAML, and the BACnet server — is already implemented and tested.

### Which live-connect path is most realistic now?

**Path A: nRF RX passive mirror.** The nRF hardware is physically on the live network. It is already receiving frames on channel 15, PAN 0x00D2. The host stack already decodes MAC → NWK → APS → OTA application events. The single missing code unit is the `OtaRegistryAdapter`. Once that adapter is written and wired into `tools/nrf_live_rx.py`, passive live mirroring of `occupied_heat_setpoint` and `occupied_cool_setpoint` is achievable with no firmware changes and no TX.

### What should be done next?

**Write `gateway/radio/ota_registry_adapter.py` and a test that proves the synthetic → BACnet ReadProperty chain.** This is a pure Python task, isolated from firmware, BACnet internals, and the live radio. It unblocks every subsequent proof step. See Section 8 for the exact coding-agent prompt.

---

## 2. "Live Connect" Defined in Three Levels

### Level 1: Passive RX Live Mirror

**Definition:** The nRF52840 passively receives 802.15.4 frames on the live network, decodes Viconics APS application payloads, and updates BACnet AnalogValue/AnalogInput `presentValue` objects on the Pi — without transmitting a single bit.

**Feasibility:** High. All hardware and nearly all code exist. One adapter module is missing.

**Blockers:**
1. `OtaPointEvent` → `PointRegistry` adapter not yet written (pure code, ~80–150 lines Python).
2. `pointmap.yaml` only has 2 confirmed point mappings (heat setpoint, cool setpoint). Room temperature OTA mapping not yet confirmed in YAML.
3. `LiveAdapter` does not yet maintain an EUI-64 identity registry for matching events to configured thermostat objects.

**Proof criteria:**
- Run `tools/nrf_live_rx.py --port /dev/ttyACM0 --channel 15` in one terminal.
- Run BACnet client (bacpypes3 console or YABE) in another.
- Set thermostat setpoint up/down from the physical device.
- BACnet ReadProperty on `analogValue,1005` (OccupiedCoolingSetpoint) returns the new value within one report cycle.
- Cross-check decoded value against a parallel Wireshark capture of the same frame.

**Important limitation:** This level proves read-only mirroring of spontaneous reports from the thermostats. It does NOT prove polling (the JACE polls; thermostats may not report all points spontaneously). Some points may only change values when the JACE sends a write request and the thermostat reports back. The passive mirror will see those report frames if it is in promiscuous receive mode.

---

### Level 2: JACE Ethernet / BACnet Mirror

**Definition:** Read thermostat point values by querying the existing JACE over its Ethernet port using BACnet/IP (or Niagara FOX protocol), without any radio hardware.

**Feasibility:** Medium-low in practice. The JACE does expose its points as BACnet/IP objects if the BACnet driver is enabled and configured. However:

- The JACE's BACnet driver must be enabled and its local device instance must be set (many deployments leave this unconfigured unless a BAS client was expected).
- The thermostat points are subdevices behind the WirelessTstatNetwork driver — whether they appear on the BACnet network depends on the JACE export table configuration.
- The FOX protocol (Niagara native protocol, TCP port 4911) is available but requires Niagara Workbench client software or a compatible library. No readily available open Python FOX client exists.
- The `/ord` HTTP endpoint used in the existing `gateway/demo_a2_mirror.py` demo is specific to older Niagara AX stations that have the HTTP/ORD module enabled. Not guaranteed on all configurations.
- **Hard constraint: do not open, modify, or extract parts from the borrowed JACE.**

**Blockers:**
- Requires BACnet/IP routing enabled on the JACE (Niagara AX: `Routing_Enabled = true` on the BacnetNetwork IpPort component).
- Requires thermostat points to be in the JACE export table or discovered as BACnet sub-devices.
- Requires network access to the JACE IP address.
- Requires knowing the JACE local device BACnet instance number (can be discovered with a WhoIs broadcast).

**Proof criteria:**
- `python -m bacpypes3 --address <pi-ip>/24` console.
- `whois` broadcast; observe JACE device instance appear.
- `rpm <jace-ip> device,<instance> object-list` returns list including thermostat-named objects.
- `read <jace-ip> analog-value,1005 present-value` returns thermostat setpoint.

**Safe approach:** Attempt a WhoIs from a read-only BACpypes3 console before assuming the JACE is BACnet-accessible. No writes. No FOX authentication needed for read-only BACnet.

---

### Level 3: True Radio Replacement

**Definition:** The nRF52840 (or a second nRF52840) acts as the Viconics wireless gateway coordinator, fully replacing the VWG/JACE serial radio module. It joins thermostats to a new PAN, polls setpoints, writes setpoints, and manages addressing — all without the JACE.

**Feasibility:** Low / research phase only. Significant unknowns:
- Joining behavior: how the thermostat expects to be joined to a coordinator (VWG serial messages `0x0F00` start network, `0x0F03` identify are known from decompiled sources, but these are the host→serial-module commands, not the OTA Zigbee join sequence).
- Coordinator impersonation: the live network has coordinator short `0x0000`. A new coordinator on the same PAN would cause conflicts. **Must use isolated lab PAN and channel.**
- Frame counters: if Zigbee NWK security is ever activated on these devices (not observed in current captures, but possible), replay protection would block a new coordinator.
- Identify/join sequence: cmd 0/1 identify frames are seen in captures; the exact join handshake to get a thermostat into a fresh PAN is not yet known.

**Blockers (each a separate gate):**
1. Joining: unknown. How does a thermostat join a new coordinator? Does it use standard Zigbee association or vendor-specific pairing?
2. Network formation: nRF52840 as 802.15.4 coordinator via Zephyr `nrf_802154` driver is feasible at PHY/MAC level, but Zigbee NWK coordinator role requires stack code not currently implemented.
3. Addressing: coordinator must assign short addresses (NWK address assignment in association response).
4. Route discovery: mesh topology requires NWK route request/reply; unknown if thermostats use mesh or star.
5. Identify: cmd 0/1 frames are observed but the exact vendor identify sequence for joining/commissioning is not fully documented.
6. Read point: passive RX proves reporting; active read-request format is not confirmed (is it a cmd 2 write to a "read" code, or a separate command?).
7. Write point: known from decompiled source (cmd 2 write frame structure confirmed).
8. ACK / report confirmation: cmd 3 format confirmed. But must handle report-then-ACK cycle correctly.
9. Link quality / heartbeat: unknown interval; losing heartbeat may trigger thermostat to stop reporting.

**Proof criteria:** None achievable safely on the live network. All Phase 3 work must be on isolated bench PAN with a spare thermostat.

---

## 3. Evidence Table

| Claim | Source / Reference | Confidence | Implication |
|---|---|---|---|
| Viconics W uses IEEE 802.15.4 at 2.4 GHz / ZigBee-based stack | [FCC filing V95-VWG-APP](https://fcc.report/FCC-ID/V95-VWG-APP/1119404.pdf); [SE product catalog 2013](https://iportal2.schneider-electric.com/Contents/docs/VICONICS_2013_PRODUCT_CATALOG.PDF) | High | PHY layer confirmed. Standard 802.15.4 promiscuous receive is the right capture approach. |
| Viconics W ("W" suffix) uses *proprietary* ZigBee, not ZigBee Pro | [SE SE7000 product comparison](https://www.scribd.com/document/204937261/SE7000-Series-Product-comparison-guide-pdf): "The wireless gateway corresponds to Room Controllers using proprietary ZigBee wireless (W) communications only"; [SE room controller datasheet](https://iportal2.schneider-electric.com/Contents/docs/SDS-SE7000-VCM7000-A4.PDF): "Proprietary version of ZigBee not compatible with SmartStruxure Lite solution" | High | Zigbee2MQTT, ZHA, and any standard Zigbee coordinator will NOT work. |
| APS profile 0xC1E4, cluster 0x0002, endpoints 0x0A / 0x32 | Project captures, `WiresharkN/viconics_w_ota_reverse_engineering_passes_1_4.md` (local project file) | High (from captures) | Vendor-specific application layer. No public documentation of this profile ID exists. |
| cmd 2 / cmd 3 point data and ACK format decoded | Project OTA parser tests, `gateway/ota/app.py`, `tests/test_ota_app_decode.py` (local) | High | Parser is tested against real pcap-derived PSDUs. |
| occupied_heat_setpoint and occupied_cool_setpoint confirmed mappings | `gateway/ota/pointmap.yaml`, Exp1 / Exp2 action-window evidence (local) | High | These two points are safe to expose via BACnet today. |
| VWG serial interface: 57600 baud, 0x40/0x3C framing, byte-sum CRC | Decompiled `BWirelessTstatNetwork.java`, `BWirelessTstatSerialHelper.java` (local Reverse/) | High | Serial path (Track B) is implementable if an owned VWG radio module is obtained. |
| JACE Niagara 4 supports BACnet/IP client/server on Ethernet | [Tridium N4 PICS](https://www.alvasys.ch/files/v200708104823/shop/JACE-8000-CSE-002_PICS.PDF) | High | JACE CAN expose points as BACnet/IP if the driver is configured. |
| JACE uses FOX protocol (TCP 4911) internally for N4→Supervisor; BACnet/IP routing must be explicitly enabled | [Reddit BuildingAutomation thread](https://www.reddit.com/r/BuildingAutomation/comments/1eai522/n4_server_exposing_bacnet_data/) | High | WhoIs → Discover is the right approach before assuming BACnet is available. |
| VWG-50 has Ethernet (RJ-45) and exposes BACnet/IP or BACnet MS/TP directly | [SE VWG-50 install guide](https://iportal2.schneider-electric.com/Contents/docs/LIT-VWG-50%20INSTALL.PDF) | High | The VWG-50 (newer standalone gateway) is Ethernet-native BACnet. The older VWG-APP-1045 is JACE-serial only. |
| SE8000 ZigBee Pro models (P suffix) partially work with Zigbee2MQTT | [Z2M issue #13345](https://github.com/Koenkk/zigbee2mqtt/issues/13345) — SE8300U with Zigbee *add-on card* (ZigBee Pro) joined z2m and endpoints 10/14 visible | Medium | This is ZigBee Pro (P suffix), NOT the proprietary W suffix. Completely different stack. Not applicable. |
| nRF52840 supports raw IEEE 802.15.4 TX/RX via `nrf_802154` driver under Zephyr | [Nordic DevZone Q&A](https://devzone.nordicsemi.com/f/nordic-q-a/128001/raw-802-15-4-tx-rx-on-nrf52840-with-ncs-v3-2-3-what-is-the-supported-approach); Zephyr `CONFIG_IEEE802154_RAW_MODE` | High | A future active coordinator firmware is buildable on the same nRF52840 hardware. |
| BACpypes3 supports multiple `Application` instances on one host (different UDP ports) | [BACpypes3 `multiple-stacks.py`](https://raw.githubusercontent.com/JoelBender/BACpypes3/main/samples/multiple-stacks.py); `multiple-stacks.json` | High | One Pi can expose one BACnet device per thermostat, each on a different port, from one Python process. |
| BACpypes3 supports IP-to-VLAN gateway / router pattern | [BACpypes3 `ip-to-vlan.py`](https://raw.githubusercontent.com/JoelBender/BACpypes3/main/samples/ip-to-vlan.py); `VirtualNetwork` class | High | Gateway-style architecture (one upstream IP device, multiple downstream virtual devices) is implementable. |
| No open-source project implements Viconics APS 0xC1E4 / cluster 0x0002 | GitHub search — no results for 0xC1E4 APS profile; gabo2212/bacgateway is the only relevant repo (this project) | High (absence of evidence) | This project is novel. No reference implementation exists. |
| GW2 (Schneider standalone BACnet/IP wireless gateway) uses ZigBee Pro, not the legacy W protocol | [GW2 user guide](https://iportal2.schneider-electric.com/Contents/docs/028-0465-00-USER-INTERFACE-GUIDE-GW2.PDF): "Stack profile to ZigBee Pro, Security Profile to Home Automation" | High | GW2 is the ZigBee Pro path (P-suffix devices). Incompatible with legacy W-suffix VT7xxx thermostats. |
| Live nRF OTA events are NOT yet wired into PointRegistry | `assessment.md`, `thermostat_nrf_bacnet_handoff.md` (local project files) | Certain | This is the exact missing link to implement. |
| nRF firmware is RX-only; no TX path in source | `firmware/nrf_vwg_bridge_rx/prj.conf`, `nrf_bridge_proto.py` HELLO_RESP cap string "RX_ONLY" (local) | Certain | Safe. No inadvertent TX possible with current firmware. |

---

## 4. Ranked Implementation Paths

### Path A: nRF RX Passive Mirror

**Feasibility:** 9/10
**Risk:** 1/10
**Time-to-proof:** 1–3 days (pure Python code)
**Hardware needed:** nRF52840 USB dongle (already present), Raspberry Pi
**Code needed:**
- `gateway/radio/ota_registry_adapter.py` (~80–150 lines)
- Wire adapter into `tools/nrf_live_rx.py` main loop
- Update `gateway/bacnet_server.py` to accept OTA adapter as a source (or add a simple asyncio loop)
- Test: `tests/test_ota_registry_adapter.py` with synthetic `OtaPointEvent`

**Safety constraints:** No TX. Promiscuous RX only. No firmware change.

**Why it may fail:**
- Thermostats only report setpoints when the JACE polls or after a user interaction. Without the JACE polling, the passive mirror only captures spontaneous thermostat reports. Interval is unknown (typically minutes). The BACnet `presentValue` will be stale between reports.
- EUI-64 identity not yet implemented in `LiveAdapter` — short-address-based matching required until identify frames are decoded.
- `pointmap.yaml` covers only 2 points. Additional semantic point names (room temp, occupancy) not yet confirmed in YAML → they will appear as `unknown` events, not BACnet points.

---

### Path B: JACE Ethernet / BACnet Read-Only

**Feasibility:** 5/10 (depends on JACE BACnet configuration, which may not be enabled)
**Risk:** 2/10 (read-only, no hardware modification)
**Time-to-proof:** 1–2 days if JACE BACnet is already configured; indeterminate if not
**Hardware needed:** Ethernet access to the JACE IP address
**Code needed:**
- None required beyond existing `gateway/demo_a2_mirror.py` / `configs/demo_a2.yaml`
- A WhoIs sweep using bacpypes3 console from the Pi

**Safety constraints:**
- Read-only operations only.
- Do not open or modify the JACE.
- Do not write any BACnet objects.
- Do not attempt FOX protocol without a proper client library.

**Why it may fail:**
- The JACE BACnet/IP routing may not be enabled (default in many Niagara AX installs is routing disabled, device instance -1 = invalid).
- Even if the JACE appears as a BACnet device, the thermostat sub-points may not be in the export table (they default to not exported unless explicitly configured).
- If the JACE is on a subnet with no broadcast path to the Pi, a directed WhoIs to the JACE IP is required.
- The borrowed JACE must remain unmodified — if the BACnet driver is unconfigured, this path is blocked without touching the JACE.

**Recommended first action:** `python -m bacpypes3 --address <pi-ip>/24` → `whois` → observe if JACE appears. If yes, proceed. If no, move to Path A.

---

### Path C: Vendor Radio Serial Module

**Feasibility:** 7/10 (with owned hardware)
**Risk:** 3/10
**Time-to-proof:** 1–2 weeks (obtaining hardware + integration)
**Hardware needed:** An owned VWG serial radio module (VWG-APP-1045 or equivalent) with RS-232 null modem; OR a VWG-50 with Ethernet if available.
**Code needed:**
- `gateway/vwg_serial.py`, `gateway/radio/session.py`, `gateway/codec.py` already exist.
- Would need to wire `session.py` session responses into `PointRegistry`.

**Safety constraints:**
- Do not use on the live PAN/channel without verifying the module is in a passive listen state first.
- Serial commands `0x0F00` (start network) would try to form a new PAN — only run on a lab PAN.
- Decompiled source confirms 57600 baud, 35ms inter-message delay, 3 retries — use exactly those defaults.

**Why it may fail:**
- Hardware not currently available. This path is blocked until an owned VWG radio module is obtained.
- The serial module may use a firmware version that doesn't match the decompiled defaults.
- Serial CRC errors will silently drop frames; careful CRC testing required first.

---

### Path D: nRF Active Replacement / Custom Coordinator

**Feasibility:** 3/10 (for full replacement)
**Risk:** 6/10
**Time-to-proof:** 4–8 weeks minimum (isolated bench only)
**Hardware needed:** 2× nRF52840 USB dongles (one as coordinator, one as sniffer), isolated 2.4 GHz lab environment, spare Viconics thermostat
**Code needed:**
- New nRF coordinator firmware using Zephyr `nrf_802154` or ZBOSS (raw 802.15.4 coordinator role)
- Custom 802.15.4 MAC association responder (coordinator duty, send association response with short address assignment)
- Zigbee NWK layer: network formation, routing table, NWK layer address assignment
- Viconics APS application layer: cmd 0/1 identify sequence, cmd 2/3 point data/ACK
- All on isolated lab PAN (PAN range 251–500 per existing docs)

**Safety constraints:**
- **Absolutely no TX on live PAN 0x00D2 or channel 15.**
- All TX work on isolated bench PAN and channel only.
- Second sniffer must be running for every TX test.
- If NWK security frame counters appear, stop and design key/counter handling.

**Why it may fail:**
- The join sequence for a Viconics W thermostat onto a new coordinator is not documented. The thermostat may use vendor-specific pairing (not standard Zigbee association).
- NWK security may be latent in the firmware but not activated in current captures. A new coordinator attempting to form a network may trigger security rejections.
- Frame counter state: if the thermostat has stored frame counter state for its previous coordinator, a new coordinator's counter may be rejected.
- Zigbee coordinator role on nRF52840 via ZBOSS requires proper licensing and is not trivial to implement raw.

---

### Path E: Zigbee2MQTT / Open Coordinator

**Feasibility: NOT APPLICABLE / 0/10**

**No proof exists** that Zigbee2MQTT supports the legacy Viconics "W" (proprietary ZigBee) protocol. The GitHub issue [#13345](https://github.com/Koenkk/zigbee2mqtt/issues/13345) involves an SE8300U with a ZigBee *Pro* (P-suffix) add-on card — a completely different protocol stack. The SE7xxx/VT7xxx "W" devices use APS profile 0xC1E4, which is a vendor-specific profile unknown to Zigbee2MQTT. The SE7000 product comparison sheet explicitly states the W variant is "not compatible with SmartStruxure Lite solution" (the standard ZigBee Pro ecosystem). **Do not attempt Zigbee2MQTT with W-suffix devices.**

---

## 5. Concrete Experiment Plan

### Experiment 1: Synthetic OtaPointEvent → PointRegistry → BACnet Read Proof

**Purpose:** Prove the full chain from OTA event to BACnet value without any live radio.

**Files to create/modify:**
```
gateway/radio/ota_registry_adapter.py  (new)
tests/test_ota_registry_adapter.py     (new)
```

**Test code (acceptance criteria):**
```python
# test_ota_registry_adapter.py
from gateway.ota.events import OtaPointEvent
from gateway.radio.ota_registry_adapter import OtaRegistryAdapter
from gateway.bacnet_server import PointRegistry  # or wherever the registry is
from gateway.model import load_config

config = load_config("points.yaml")
registry = PointRegistry(config)
adapter = OtaRegistryAdapter(registry, config)

event = OtaPointEvent(
    canonical_point="occupied_cool_setpoint",
    value=69.0,
    eui64="1d:35:08:02:07:43:61:04",
    short_addr=0x0001,
    seq=42
)
adapter.on_ota_event(event)

# BACnet object analogValue,1005 (OccupiedCoolingSetpoint) should now read 69.0
runtime = registry.get_point_runtime("occ_cool_setpoint")
assert abs(runtime.present_value - 69.0) < 0.001
```

**Pass criteria:** Test passes with `python -m pytest tests/test_ota_registry_adapter.py -v`.

**Logs to collect:** pytest stdout showing assertion pass. Optionally: BACnet YABE screenshot of analogValue,1005 present value.

---

### Experiment 2: Live nRF RX Proof

**Purpose:** Confirm the nRF USB device receives live OTA frames on channel 15, PAN 0x00D2.

**Command:**
```bash
python tools/nrf_live_rx.py --port /dev/ttyACM0 --channel 15 --limit 50
```

**Expected output (pass):**
```
RX_FRAME ts=... ch=15 rssi=-70 lqi=... len=...
  -> OtaPointEvent(canonical_point='occupied_heat_setpoint', value=72.0, short_addr=0x0001)
RX_FRAME ts=... ch=15 rssi=-68 lqi=... len=...
  -> OtaAckEvent(short_addr=0x0001, seq=...)
RX_FRAME ts=... ch=15 rssi=... len=...
  -> OtaUnknownEvent(reason='...', short_addr=0x143E)
```

**Pass criteria:**
- At least 1 `OtaPointEvent` with a non-None `canonical_point` is decoded within a 5-minute observation window.
- No Python exceptions or CRC errors.
- At least 1 frame from both known short addresses (0x0001 and 0x143E) OR matching EUI-64 values.

**Fail criteria:** Zero decoded events after 15 minutes → check USB CDC ACM device enumeration, confirm channel is 15, confirm firmware HELLO_RESP reports `cap=RX_ONLY`.

**Logs to collect:** Full `--limit 50` output to a text file for parity check.

---

### Experiment 3: Live BACnet Mirror Proof

**Purpose:** Confirm that after implementing the adapter, a BACnet client can read live thermostat data.

**Pre-condition:** Experiment 1 passed (adapter implemented and tested).

**Setup:**
- Terminal 1: `python gateway/bacnet_server.py --config points.yaml`
- Terminal 2: `python tools/nrf_live_rx.py --port /dev/ttyACM0 --channel 15 --bacnet-push`
- Terminal 3: BACpypes3 console or YABE

**Command (BACpypes3 console):**
```
> read <pi-ip> analog-value,1005 present-value
```

**Pass criteria:**
- Before thermostat interaction: `present-value` may show configured default or last-known value.
- After changing thermostat cool setpoint by 2°F on physical device: `present-value` updates within one report cycle (typically 30–120 seconds).
- Value matches the decoded `OtaPointEvent` value from Terminal 2 log.

---

### Experiment 4: Parity Proof Against Offline pcap Decode

**Purpose:** Confirm live nRF RX produces bit-for-bit identical decoded events compared to offline pcap decode of the same frame.

**Setup:**
- Run Nordic nRF Sniffer for 802.15.4 (Wireshark extcap) and `tools/nrf_live_rx.py` simultaneously on the same channel.
- Wait for a setpoint change event.

**Command:**
```bash
python tools/ota_extract.py --input <wireshark_capture.pcapng> --format jsonl > pcap_events.jsonl
python tools/nrf_live_rx.py --port /dev/ttyACM0 --channel 15 --limit 20 --jsonl > live_events.jsonl
diff <(python tools/compare_events.py pcap_events.jsonl) <(python tools/compare_events.py live_events.jsonl)
```

**Pass criteria:** The `canonical_point`, `value`, `short_addr`, and approximate timestamp of the setpoint change event match between pcap and live RX within one frame sequence.

---

### Experiment 5 (Optional): JACE Ethernet Read-Only Proof

**Purpose:** Determine if the JACE exposes thermostat points via BACnet/IP.

**Command (read-only, no writes):**
```bash
python -m bacpypes3 --address <pi-ip>/24
> whois
```

**Pass criteria:** JACE device instance appears in WhoIs response. Then:
```
> rpm <jace-ip> device,<instance> object-list
```
Look for objects named after thermostats. Then:
```
> read <jace-ip> analog-value,<instance> present-value
```

**Fail criteria:** No WhoIs response from JACE IP → BACnet routing not enabled on JACE. Do not attempt to modify JACE to fix this.

---

## 6. Architecture Recommendation

### Required Module Boundaries

```
┌─────────────────────────────────────────────────────────────────┐
│  nRF52840 USB firmware (firmware/nrf_vwg_bridge_rx/)            │
│  RX-only: nrf_802154 promiscuous receive → USB CDC ACM binary   │
└────────────────────────────┬────────────────────────────────────┘
                             │ VW binary frames
┌────────────────────────────▼────────────────────────────────────┐
│  gateway/radio/nrf_bridge_client.py                             │
│  gateway/radio/nrf_bridge_proto.py                             │
│  Deserialization of VW frames → RxFramesPayload                 │
└────────────────────────────┬────────────────────────────────────┘
                             │ raw PSDUs
┌────────────────────────────▼────────────────────────────────────┐
│  gateway/radio/live_adapter.py                                  │
│  MAC → NWK → APS → OTA app decode                              │
│  Emits: OtaPointEvent | OtaAckEvent | OtaUnknownEvent           │
└────────────────────────────┬────────────────────────────────────┘
                             │ OtaEvent objects
┌────────────────────────────▼────────────────────────────────────┐
│  ** NEW ** gateway/radio/ota_registry_adapter.py                │
│  Maps OtaPointEvent.canonical_point → configured PointRuntime   │
│  Calls PointRegistry.update(point_key, value, source=OTA)       │
│  Maintains EUI-64 → thermostat identity cache                   │
│  Does NOT know BACnet object details                            │
└────────────────────────────┬────────────────────────────────────┘
                             │ PointRegistry updates
┌────────────────────────────▼────────────────────────────────────┐
│  gateway/bacnet_server.py + gateway/model.py                    │
│  PointRegistry: holds PointRuntime objects                      │
│  BACnet Application: serves ReadProperty from PointRuntime      │
│  Does NOT know OTA frame details, radio protocol, or EUI-64     │
└────────────────────────────┬────────────────────────────────────┘
                             │ BACnet/IP (UDP 47808)
┌────────────────────────────▼────────────────────────────────────┐
│  BMS / Workbench / YABE                                         │
└─────────────────────────────────────────────────────────────────┘
```

### Swappable Source Adapters (future)

Define a base class or protocol:

```python
class SourceAdapter(Protocol):
    def on_ota_event(self, event: OtaEvent) -> None: ...
    def start(self) -> None: ...
    def stop(self) -> None: ...
```

Current: `OtaRegistryAdapter` (nRF RX path)
Future: `JaceEthernetAdapter` (BACnet/IP poll from JACE), `VwgSerialAdapter` (serial radio module)

All adapters write to the same `PointRegistry`. The `bacnet_server.py` does not need to know which adapter is active.

### BACnet Device Model Options

BACpypes3 provides two well-tested patterns:

**Option 1: One BACnet device per thermostat (recommended for BMS discovery)**
```json
// multiple-stacks.json pattern
[
  { "object-identifier": "device,1001", "object-name": "Thermostat-DEVICE2",
    "bacnet-ip-udp-port": 47808 },
  { "object-identifier": "device,1002", "object-name": "Thermostat-RTC",
    "bacnet-ip-udp-port": 47809 }
]
```
- Each thermostat appears as a separate BACnet device on the BMS.
- Each uses a different UDP port on the same Pi IP.
- Confirmed by `multiple-stacks.py` sample.
- BMS discovers each thermostat individually.

**Option 2: One gateway device with all thermostat objects**
```
device,9000 "ViconicsGateway"
  analogValue,1005 OccupiedCoolingSetpoint [thermostat 1]
  analogValue,2005 OccupiedCoolingSetpoint [thermostat 2]
  analogInput,1000 RoomTemperature [thermostat 1]
```
- Simpler to implement initially.
- All objects under one BACnet device.
- Object instance numbering must encode thermostat identity (e.g., `1000 + thermostat_index * 100 + point_offset`).

**Option 3: Configurable (recommended long-term)**
- YAML config declares per-thermostat device mode vs. gateway mode.
- Start with Option 2 for the first working proof; migrate to Option 1 for production.

---

## 7. Red Flags

1. **Treating passive RX as full replacement:** The JACE is the coordinator on this network. It sends writes to thermostats on a poll cycle (typically every 30 seconds to 5 minutes for setpoints). The thermostats report back in response to those writes. A passive mirror will only see spontaneous reports — which may be infrequent or absent for some point types. Do not claim the passive mirror fully replicates JACE behavior.

2. **Confusing the ZigBee Pro (P suffix) support with W suffix:** The Zigbee2MQTT issue #13345 involves the SE8300U with a ZigBee Pro add-on card. That device joins standard Zigbee networks. The legacy W-suffix VT7xxx thermostats do NOT use ZigBee Pro. Attempting Zigbee2MQTT on a W-suffix thermostat will silently fail — the device will never join the Z2M coordinator.

3. **Promoting unconfirmed point mappings:** The `pointmap.json` has 77 entries; `pointmap.yaml` has 2 confirmed. Any mapping not explicitly proven by action-window A/B evidence should stay in JSON only. Promoting uncertain mappings will cause the BACnet server to emit incorrect values.

4. **Short address volatility:** Thermostat short addresses (0x0001, 0x143E) can change after a rejoin. Using short address as the long-term device key in the registry will cause mis-routing if a thermostat rejoins with a different short address. Use EUI-64 as the primary key; short address only as a lookup cache.

5. **ACK layer confusion:** There are three distinct ACK layers:
   - USB transport ACK (VW bridge frame acknowledged)
   - 802.15.4 MAC ACK (immediate L2 acknowledgment)
   - Viconics vendor APS ACK (cmd 3 application-layer confirmation)
   Logging or testing must keep these separate. A MAC ACK does not mean the thermostat accepted a write.

6. **Opening the borrowed JACE:** This would violate the project's hard constraints and risks damaging a device you don't own.

7. **Starting a Zigbee coordinator (any kind) on the live network:** A second coordinator on PAN 0x00D2 would fight with the JACE for network control, potentially disrupting the thermostat network and causing the building's HVAC to lose BAS control.

8. **Assuming room temperature is available via passive RX:** The `RoomTemp` point (0x1000) is in `spec/points_catalog.csv` but its OTA `(prefix, code)` mapping is NOT confirmed in `pointmap.yaml`. Do not expose it as a BACnet object until the OTA mapping is proven via action-window evidence.

9. **Treating `pointmap.json` as production-ready:** The JSON catalog has 77 entries, but many are labeled `heat_setpoint_candidate_2`, `sp_*`, `auto_*` — these are hypotheses, not confirmed mappings. Only promote via the sanctioned `ota_promote_mapping.py` workflow.

---

## 8. Code Task Recommendation (Precise Coding-Agent Prompt)

```
## Task: Implement OtaRegistryAdapter — nRF OTA to BACnet PointRegistry bridge

### Working directory
Reverse/bac-gateway

### Context
The OTA decode stack (gateway/radio/live_adapter.py → gateway/ota/events.py → gateway/ota/pointmap.py)
produces OtaPointEvent, OtaAckEvent, and OtaUnknownEvent objects from live nRF RX frames.
The BACnet server (gateway/bacnet_server.py) exposes points via PointRegistry, which is
currently driven by the vendor serial path using update_from_radio(comm_addr, point_addr, ...).

The missing link is:
  OtaPointEvent(canonical_point='occupied_cool_setpoint', value=69.0)
    -> OtaRegistryAdapter
    -> PointRegistry update
    -> BACnet ReadProperty returns 69.0

### Files to create
gateway/radio/ota_registry_adapter.py

### Files to read first (do not modify without instruction)
gateway/ota/events.py            -- OtaPointEvent, OtaAckEvent, OtaUnknownEvent dataclasses
gateway/ota/pointmap.py          -- PointMap YAML loader, canonicalize() method
gateway/ota/pointmap.yaml        -- current 2-entry live map
gateway/bacnet_server.py         -- PointRegistry class and update_from_radio() signature
gateway/model.py                 -- ThermostatConfig, PointConfig, load_config()
points.yaml                      -- example config: occ_cool_setpoint → analogValue,1005

### OtaRegistryAdapter specification

class OtaRegistryAdapter:
    """
    Consumes OtaEvent objects from LiveAdapter and updates the canonical PointRegistry.
    Must not know BACnet object details. Must not know OTA frame details.
    """
    def __init__(self, registry: PointRegistry, config: GatewayConfig):
        # Build a lookup: canonical_point_name -> list of configured PointRuntime objects
        # that have a matching ota_point_name (or semantic_name) in their config.
        # Use config to build the mapping at init time; do not recompute on every event.
        ...

    def on_ota_event(self, event: OtaEvent) -> None:
        if isinstance(event, OtaPointEvent):
            self._handle_point(event)
        elif isinstance(event, OtaAckEvent):
            self._handle_ack(event)
        # OtaUnknownEvent: log at DEBUG level, do not raise.

    def _handle_point(self, event: OtaPointEvent) -> None:
        # Find PointRuntime entries where config.ota_name == event.canonical_point
        # Call registry.update(point_key, value=event.value, source='ota',
        #                       device_eui64=event.eui64)
        # If canonical_point is None or not in config: log at DEBUG, skip.
        ...

### Identity handling
- event.eui64 may be None if the OtaPointEvent came from a frame where the EUI-64 was
  not present in the payload (short-address-only path).
- If eui64 is None, use event.short_addr to match against the configured thermostat's
  last-known short address.
- Maintain an in-memory dict: eui64 -> current short_addr (updated from OtaPointEvents
  and OtaAckEvents where eui64 is present).
- Do not persist this dict to disk. It is rebuilt from live events.

### Tests to add
tests/test_ota_registry_adapter.py

Required test cases:
1. synthetic OtaPointEvent(canonical_point='occupied_cool_setpoint', value=69.0, eui64=...)
   -> adapter.on_ota_event(event)
   -> registry.get_point_runtime('occ_cool_setpoint').present_value == 69.0

2. synthetic OtaPointEvent(canonical_point='occupied_heat_setpoint', value=72.0)
   -> registry.get_point_runtime('occ_heat_setpoint').present_value == 72.0

3. OtaPointEvent with canonical_point='room_temperature' (not in YAML)
   -> adapter silently drops it (no KeyError, no exception)

4. OtaUnknownEvent -> adapter does not raise

5. OtaPointEvent with eui64=None, short_addr=0x0001
   -> adapter falls back to short-address lookup and still updates registry

### Constraints
- Do NOT modify gateway/bacnet_server.py internal BACnet logic.
- Do NOT modify gateway/radio/live_adapter.py OTA parsing.
- Do NOT add TX of any kind.
- Do NOT invent point mappings. Only use what is in pointmap.yaml.
- Keep all ACK layers distinct in comments and variable names.
- No network calls, no file I/O at event time.

### Acceptance criteria
  python -m pytest tests/test_ota_registry_adapter.py -v  -> all tests pass
  python -m pytest tests -q                               -> no regressions
  python tools/nrf_live_rx.py --help                      -> exits 0

### Optional wiring (only if tests pass cleanly)
Add --bacnet-push flag to tools/nrf_live_rx.py that:
  1. Instantiates OtaRegistryAdapter with the loaded config.
  2. Passes each decoded event to adapter.on_ota_event().
  3. Does NOT start the BACnet server in the same process (leave that to a separate launch).
```

---

## 9. Final Recommendation: Next 3 Actions in Order

### Action 1 (now, ~1–3 days)

**Write `gateway/radio/ota_registry_adapter.py` and `tests/test_ota_registry_adapter.py` using the coding-agent prompt in Section 8.**

- This is a pure Python task.
- Requires no hardware, no network access, no firmware change.
- Proves the `OtaPointEvent → PointRegistry → BACnet ReadProperty` chain with synthetic test data.
- Run: `python -m pytest tests/test_ota_registry_adapter.py -v`
- Pass criteria: all 5 test cases pass, zero regressions in the full test suite.

---

### Action 2 (after Action 1 passes, ~1–2 days)

**Wire the adapter into `tools/nrf_live_rx.py` and run it against the live nRF hardware.**

- Add the `--bacnet-push` flag (or just call `adapter.on_ota_event()` inline).
- Run: `python tools/nrf_live_rx.py --port /dev/ttyACM0 --channel 15 --limit 50`
- Expected: `OtaPointEvent(canonical_point='occupied_heat_setpoint', value=...)` appears when the physical thermostat has its setpoint changed (or when the JACE sends a write and the thermostat reports back).
- Log the full output to a file.
- Cross-check one frame against the offline pcap decode (`tools/ota_extract.py`) on the same frame bytes.

---

### Action 3 (after Action 2 shows live OtaPointEvents, ~1–2 days)

**Run the full live BACnet mirror proof using a BACnet client.**

- Start `gateway/bacnet_server.py --config points.yaml` in one terminal.
- Start the nRF live adapter (with push wired) in a second terminal.
- Open bacpypes3 console or YABE on a BMS client.
- Change the thermostat's cool setpoint physically by at least 2°F.
- Observe `analogValue,1005 present-value` update in the BACnet client.
- Record the before/after screenshot and the adapter log showing the matching `OtaPointEvent`.

This is the proof of "Level 1: Passive RX Live Mirror." After it is documented and reproducible, the project has a solid foundation for:
- Migrating additional point mappings (Exp3 candidate, room temperature) from the JSON catalog into YAML via the sanctioned promote workflow.
- Planning isolated bench Phase 2 (nRF-to-nRF, spare thermostat, lab PAN) for active write experiments.
- Optionally attempting the JACE Ethernet BACnet WhoIs sweep (Experiment 5 above) as a parallel validation path.

---

*End of Expert Council Report — 2026-06-25*
