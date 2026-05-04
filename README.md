# BACnet Viconics Gateway

Python gateway and reverse-engineering toolkit for Viconics / VWG wireless
thermostats. The project has two active paths:

- **Safe live bridge:** read selected thermostat points from a borrowed JACE over
  Niagara `/ord`, mirror them locally, and expose them as BACnet/IP from this
  codebase.
- **OTA capture mining:** decode existing nRF / Wireshark `.pcapng` captures,
  correlate them with operator action logs, and maintain conservative
  confirmed/candidate OTA point mappings.

True direct-radio replacement remains a later path. The code keeps source
adapters, the point model, and BACnet presentation separate so the JACE adapter
can eventually be replaced by a radio source adapter.

## Current Status

- JACE Ethernet source adapter exists: `gateway/jace_client.py`.
- Niagara HTTP/oBIX/SCRAM/cookieDigest client exists: `gateway/niagara_client.py`.
- Demo A2 BACnet mirror exists: `gateway/demo_a2_mirror.py`.
- OTA parser stack exists under `gateway/ota/`.
- Capture manifest and batch tooling exist under `gateway/captures/` and `tools/`.
- Confirmed OTA facts and mappings are documented in `docs/ota/`.
- Raw captures are intentionally gitignored; curated manifests and mapping docs are tracked.
- Full test suite currently covers the capture tools, OTA decoder/mapping logic,
  JACE client scaffolding, Niagara parsing/auth behavior, radio session matching,
  and probe smoke tests.

## Setup

```powershell
pip install -r requirements.txt
```

Dependencies:

- `bacpypes3` for BACnet/IP.
- `pyserial` for the future/legacy serial-radio path.
- `PyYAML` for config and capture manifest files.

Run tests:

```powershell
python -m pytest tests -q
```

Validate OTA point-map invariants:

```powershell
python tools\ota_validate.py
```

## Repository Layout

- `gateway/ota/` — pcapng, IEEE 802.15.4, Zigbee/NWK/APS, app-frame decoding,
  point-map normalization, decoded-line parsing, and TX template helpers.
- `gateway/captures/` — typed manifest model and validation helpers.
- `gateway/jace_client.py` / `gateway/niagara_client.py` — safe JACE Ethernet
  source path.
- `gateway/demo_a2_mirror.py` — Niagara `/ord` to BACnet/IP demo mirror.
- `gateway/radio/`, `gateway/vwg_serial.py`, `proto/codec.py` — future/direct
  radio and legacy serial foundations.
- `tools/` — operator CLIs for capture mining, mapping reports, JACE probing,
  OTA baselines, diffs, label generation, point-map edits, and TX verification.
- `captures/manifest.yaml` — curated capture inventory.
- `docs/ota/known_ota_mappings.md` — confirmed mappings only.
- `docs/ota/candidate_ota_mappings.md` — generated conservative mapping report.
- `docs/a2_jace_ethernet_discovery.md` — JACE Ethernet discovery/read checklist.

## OTA Capture Mining

The current capture-mining workflow starts from existing nRF/Wireshark captures
instead of asking for more live testing first.

Raw `.pcapng` files and action logs should go under:

```text
captures/raw/
```

Raw files are gitignored. The manifest and docs are tracked.

Inventory captures and generate manifest stubs:

```powershell
python tools\capture_inventory.py captures\raw
```

Edit the printed stubs into:

```text
captures/manifest.yaml
```

Batch summarize all manifest captures:

```powershell
python tools\ota_batch_analyze.py --manifest captures\manifest.yaml
python tools\ota_batch_analyze.py --manifest captures\manifest.yaml --json
```

Generate action-window evidence for a capture with an action log:

```powershell
python tools\ota_action_windows.py --manifest captures\manifest.yaml --capture pass4_rtc --window 2.0
```

Generate the conservative mapping report:

```powershell
python tools\ota_mapping_report.py --manifest captures\manifest.yaml --format markdown --output docs\ota\candidate_ota_mappings.md
python tools\ota_mapping_report.py --manifest captures\manifest.yaml --format csv --output ota\mapping_report.csv
```

`ota/mapping_report.csv` is ignored. The tracked source-of-truth report is
`docs/ota/candidate_ota_mappings.md`.

### Mapping Rules

Do not promote inferred candidates automatically.

- `docs/ota/known_ota_mappings.md` contains confirmed network facts, command
  meanings, and confirmed semantic point mappings.
- `docs/ota/candidate_ota_mappings.md` may list rows inferred from action text,
  but those rows stay candidates until reviewed against action-window or A/B
  experiment evidence.
- `gateway/ota/pointmap.json` is the machine-readable point map used by the
  decoder.
