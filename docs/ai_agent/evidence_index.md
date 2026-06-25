# Evidence Index

This file indexes the local evidence for the Viconics thermostat -> nRF -> BACnet goal.

## Primary Repo

Primary implementation repo:

```text
Reverse/bac-gateway/
```

The workspace root is not the git repo. Generated firmware build output exists under `Reverse/bac-gateway/build/` and is currently untracked; do not treat it as source.

## Top-Level Source Map

| Path | Why it matters |
| --- | --- |
| `Reverse/bac-gateway/README.md` | Current project overview, tracks A/B/C, commands, and safety rules. |
| `Manuals/planforlivecon` | Strategic source of truth for phases, safety gates, and non-paths. |
| `Manuals/deep-research-report.md` | Research-backed rationale for receive-first nRF raw 802.15.4 path. |
| `WiresharkN/viconics_w_ota_reverse_engineering_passes_1_4.md` | Strongest protocol dossier from captures and action logs. |
| `WiresharkN/wireshark_passes_1_3_summary.txt` | Capture-pass metadata, network constants, and early examples. |
| `Manuals/Important files.txt` | External reference links collected by the user. |

## nRF / Radio Receive Files

| Path | Contents |
| --- | --- |
| `firmware/nrf_vwg_bridge_rx/src/main.c` | USB CDC ACM startup, HELLO_RESP, radio init, RX loop. |
| `firmware/nrf_vwg_bridge_rx/src/radio_rx.c` | Nordic `nrf_802154` receive setup, channel, promiscuous mode, PSDU capture, RSSI/LQI. |
| `firmware/nrf_vwg_bridge_rx/src/bridge_proto.c` | Binary bridge framing and CRC32. |
| `firmware/nrf_vwg_bridge_rx/prj.conf` | Zephyr config: USB CDC ACM, serial, nRF 802.15.4 driver, no networking/shell. |
| `firmware/nrf_vwg_bridge_rx/boards/*.overlay` | USB CDC ACM overlays for DK and dongle. |
| `docs/radio/nrf_bridge_protocol.md` | Host/firmware frame protocol source-of-truth. |
| `docs/radio/nrf_firmware_bringup.md` | Build/flash/smoke-test notes. |
| `docs/radio/receive_only_live_test.md` | Phase 1A/1B live RX test expectations. |
| `gateway/radio/nrf_bridge_proto.py` | Python host codec for `VW` frames and `RX_FRAME` payloads. |
| `gateway/radio/nrf_bridge_client.py` | Serial client and frame extraction. |
| `gateway/radio/live_adapter.py` | Raw PSDU -> MAC -> NWK -> APS -> OTA event. |
| `tools/nrf_live_rx.py` | CLI for receive-only live bridge. |
| `tests/test_nrf_bridge_proto.py` | Host protocol unit tests. |
| `tests/test_nrf_firmware_rx_only.py` | Source inspection tests enforcing RX-only firmware shape. |
| `tests/test_live_adapter.py` | Synthetic PSDU tests for point, ACK, and unknown events. |

## OTA Decode and Mapping Files

| Path | Contents |
| --- | --- |
| `gateway/ota/ieee802154.py` | Minimal IEEE 802.15.4 MAC parser. |
| `gateway/ota/zigbee.py` | Minimal Zigbee NWK and APS parsers. |
| `gateway/ota/app.py` | Viconics application parser and command/rest decode rules. |
| `gateway/ota/events.py` | Canonical OTA event dataclasses. |
| `gateway/ota/pointmap.py` | YAML point map model, canonicalization, labels. |
| `gateway/ota/pointmap.yaml` | Current live parser mapping source, 2 confirmed entries. |
| `gateway/ota/pointmap.json` | Legacy richer discovered mapping catalog, 77 point entries. |
| `docs/ota/README.md` | OTA decoder workflow, TAP/FCS tolerance, command rules. |
| `docs/ota/known_ota_mappings.md` | Human-readable confirmed facts and promotion rules. |
| `docs/ota/candidate_ota_mappings.md` | Generated action-window candidate report. |
| `tools/ota_extract.py` | Decode pcapng to text/jsonl/csv; supports stats/catalog. |
| `tools/ota_batch_analyze.py` | Manifest-level batch analysis. |
| `tools/ota_action_windows.py` | Correlates action logs to decoded frames. |
| `tools/ota_mapping_report.py` | Generates conservative candidate mapping report. |
| `tools/ota_promote_mapping.py` | Sanctioned YAML mapping promotion workflow. |
| `tools/ota_tx_build.py` / `tools/ota_tx_verify.py` | TX template helpers; use only for isolated bench planning. |
| `tests/test_ota_app_decode.py` | Decoder semantics for analog, enum, u8, ACK, short-rest unknowns. |
| `tests/test_ota_mapping_report.py` | Candidate report behavior. |
| `tests/test_ota_promote_mapping.py` | Mapping promotion behavior. |

## Captures

Tracked manifest:

```text
Reverse/bac-gateway/captures/manifest.yaml
```

Original capture source folder:

```text
WiresharkN/
```

Important capture/action files:

| File | Notes |
| --- | --- |
| `log1.pcapng` | Baseline/capture sanity. |
| `log2.pcapng` + `Actionsforlog2.txt` | Mixed actions and early setpoint evidence. |
| `pass3_device2.pcapng` + `pass3_device2.txt` | DEVICE2 setpoint/occupancy actions. |
| `pass3_rtc.pcapng` + `pass3_rtc.txt` | RTC setpoint/occupancy actions. |
| `pass4_device2.pcapng` + `pass4_device2.txt` | Clean DEVICE2 scripted actions. |
| `pass4_rtc.pcapng` + `pass4_rtc.txt` | Clean RTC scripted actions. |
| `exp1_rtc_A/B.pcapng` | RTC occupied heat setpoint experiment. |
| `exp2_rtc_A/B.pcapng` | RTC occupied cool setpoint experiment. |
| `exp3_rtc_A/B.pcapng` | RTC candidate setpoint/action experiment. |

