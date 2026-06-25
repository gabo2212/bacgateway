# Viconics W Deep Research Round 2
## Expert Council Report — Accelerating Live Connect

**Date:** 2026-06-25
**Scope:** New evidence, updated ranked paths, concrete next experiments, minimal safe lab plan, and coding recommendation. Does NOT repeat generic Round 1 conclusions.

---

## 1. Executive Answer

### Question: Is the passive RX mirror still the right top priority?

**Yes, and Round 2 research only strengthens that conclusion — but with three new actionable upgrades:**

1. **The thermostat transmits spontaneously on a 3-minute heartbeat.** This is now confirmed from the Viconics VWG driver guide ([FCC filing LIT document, fcc.report/FCC-ID/V95-VWG-APP/1119404.pdf](https://fcc.report/FCC-ID/V95-VWG-APP/1119404.pdf)). You are not blocked on polling — the thermostat will appear in your capture within 3 minutes of any power cycle or setpoint change. Passive RX is not "waiting in the dark"; it will see regular traffic.

2. **The ZBOSS `zb_af_set_data_indication()` API is a direct path to receiving APS 0xC1E4 frames on a ZBOSS coordinator — but it is not needed yet** because the nRF firmware is already in promiscuous RX mode delivering raw frames over USB. The API is noted as the Phase 2 entry point if you ever run a Zigbee stack on the nRF.

3. **BACpypes3 same-process architecture is confirmed viable.** A single asyncio loop can host multiple `Application` instances, each on a different UDP port. The `nrf_live_rx.py` reader loop and the BACnet server can share one process — eliminating the IPC boundary that currently exists between `bacnet_server.py` and `nrf_live_rx.py`.

### Summary verdict

| Question | Answer |
|---|---|
| Is passive RX enough for Level 1 live connect? | **Yes. 3-min heartbeat = regular data without polling.** |
| Is the chipset identified? | **No. Not proven. FCC filing photos exist but part numbers are absent from accessible text.** |
| Is 0xC1E4 implemented anywhere else publicly? | **No. Confirmed Viconics-private, zero public implementations found.** |
| Is ZBOSS coordinator with custom APS profile feasible? | **Partially feasible — with a known workaround. See Section 6.** |
| Is the raw nRF 802.15.4 coordinator (non-ZBOSS) path viable for association response? | **Technically possible but currently broken in NCS v3.2.3 due to MPSL timeslot conflict. Not recommended as primary path.** |
| Is BACpypes3 same-process multi-device confirmed? | **Yes. `multiple-ipv4-applications.py` gist by JoelBender confirms it.** |
| Is roomTemp capture feasible passively? | **Yes. Capture live heartbeat frames and look for the previously unseen OTA byte codes.** |

---

## 2. New Evidence Table

All items are new findings from Round 2 research not in the Round 1 report.

| # | Finding | Source | Confidence | Impact |
|---|---|---|---|---|
| R2-01 | Thermostat heartbeat interval is **3 minutes** — confirmed from VWG driver guide | [FCC filing 1119404.pdf](https://fcc.report/FCC-ID/V95-VWG-APP/1119404.pdf) | **High** | Passive RX will see regular data without polling |
| R2-02 | New thermostat discovery takes **up to 2 minutes** at the JACE — implies join is fast enough to observe | [FCC filing 1119404.pdf](https://fcc.report/FCC-ID/V95-VWG-APP/1119404.pdf) | High | Sets timeout expectation for bench join experiment |
| R2-03 | VWG join process uses standard Zigbee association: "PAN ID and Channel → any thermostat with same config is detected" | [FCC filing 1119404.pdf](https://fcc.report/FCC-ID/V95-VWG-APP/1119404.pdf) | High | Coordinator impersonation only needs standard 802.15.4 association response |
| R2-04 | PAN ID 251–500 = **stand-alone mode** (no gateway), usable for isolated bench testing | [FCC filing 1119404.pdf](https://fcc.report/FCC-ID/V95-VWG-APP/1119404.pdf) | High | Safe bench isolation without touching live PAN 0x00D2 |
| R2-05 | ZBOSS `zb_af_set_data_indication(cb)` API intercepts **all APS data packets**, noted as useful for "custom profile IDs" | [TI ZBOSS SDK AF docs](https://software-dl.ti.com/simplelink/esd/simplelink_lowpower_f3_sdk/9.10.00.83/exports/docs/third_party/zboss_r23/doxygen/html/group__af__management__service.html) | High | Can receive 0xC1E4 packets on a ZBOSS coordinator without per-profile registration |
| R2-06 | ZBOSS custom profile endpoint handler does **not trigger** unless profile ID is changed to 0x0104 (HA) — confirmed bug/limitation | [Nordic DevZone Q&A 95807](https://devzone.nordicsemi.com/f/nordic-q-a/95807/changing-zigbee-profile-id-to-my-own-custom-profile-makes-the-endpoint-handler-not-trigger) | High | `ZB_AF_SET_ENDPOINT_HANDLER` cannot be used with profile 0xC1E4; must use raw APS data indication instead |
| R2-07 | `nrf_802154_transmit_raw()` fires `TX_ERROR_TIMESLOT_DENIED` (error 5) in NCS v3.2.3 when `CONFIG_MPSL=n` | [Nordic DevZone Q&A 128001](https://devzone.nordicsemi.com/f/nordic-q-a/128001/raw-802-15-4-tx-rx-on-nrf52840-with-ncs-v3-2-3-what-is-the-supported-approach) | High | Raw 802.15.4 TX (for coordinator association response) is not straightforwardly available in current NCS without MPSL |
| R2-08 | Zephyr IEEE 802.15.4 L2 layer gained association request/response test coverage in recent changes — coordinator role and short address assignment supported at L2 | [Zephyr commit on juju.nz](https://juju.nz/src/michaelh/zephyr/commits/branch/main/tests/net/ieee802154) | Medium | Zephyr L2 coordinator mode (not ZBOSS) could handle association responses if raw TX issue is resolved |
| R2-09 | `IEEE802154_CONFIG_PAN_COORDINATOR` config option exists in Zephyr/nRF52840 driver — enabling PAN coordinator role at MAC level | [Nordic NCS IEEE 802.15.4 docs](https://developer.nordicsemi.com/nRF_Connect_SDK/doc/1.4.99-dev1/zephyr/reference/networking/ieee802154.html) | High | PAN coordinator bit set without full Zigbee stack; MAC-level coordinator mode is supported |
| R2-10 | BACpypes3 confirmed: multiple `Application` instances in same asyncio process using separate UDP ports; `VirtualNetwork` pattern also available for shared-port virtual devices | [JoelBender gist multiple-ipv4-applications.py](https://gist.github.com/JoelBender) | High | Single-process architecture: BACnet server + nRF RX loop feasible in one asyncio event loop |
| R2-11 | VT7300W datasheet: "thermostat begins querying network 30 seconds after parameter given" — implies device actively polls coordinator for config on startup | [VT7300c1000 datasheet](https://cprbestek.com/wp-content/uploads/2014/08/VT7300c1000_Data_Sheet.pdf) | Medium | After join, thermostat expects to receive config from coordinator within 30 s — coordinator must respond |
| R2-12 | W-suffix confirmed proprietary: "Proprietary version of ZigBee not compatible with SmartStruxure Lite solution" — VCM7000 datasheet explicit | [VCM7000 datasheet via veris.com](https://www.veris.com/ASSETS/DOCUMENTS/ITEMS/EN/028-0457-00-Technical-Cut-Sheet.pdf) | Definitive | All Zigbee2MQTT and SmartStruxure Lite paths **ruled out** for W-suffix hardware |
| R2-13 | `zb_aps_send_user_payload()` function mentioned in NCS InfoCenter SDK 4.1.0 for sending custom low-level APS data | [Nordic InfoCenter SDK](https://infocenter.nordicsemi.com/index.jsp?topic=%2Fsdk_tz_v4.1.0%2Fgroup__aps__user__payload.html) | Medium | ZBOSS coordinator can send raw APS frames to 0xC1E4 via this API; not proven on NCS current version |
| R2-14 | Viconics 0xC1E4 APS profile ID: in Wireshark packet-zbee.h, the range 0xC000–0xC002 is Cirronet, 0xC003–0xC00C is ChipCon. **0xC1E4 falls in undefined manufacturer range** — not a registered Zigbee Alliance profile | [Wireshark packet-zbee.h source](https://www.wireshark.org/docs/wsar_html/packet-zbee_8h_source.html) | Definitive | 0xC1E4 is a Viconics-assigned private profile — Wireshark will show it as unknown; this is expected |
| R2-15 | Stack profile 0x00 = "network-specific" (private). Viconics W likely uses this. Stack profile visible in Zigbee beacon frames | [Kaspersky ZigBee security research 2025](https://securelist.com/zigbee-protocol-security-assessment/118373/) | Medium-High | Beacon capture on ch.15 will reveal Viconics W stack profile and confirm private vs. ZigBee Pro |
| R2-16 | FCC V95-VWG-APP chipset: internal photos document exists (doc# 1119399) but returned 404; operational description confirms "RF chip" photo visible but no part number in accessible text | [FCC Report V95-VWG-APP](https://fcc.report/FCC-ID/V95-VWG-APP) | High (negative) | Chipset is **not publicly identifiable** from FCC filings — confidential or illegible |
| R2-17 | VT8600 BACnet integration guide maps ZigBee network params as BACnet AV objects: AV10=PAN ID, AV11=channel, AV12=short address, AV13=IEEE address | [Kele VT8600 integration guide](https://www.kele.com/Catalog/22%20Thermostats_Controllers/PDFs/VT8600%20BACNET%20Integration%20Guide.pdf) | Medium | Useful reference BACnet object model for final gateway design |

---

## 3. Changed My Mind?

### 3a. Items where Round 2 evidence reverses or sharpens Round 1 conclusions

**CHANGED: "Heartbeat/update frequency unknown."**
Round 1 listed heartbeat timing as unknown. Round 2 confirms it is **3 minutes** from the official VWG driver guide. This eliminates the "blind wait" concern about passive RX. You will receive at least one frame per thermostat every 3 minutes without any polling.

**CHANGED: "Joining process unknown — may not be standard Zigbee."**
Round 1 rated the join mechanism as unknown. Round 2 finds VWG documentation states "any thermostat having the same PAN ID and Channel can be detected and registered" and "a ZigBee address assigned by the wireless communication card." This is **standard 802.15.4 MAC association**. The thermostat sends an association request; the coordinator (VWG radio card) responds with a short address. No vendor-specific pairing handshake has been found. The join is standard.

**CHANGED: "ZBOSS coordinator with custom profile may work like standard endpoints."**
Round 1 was cautious. Round 2 finds a confirmed bug: `ZB_AF_SET_ENDPOINT_HANDLER` does NOT trigger for custom profile IDs in current ZBOSS on NCS. The workaround is `zb_af_set_data_indication()` which intercepts all APS data regardless of profile. This is a workable path but requires using the undocumented/test-oriented API. The endpoint handler approach is ruled out for profile 0xC1E4.

**CHANGED: "Raw 802.15.4 coordinator (non-ZBOSS) may be simplest TX path."**
Round 2 finds that `nrf_802154_transmit_raw()` in NCS v3.2.3 fires `TX_ERROR_TIMESLOT_DENIED` when MPSL is disabled. This is a real blocker for standalone raw MAC. The nRF5 SDK (not NCS) has a cleaner raw API but has toolchain issues. The Zephyr IEEE 802.15.4 L2 layer (not ZBOSS) with `IEEE802154_CONFIG_PAN_COORDINATOR` is a more promising path for raw coordinator mode — but still requires TX, which is Phase 2+.

**UNCHANGED: "No public implementation of 0xC1E4 exists."**
Exhaustive search of GitHub, Wireshark source, Zigbee cluster registries, and community forums found zero results. Confirmed private/proprietary. This is not expected to change.

**UNCHANGED: "Zigbee2MQTT does not work for W-suffix hardware."**
W-suffix is explicitly documented as incompatible with any standard Zigbee implementation. Ruled out definitively.

---

## 4. Ranked Path Update (A–G)

Paths from Round 1 retained and updated; new paths F and G added.

| Rank | Path | Change from R1 | Feasibility | Next Gate |
|---|---|---|---|---|
| **A** | **Passive RX live mirror (nRF promiscuous, no TX)** | **Unchanged #1. Heartbeat confirmed 3 min.** | **High — one code unit away** | Write `OtaRegistryAdapter`; prove BACnet ReadProperty returns decoded setpoint |
| **B** | **Single-process architecture (BACnet server + nRF RX in same asyncio event loop)** | **New in R2. BACpypes3 multi-app confirmed.** | High | Merge `bacnet_server.py` + `nrf_live_rx.py` into single process; eliminates IPC boundary |
| **C** | **roomTemp passive capture + code identification** | **New in R2. Promoted over JACE path.** | Medium-High | Let 3-min heartbeat run; look for OTA byte codes adjacent to known setpoint codes in live capture |
| **D** | **JACE Ethernet / BACnet WhoIs read-only** | Demoted. Not desired solution. | Medium | `bacpypes3` WhoIs from Pi; read-only; useful only if reveals 0xC1E4 decode or point name lookup |
| **E** | **ZBOSS coordinator (NCS) with `zb_af_set_data_indication()` + custom APS** | R1 was speculative. R2 confirms specific API and workaround. | Medium | Isolated bench; bench-flash coordinator image with `zb_af_set_data_indication`; verify 0xC1E4 frames arrive in callback |
| **F** | **Zephyr IEEE 802.15.4 L2 coordinator (non-ZBOSS) for bench join** | **New in R2.** | Medium-Low | Isolated bench PAN (ID 251–500); set `IEEE802154_CONFIG_PAN_COORDINATOR`; implement association response handler in Zephyr L2; requires resolving MPSL/timeslot TX issue |
| **G** | **nRF5 SDK raw 802.15.4 coordinator (older SDK)** | **New in R2. Fallback if NCS TX blocked.** | Low-Medium | nRF5 SDK v17 has documented `nrf_802154_transmit_raw` without MPSL dependency; separate from NCS; toolchain complexity |

---

## 5. Detailed Findings

### 5.1 Chipset Identification (Task 1)

**Verdict: Not proven.**

FCC filing V95-VWG-APP (filed 2009-06-05) confirms internal photos exist (document #1119399) showing the RF chip (Figure 1-3: bottom view, top view, RF chip view; Figure 5: shield removed). However, document #1119399 returned HTTP 404 when fetched. The operational description (document #1119402) references the RF chip photos but does not state the part number in its accessible text. The FCC filing is under a confidentiality agreement for the internal schematics.

**Best hypothesis (not proven):** The 2009-era Viconics wireless gateway uses a Freescale/NXP MC13192/MC13201-family 802.15.4 transceiver or an EmberZNet EM2420-era chip. This is the era when proprietary Zigbee was common with those chipsets. Both supported private stack profiles. However, this is speculation — **no part number confirmed.**

**Impact on project:** None. The nRF52840 receives standard IEEE 802.15.4 frames regardless of what chip transmits them. Chipset identity would only matter if trying to replicate clock-exact timing or if security keys were device-specific (not observed).

### 5.2 0xC1E4 APS Profile (Task 2)

**Verdict: Confirmed zero public implementations. Range analysis confirms private.**

From Wireshark `packet-zbee.h`: The manufacturer-allocated profile ranges start at 0xC000. The range 0xC1E4 is not in any registered entry (Cirronet: 0xC000–0xC002, ChipCon: 0xC003–0xC00C, Ember: 0xC00D–0xC016 — the listing stops there). 0xC1E4 falls well into the unassigned manufacturer space. This confirms it was a Viconics-assigned profile number, never registered with Zigbee Alliance.

**Practical consequence:** Wireshark will display this profile as "Unknown" — this is expected and correct, not an error in your capture. The project's own decoder is the only working implementation.

### 5.3 Join / Commissioning Process (Task 4)

**Verdict: Standard 802.15.4 MAC association — high confidence.**

From the VWG driver guide ([FCC doc 1119404.pdf](https://fcc.report/FCC-ID/V95-VWG-APP/1119404.pdf)):

> "As soon as a valid PAN ID and Channel are given to the JACE wireless communication card, any thermostat having the same configuration of PAN ID and Channel can be detected and registered."
> "Thermostats get a ZigBee address assigned by the wireless communication card of the JACE or another thermostat device."

This language is consistent with standard IEEE 802.15.4 association:
1. Thermostat scans configured channel for beacon from a PAN coordinator.
2. Thermostat sends association request (MAC command frame 0x01) to coordinator.
3. Coordinator assigns short address and sends association response (MAC command frame 0x02).
4. Thermostat sends data request to pull pending association response.
5. Coordinator delivers association response with assigned short address.

**Critical lab isolation fact:** PAN IDs 251–500 are documented as "stand-alone" mode (no gateway required). Setting a thermostat to PAN ID 252 (safe value, not 0x00D2=210) and a new channel (e.g., channel 20) creates a fully isolated bench network.

**What remains unknown:** The exact VWG-specific post-join initialization (cmd 0/1 identify exchange, any config provisioning the thermostat expects to receive within 30 s of joining). The datasheet notes "thermostat will continue querying the specified device for its properties every 30 seconds" — this suggests the thermostat actively polls the gateway after join, which means the coordinator must respond to those queries.

### 5.4 Zephyr/nRF52840 Raw Coordinator Association Response (Task 5)

**Verdict: Technically possible, currently blocked in NCS v3.2.3 for raw TX path.**

Key findings:
- `IEEE802154_CONFIG_PAN_COORDINATOR` option exists in nRF52840 Zephyr driver — sets PAN coordinator bit at MAC level ([NCS IEEE 802.15.4 docs](https://developer.nordicsemi.com/nRF_Connect_SDK/doc/1.4.99-dev1/zephyr/reference/networking/ieee802154.html)).
- Zephyr L2 now has `MLME-ASSOCIATE.indication` and `MLME-ASSOCIATE.response` primitives and test coverage ([Zephyr juju.nz commit](https://juju.nz/src/michaelh/zephyr/commits/branch/main/tests/net/ieee802154)).
- BUT: `nrf_802154_transmit_raw()` with `CONFIG_MPSL=n` fires `TX_ERROR_TIMESLOT_DENIED` error 5 in NCS v3.2.3 — confirmed recent DevZone report ([DevZone Q&A 128001, 2026-05-01](https://devzone.nordicsemi.com/f/nordic-q-a/128001/raw-802-15-4-tx-rx-on-nrf52840-with-ncs-v3-2-3-what-is-the-supported-approach)).
- Workaround: Use MPSL (keep it enabled) with the 802.15.4 radio driver; or use nRF5 SDK which has a simpler raw API; or use Zephyr L2 `net_mgmt` association API (which bypasses the raw transmit path).

**Feasibility score for Phase 2 bench work:** 6/10. The MAC-level association response can be constructed and sent using Zephyr L2 association primitives once the MPSL TX issue is resolved or worked around. This is not a fundamental impossibility — it is an NCS version-specific toolchain issue.

### 5.5 ZBOSS NCS Coordinator with Custom APS Profile (Task 6)

**Verdict: Feasible using `zb_af_set_data_indication()` — not using `ZB_AF_SET_ENDPOINT_HANDLER`.**

Critical findings:

**Problem confirmed:** `ZB_AF_SET_ENDPOINT_HANDLER` does not trigger for custom profile IDs. A user confirmed that changing from a custom profile to `0x0104` (HA) immediately fixed the issue. Root cause: ZBOSS filters endpoint handlers by profile ID match.

**Solution:** Use `zb_af_set_data_indication(cb)` which is documented as:
> "This API call may be useful for tests which uses custom profile id or which needs to send raw data over APS."

This callback receives **all** incoming APS data packets before per-profile endpoint dispatch. It can inspect the profile ID (expected: 0xC1E4) and cluster ID (expected: 0x0002) and handle Viconics-specific payloads directly.

**Architecture for ZBOSS path (Phase 2, bench-only):**
```
ZBOSS coordinator (nRF52840 bench)
  → Standard association handling (built-in ZBOSS ZDO)
  → zb_af_set_data_indication() callback
      → check aps_header.profile_id == 0xC1E4
      → check aps_header.cluster_id == 0x0002
      → extract APS payload bytes
      → route to existing Python OTA decoder (via USB serial to host)
```

**ZBOSS stack profile mismatch risk:** Viconics W uses stack profile 0x00 (private); ZBOSS defaults to 0x02 (ZigBee Pro). The `ZB_STACK_PROFILE` parameter in `zb_config.h` must be set to 0x00. This is documented as possible for "closed networks" but may affect other behaviors. **Not proven** that this is required — the beacon inspection experiment (Section 6.1) should determine the actual stack profile first.

### 5.6 BACpypes3 Same-Process Architecture (Task 7)

**Verdict: Confirmed feasible. JoelBender gist `multiple-ipv4-applications.py` demonstrates it explicitly.**

From [JoelBender's gists](https://gist.github.com/JoelBender):
> "Simple example of a BACnet/IP application that runs multiple applications on the same host."

The architecture uses separate UDP ports (47808, 47809) for each `Application` instance in the same `asyncio` event loop. This is directly applicable to the gateway architecture:

**Recommended single-process design:**
```python
async def main():
    # BACnet server: presents thermostat points to BACnet clients
    bacnet_app = Application(...)          # UDP 47808
    point_registry = PointRegistry(bacnet_app)

    # nRF RX loop: reads USB serial, decodes OTA frames
    nrf_loop = NrfLiveRxLoop(port="/dev/ttyACM0")
    ota_adapter = OtaRegistryAdapter(point_registry)
    nrf_loop.add_listener(ota_adapter)

    # Run both concurrently in same asyncio event loop
    await asyncio.gather(
        bacnet_app.run(),
        nrf_loop.run()
    )
```

**Key constraint from BAC0 docs:** Two `Application` instances on the same IP must use different UDP ports. If presenting multiple virtual thermostat devices (as separate BACnet device instances), use the `VirtualNetwork` pattern or assign each to a different port. For the single-gateway-device model (all thermostat points aggregated under one BACnet device), a single UDP port on 47808 is sufficient.

**Benefit of single-process vs. IPC:** Eliminates the need for `asyncio.Queue`, Unix socket, or subprocess pipe between `bacnet_server.py` and `nrf_live_rx.py`. The `PointRegistry` becomes a shared in-memory object with no serialization. Latency from radio frame to BACnet presentValue update becomes a single coroutine dispatch.

### 5.7 Passive RX Update Frequency (Task 8)

**Verdict: 3-minute heartbeat confirmed. Spontaneous reports on setpoint change are likely faster.**

From the VWG driver guide (FCC doc 1119404.pdf):
> "Please refer to the health status 'Last Ok Time' value for the total amount of time a single thermostat has not updated its **mandatory 3 minutes heartbeat update** to the JACE."

This is a hard number from the official documentation. Implications:

| Event type | Expected frequency |
|---|---|
| Mandatory heartbeat | Every 3 minutes (confirmed) |
| Setpoint change initiated at thermostat | Immediate spontaneous report (consistent with ZCL reporting behavior) |
| JACE-initiated write → thermostat ACK → gateway sees cmd 3 | Within 1–2 seconds of write (passive RX will see this if in promiscuous mode) |
| Temperature drift report | Unknown — likely on change-of-value threshold, not timed |
| System mode / fan mode change | Unknown — likely immediate on change |

**Practical implication for passive RX:** You will see at least 2 frames every 3 minutes (one from each of the two observed thermostats, short addresses 0x0001 and 0x143E). If both thermostats are active, and the JACE is polling, you may see significantly more traffic. The `OtaRegistryAdapter` should update `presentValue` on every received frame.

### 5.8 roomTemp OTA Mapping Discovery (Task 9)

**Verdict: Passive capture is the correct approach. Systematic byte-pattern analysis will identify the code.**

**What is confirmed:**
- `occupied_heat_setpoint` maps to OTA bytes `0x08/0x49 → 0x0A` (value * 10 in u16_be)
- `occupied_cool_setpoint` maps to OTA bytes `0x08/0x4B → 0x2D` (value * 10 in u16_be)

**Observation:** 0x49 = decimal 73; 0x4B = decimal 75. These look like ASCII 'I' and 'K'. The next field in the setpoint range might be 0x4D (decimal 77, ASCII 'M') for some other setpoint. Room temperature would have a different prefix code.

**Experiment design:**
1. Run `tools/nrf_live_rx.py --port /dev/ttyACM0 --channel 15 --dump-hex` to log all raw OTA payloads.
2. Let it run for 2+ heartbeat cycles (6+ minutes) to capture baseline.
3. Change room temperature stimulus (warm/cool the sensor). The thermostat should spontaneously report when temperature crosses its threshold (unknown threshold, likely 0.5°C increments based on typical HVAC thermostats).
4. Identify frames where the known setpoint byte codes (0x49, 0x4B) are absent but a new byte code with the same analog encoding (`prefix | code | u16_be(value * 10)`) appears.
5. Cross-reference the decoded value against the thermostat's displayed temperature.

**Alternative approach:** Compare frame lengths. A roomTemp update frame likely has the same structure as a setpoint frame. If you see a cmd 2 frame with a different byte code but the same total length and value encoding, it is a new point.

**Warning (per constraint):** Do not add this to `pointmap.json` until the mapping is confirmed by at least 3 correlated captures at different temperature values.

---

## 6. Capture Plan (Safe Passive RX)

### Phase 1A: Prove the `OtaRegistryAdapter` chain (no hardware required)

```python
# Test: synthetic OTA event → BACnet ReadProperty
from gateway.radio.ota_registry_adapter import OtaRegistryAdapter
from gateway.registry import PointRegistry
from gateway.bacnet_server import build_test_app

app = build_test_app(port=47809)  # test port, not 47808
registry = PointRegistry(app)
adapter = OtaRegistryAdapter(registry)

# Inject synthetic event
event = OtaPointEvent(
    eui64="1d:35:08:04:32:20:31:04",
    point_code=0x49,
    value=215  # 21.5°C * 10
)
adapter.on_ota_event(event)

# BACnet read
result = await app.read_property(
    Address("127.0.0.1"),
    ObjectIdentifier("analogValue", 1005),
    PropertyIdentifier("present-value")
)
assert result == 21.5
```

### Phase 1B: Live capture with known thermostats

```bash
# Terminal 1: start gateway
python tools/nrf_live_rx.py --port /dev/ttyACM0 --channel 15

# Terminal 2: BACnet client poll every 30 s
python -m bacpypes3 read <pi-ip> analogValue,1005 present-value

# Terminal 3 (optional): Wireshark packet capture for cross-validation
# whsniff -c 15 | wireshark -k -i -
```

**Success criteria for Level 1 live connect:**
- `analogValue,1005 present-value` updates within 3 minutes of any thermostat setpoint change
- Decoded value matches thermostat display within ±0.5°C (analog encoding precision)
- Frame capture shows `APS profile 0xC1E4, cluster 0x0002, cmd 2` in Wireshark simultaneously

### Phase 1C: roomTemp passive mapping

```bash
# Extended capture for roomTemp discovery
python tools/nrf_live_rx.py --port /dev/ttyACM0 --channel 15 \
    --dump-unknown-codes --output-dir /tmp/viconics_captures/
```

Heat or cool the room near thermostat 0x0001 (EUI-64: `1d:35:08:04:32:20:31:04`). Log all cmd 2 frames with unknown point codes. Identify the code that correlates with ambient temperature change.

---

## 7. Minimal Safe Lab Plan (Phase 2 — Bench Only, Isolated)

**These steps require a spare thermostat and a physically isolated setup. NO live PAN 0x00D2, NO channel 15.**

### Setup
```
Isolated bench:
- PAN ID: 252 (stand-alone mode: 251–500)
- Channel: 20 (or any channel ≠ 15 to avoid accidental live network interference)
- One spare VT7300W thermostat set to the bench PAN/channel
```

### Lab Step B1: Beacon observation (passive, no TX)
Point a second nRF52840 in promiscuous mode at the isolated PAN. Capture beacons from the live thermostat scanning for a coordinator. Inspect the beacon payload for:
- Stack profile bits (bits 0–3 of Superframe Specification field)
- Whether the thermostat broadcasts association requests blindly or waits for a beacon

### Lab Step B2: Association response (requires bench TX)
Only if current RX-only firmware can be safely switched on the bench unit:
1. Flash bench nRF52840 with Zephyr L2 coordinator sample (`IEEE802154_CONFIG_PAN_COORDINATOR=y`).
2. Set PAN ID 252, channel 20.
3. Observe thermostat sending association request.
4. Respond with association response assigning short address 0x0001.
5. Observe post-join traffic: does thermostat send cmd 0 identify? Does it send point data?

### Lab Step B3: Confirm 30-second config poll
After join, wait 30 seconds. The VT7300W datasheet states the thermostat begins querying the coordinator 30 seconds after receiving a network address. Capture this query frame structure — it may be cmd 0 (identify request) or a ZDO simple descriptor request.

### Lab Step B4: ZBOSS coordinator test (optional, higher complexity)
If Lab Step B2 succeeds, cross-test with ZBOSS coordinator:
1. Flash ZBOSS coordinator image with `ZB_STACK_PROFILE=0` and `zb_af_set_data_indication()` callback.
2. Join bench thermostat.
3. Verify 0xC1E4 APS frames arrive in the callback.
4. Log raw APS payload — compare against known pointmap codes.

---

## 8. Identity Registry Design

### The Problem
- EUI-64 is the stable identity (per project constraint and operational rule)
- Short/NWK address is volatile (changes on rejoin)
- Thermostat config (BACnet object IDs, point names, floor/zone) maps to EUI-64
- The live RX stream delivers frames with source EUI-64 in APS payload (confirmed in evidence: `1d:35:08:04:32:20:31:04` and `1d:35:08:02:07:43:61:04`)

### Proposed Registry Architecture

```python
# identity_registry.py

@dataclass
class ThermostatIdentity:
    eui64: str                    # Canonical key (immutable)
    display_name: str             # e.g., "Floor3-RM302"
    short_addr: int | None        # Volatile — updated on each received frame
    bacnet_device_instance: int   # e.g., 1001, 1002
    point_map: dict[int, str]     # OTA code → BACnet object name
    last_seen: datetime           # For health monitoring

class IdentityRegistry:
    def __init__(self):
        self._by_eui64: dict[str, ThermostatIdentity] = {}
        self._by_short_addr: dict[int, str] = {}  # short_addr → eui64

    def register(self, eui64: str, config: dict) -> ThermostatIdentity: ...

    def update_short_addr(self, eui64: str, short_addr: int) -> None:
        """Called on every received frame where short addr is known."""
        identity = self._by_eui64[eui64]
        old_short = identity.short_addr
        if old_short is not None and old_short != short_addr:
            del self._by_short_addr[old_short]
            logger.warning(f"Short addr changed for {eui64}: 0x{old_short:04X} → 0x{short_addr:04X}")
        identity.short_addr = short_addr
        self._by_short_addr[short_addr] = eui64

    def resolve_eui64(self, eui64: str) -> ThermostatIdentity | None:
        return self._by_eui64.get(eui64)

    def resolve_short_addr(self, short_addr: int) -> ThermostatIdentity | None:
        eui64 = self._by_short_addr.get(short_addr)
        return self._by_eui64.get(eui64) if eui64 else None
```

### Process-Boundary Fix

The current architecture has `bacnet_server.py` and `nrf_live_rx.py` as separate processes, requiring IPC. **Recommended fix: merge into single asyncio process.**

```python
# gateway/main.py — unified entry point

async def main():
    # Load identity registry from config file
    registry = IdentityRegistry.from_yaml("config/thermostats.yaml")

    # Build BACnet application with objects pre-populated from registry
    bacnet_app = GatewayBacnetApp.from_registry(registry, port=47808)

    # Build OTA adapter that wires OTA events → BACnet presentValue updates
    ota_adapter = OtaRegistryAdapter(registry, bacnet_app)

    # Build nRF RX loop
    nrf_rx = NrfLiveRxLoop(
        port=os.environ.get("NRF_PORT", "/dev/ttyACM0"),
        channel=15,
    )
    nrf_rx.add_listener(ota_adapter)

    # Run both in same event loop
    await asyncio.gather(
        bacnet_app.run_forever(),
        nrf_rx.run_forever(),
        health_reporter(registry),  # optional: log offline thermostats
    )

if __name__ == "__main__":
    asyncio.run(main())
```

**Config file format (inspired by VT8600 BACnet model, R2-17):**
```yaml
thermostats:
  - eui64: "1d:35:08:04:32:20:31:04"
    name: "Floor3-RM302"
    bacnet_device_instance: 1001
    points:
      - code: 0x49
        name: "occupied_heat_setpoint"
        bacnet_object: "analogValue,1005"
        unit: degreesCelsius
      - code: 0x4B
        name: "occupied_cool_setpoint"
        bacnet_object: "analogValue,1006"
        unit: degreesCelsius
      # roomTemp code: TBD from Phase 1C capture
```

---

## 9. Coding Recommendation

### Priority 1: `OtaRegistryAdapter` (unblocks Level 1 live connect)

**What it does:** Receives `OtaPointEvent` objects from the nRF live RX decoder and updates BACnet `presentValue` on the matching BACnet object.

**Exact coding-agent prompt:**

```
Task: Write gateway/radio/ota_registry_adapter.py

Purpose: Bridge between the OTA decoder (gateway/radio/ota_parser.py produces OtaPointEvent)
and the BACnet PointRegistry (gateway/registry.py).

Inputs:
- OtaPointEvent: dataclass with fields {eui64: str, point_code: int, raw_value: int}
- IdentityRegistry: maps eui64 → ThermostatIdentity → bacnet_object

Behavior:
- On each OtaPointEvent:
  1. Look up eui64 in identity_registry → get ThermostatIdentity
  2. Look up point_code in identity.point_map → get BACnet object identifier
  3. Decode raw_value using codec (analog: raw_value / 10.0; enum: raw_value directly)
  4. Set bacnet_app.object_list[obj_id].presentValue = decoded_value
  5. Update identity.last_seen = datetime.now(UTC)
  6. If eui64 not found, log warning and skip

Error cases:
- eui64 not in registry: log WARNING, drop event (not an error — could be neighbor thermostat)
- point_code not in identity.point_map: log DEBUG (unknown point code — candidate for Phase 1C discovery)
- BACnet object not found: log ERROR

Tests required:
- test_synthetic_heat_setpoint: inject OtaPointEvent for 0x49, verify BACnet AV reads 21.5
- test_unknown_eui64: inject event with unregistered EUI-64, verify no crash and warning logged
- test_unknown_code: inject event with known EUI-64 but unknown code 0xFF, verify debug log

Constraints:
- Must be asyncio-compatible (async def on_ota_event or queue-based)
- Must NOT write to pointmap.yaml or auto-discover new codes (read-only point map use)
- Must NOT transmit anything to the radio
```

### Priority 2: Single-process architecture merge

**What it does:** Merges `bacnet_server.py` and `nrf_live_rx.py` into `gateway/main.py` using a shared asyncio event loop and shared `IdentityRegistry`.

This is the process-boundary fix. It eliminates the IPC path and makes the `OtaRegistryAdapter` a direct in-process call rather than a message queue consumer.

### Priority 3: Wireshark beacon capture for stack profile

**What it does:** Run Wireshark on channel 15, filter for beacon frames (`wpan.frame_type == 0`), inspect the stack profile bits in the Zigbee beacon payload. This tells you definitively whether the live network uses stack profile 0x00 (private) or 0x02 (ZigBee Pro). This is a 10-minute passive observation with no TX.

```bash
# Using whsniff + Wireshark
whsniff -c 15 | wireshark -k -i -
# Filter: wpan.frame_type == 0
# Look at: zbee_beacon.stack_profile
```

---

## 10. Red Flags (Updated for Round 2)

| Flag | Severity | Status |
|---|---|---|
| roomTemp OTA code not confirmed | HIGH | Unknown until Phase 1C capture |
| 30-second post-join config poll: coordinator must respond | HIGH | Unknown format — blocks Phase 2 bench join |
| ZBOSS endpoint handler bug for custom profiles | HIGH | Workaround found (`zb_af_set_data_indication`), but untested for 0xC1E4 |
| NCS v3.2.3 raw TX `TIMESLOT_DENIED` | HIGH (Phase 2 only) | Blocks raw coordinator path; MPSL or Zephyr L2 workaround needed |
| Stack profile mismatch (W=0x00 vs ZBOSS default=0x02) | MEDIUM | Needs beacon capture to confirm; may block ZBOSS join |
| Short address volatility | MEDIUM | Identity registry design handles this; code not yet written |
| Frame counter replay protection | LOW (not observed) | NWK security not seen in captures; low risk unless future firmware update |
| Wireshark APS decryption key unknown | LOW for RX-only | If Viconics W uses NWK encryption, payload will be opaque; not observed in current captures |

---

## 11. Final Three Actions

**Action 1 (this week):** Write `gateway/radio/ota_registry_adapter.py` and `gateway/identity_registry.py` using the coding task in Section 9. Run the synthetic test. This proves the Python chain from OTA bytes to BACnet ReadProperty with no hardware and no TX.

**Action 2 (this week):** Merge `bacnet_server.py` + `nrf_live_rx.py` into `gateway/main.py` using a single asyncio process. Wire `OtaRegistryAdapter` directly. Verify with a synthetic frame injection that `analogValue,1005` updates in-process.

**Action 3 (within 1–2 weeks, requires hardware):** Run the unified gateway on the live network. Let it capture heartbeat frames for 10 minutes. Observe which OTA byte codes appear in cmd 2 frames for both thermostats. Identify the roomTemp code. Do not write to `pointmap.yaml` until 3 correlated temperature observations confirm the mapping. **No TX. Live network only for observation.**

---

*Report compiled: 2026-06-25. All external claims cited. Items labeled "not proven" are clearly marked. Actionable findings prioritized over generic explanations.*