- `tools/ota_validate.py` must pass after point-map changes.

Confirmed current semantic mappings:

- Family `0x08`, write `0x49` ↔ report `0x0A`:
  `occupied_heat_setpoint`.
- Family `0x08`, write `0x4B` ↔ report `0x2D`:
  `occupied_cool_setpoint`.
- Family `0x08`, write `0x4C` ↔ report `0x45`:
  candidate heat/setpoint mapping, semantic name not final.

## OTA Decoder Tools

Generate deterministic baseline artifacts:

```powershell
python tools\ota_baseline.py --pcap captures\raw\pass4_rtc.pcapng --device 0x143e --tag pass4_rtc
```

Decode a capture directly:

```powershell
python tools\ota_extract.py --pcap captures\raw\pass4_rtc.pcapng --device 0x143e
python tools\ota_extract.py --pcap captures\raw\pass4_rtc.pcapng --format jsonl --out ota\baselines\pass4_rtc.decoded.jsonl
```

Compare experiment catalogs:

```powershell
python tools\ota_diff.py --a ota\baselines\exp1_rtc_A.catalog.csv --b ota\baselines\exp1_rtc_B.catalog.csv
```

Propose write/report pairs from decoded output:

```powershell
python tools\ota_experiment.py --decoded ota\baselines\exp1_rtc_B.decoded.txt
```

Edit the point map safely:

```powershell
python tools\ota_pointmap_edit.py add-point --family 0x08 --code 0x4c --kind analog_x10 --label heat_setpoint_candidate_2
python tools\ota_pointmap_edit.py add-report-map --family 0x08 --report-code 0x45 --write-code 0x4c
python tools\ota_validate.py
```

## Safe JACE Ethernet Bridge

The safe live path keeps the borrowed JACE closed and untouched. It reads known
thermostat points over Ethernet using Niagara `/ord` and exposes/mirrors them
from this codebase.

Known Device2 read slice:

- `RoomTemperature`
- `OccupiedHeatingSetpoint`
- `OccupiedCoolingSetpoint`

Probe the JACE:

```powershell
python tools\jace_probe.py --config configs\demo_a2.yaml summary
python tools\jace_probe.py --config configs\demo_a2.yaml read-known-points --device-ref Device2 --json
```

Required environment variables for authenticated live reads:

```powershell
$env:NIAGARA_USER = "<username>"
$env:NIAGARA_PASS = "<password>"
```

Optional session-cookie fallback:

```powershell
$env:NIAGARA_SESSION = "<niagara_session_cookie>"
```

See `docs/a2_jace_ethernet_discovery.md` for confirmed ORD paths and remaining
manual checks.

## Demo A2: Niagara ORD to BACnet/IP

Run the local BACnet/IP mirror:

```powershell
python -m gateway.demo_a2_mirror --config configs\demo_a2.yaml
```

Open the status page:

```text
http://127.0.0.1:8090/health
```

Expected BACnet objects from the demo config:

- `analogInput,1101` — room temperature.
- `analogValue,1201` — occupied heating setpoint.
- `analogValue,1202` — occupied cooling setpoint.

Writes through the JACE path are still treated as experimental until a live
write path is accepted and readback confirms the applied value. Read mirroring
continues even if write attempts fail.

See `docs/demo_a2/README.md` for firewall and verification notes.

## Legacy / Future Direct-Radio Path

The direct-radio path is preserved but is not the current live path for borrowed
hardware.

Low-level probe examples:

```powershell
python -m gateway.vwg_probe raw-identify --port COM3
python -m gateway.vwg_probe raw-identify --port COM3 --baud 57600 --timeout 2 --retries 3
python -m gateway.vwg_probe raw-frames --port COM3
```

Legacy probe CLI:

```powershell
python tools\vwg_probe.py identify --port COM3
python tools\vwg_probe.py netcfg --port COM3
python tools\vwg_probe.py scan --port COM3 --start 1 --end 50 --point 0x1000
python tools\vwg_probe.py read --comm-addr 10 --point 0x1000 --port COM3
python tools\vwg_probe.py write --comm-addr 10 --point 0x1005 --value 72.0 --port COM3
```

Do not make the nRF an active coordinator or transmit on the live network until
protocol mappings and hardware safety assumptions are explicit.

## Git Hygiene

Tracked:

- `captures/README.md`
- `captures/manifest.yaml`
- `captures/manifest.example.yaml`
- source code under `gateway/`, `tools/`, and `tests/`
- curated OTA docs under `docs/ota/`

Ignored:

- raw `.pcapng` files
- `captures/raw/`
- generated `ota/` outputs
- point-map backup/noack scratch files

This keeps the repo reproducible without committing large or sensitive capture
payloads.