Raw `.pcapng` files should remain out of git. The manifest and docs are the tracked evidence surface.

## BACnet / Canonical Model Files

| Path | Contents |
| --- | --- |
| `gateway/bacnet_server.py` | BACnet/IP server, `PointRegistry`, radio manager for vendor serial path. |
| `gateway/model.py` | Config model for thermostats and points. |
| `points.yaml` | Local example point/BACnet object config. |
| `spec/points_catalog.csv` | 271-row Viconics point catalog extracted from Niagara/Viconics material. |
| `spec/enums.yaml` | Enum mappings extracted from Viconics classes. |
| `configs/demo_a2.yaml` | JACE demo config for safe Ethernet bridge. |
| `gateway/demo_a2_mirror.py` | Niagara `/ord` to BACnet/IP demo mirror. |
| `docs/demo_a2/README.md` | Demo A2 run/verification notes. |

## Vendor Radio Serial Path

| Path | Contents |
| --- | --- |
| `gateway/vwg_serial.py` | Canonical serial transport with start bytes, length, CRC checks, counters. |
| `gateway/radio/vwg_serial.py` | Re-export shim. |
| `gateway/radio/session.py` | Request/response matching, retries, reads/writes, RF module commands. |
| `gateway/codec.py` | Raw vendor frame constants and parser/builder. |
| `proto/codec.py` | Point value encode/decode using `spec/points_catalog.csv`. |
| `config/radio.yaml` | Serial config example. |
| `tools/vwg_probe.py` / `gateway/vwg_probe.py` | Probe CLIs. |
| `Reverse/vwirelessTstat_src/sources/com/viconics/wirelessTstat/enums/WirelessTstatConst.java` | Decompiled RF module constants. |
| `Reverse/vwirelessTstat_src/sources/com/viconics/wirelessTstat/BWirelessTstatNetwork.java` | Decompiled serial defaults: delay, retries, timeout, 57600 baud. |
| `Reverse/vwirelessTstat_src/sources/com/viconics/wirelessTstat/datatypes/BWirelessTstatSerialHelper.java` | Serial helper defaults. |
| `Reverse/vwirelessTstat_src/sources/com/viconics/wirelessTstat/datatypes/BWirelessTstatZigbeeHelper.java` | Zigbee PAN/channel config constraints. |

## Decompiled Viconics / Niagara Source Trees

| Path | Use |
| --- | --- |
| `Reverse/vwirelessTstat_src/` | Thermostat driver classes, serial messages, address models, point proxy code. |
| `Reverse/vwirelessGateway_src/` | Gateway/BACnet wrapper classes, point/device managers. |
| `Reverse/vwirelessTstatDevices_src/` | Device point definitions and frozen enums. |
| `vwirelessGateway.jar`, `vwirelessTstat.jar`, `vwirelessTstatDevices.jar` | Original jar inputs. |

Useful Java classes to inspect first:

```text
Reverse/vwirelessTstat_src/sources/com/viconics/wirelessTstat/messages/
Reverse/vwirelessTstat_src/sources/com/viconics/wirelessTstat/unsolicited/
Reverse/vwirelessTstat_src/sources/com/viconics/wirelessTstat/datatypes/
Reverse/vwirelessTstatDevices_src/sources/com/viconics/wirelessTstatDevices/point/
Reverse/vwirelessGateway_src/sources/com/viconics/wirelessGateway/bacnet/
```

## nRF Sniffer Reference

| Path | Contents |
| --- | --- |
| `nrf-sniffer/nRF-Sniffer-for-802.15.4/README.md` | Nordic sniffer overview and supported boards. |
| `nrf-sniffer/nRF-Sniffer-for-802.15.4/nrf802154_sniffer/` | Sniffer firmware hex files and extcap script. |

This is useful for passive capture and validation. It is not the same as the custom receive bridge under `firmware/nrf_vwg_bridge_rx/`.

## Zigbee2MQTT Reference

| Path | Contents |
| --- | --- |
| `zigbee2mqtt/` | Full Zigbee2MQTT checkout. |

Use it only for architecture ideas around bridge/controller abstractions. Do not assume Viconics W devices can be joined or controlled by stock Zigbee2MQTT.

## Commands for Future Agents

Run from `Reverse/bac-gateway`:

```powershell
python -m pytest tests -q
python tools\nrf_live_rx.py --help
python tools\ota_batch_analyze.py --manifest captures\manifest.yaml
python tools\ota_mapping_report.py --manifest captures\manifest.yaml --format markdown --output docs\ota\candidate_ota_mappings.md
python tools\ota_validate.py
```

Live receive smoke test:

```powershell
python tools\nrf_live_rx.py --port COM7 --channel 15 --limit 50
```

Vendor serial probe examples, only with an owned module:

```powershell
python -m gateway.vwg_probe raw-identify --port COM3
python tools\vwg_probe.py identify --port COM3
```

## Do Not Feed or Commit

Be careful with:

- `cookies.txt`
- HAR files that may contain session/cookie material
- any live JACE credentials in environment variables or copied logs
- raw capture files if they are considered sensitive
- generated `build/` outputs
