# Captures

This directory holds nRF / Wireshark captures and matching action logs used
to mine the OTA protocol.

## Layout

- `raw/` — newly recorded `.pcapng` files. Drop new captures here before
  running the inventory tool. Subdirectories are walked recursively.
- `manifest.yaml` — curated inventory of captures used by batch tools.
  Editable. Not overwritten by tooling.
- `manifest.example.yaml` — reference manifest with all supported fields.

Existing legacy captures live in the parent `captures/` directory and under
`../../WiresharkN/`. The manifest may reference any path (relative or
absolute); tools resolve paths relative to the repo root.

## Action logs

Each capture may have a paired `.txt` action log with operator notes. The
inventory tool looks for a sibling `.txt` with the same basename (e.g.
`pass4_rtc.pcapng` ↔ `pass4_rtc.txt`).

Action logs are expected to use `mm:ss` timestamps relative to capture
start. Lines without a timestamp prefix are treated as free-form notes.

Example action log:

```
00:00 idle
00:12 ping device 0x143E
00:34 setpoint write occupied_heat_setpoint = 21.5
01:02 occupancy override = unoccupied
```

## Workflow

1. Place new captures into `captures/raw/`.
2. Run `python tools/capture_inventory.py captures/raw` to print suggested
   manifest entries.
3. Copy/edit those entries into `captures/manifest.yaml`.
4. Run `python tools/ota_batch_analyze.py --manifest captures/manifest.yaml`
   for a per-capture decoded summary.
5. For action-correlation, run
   `python tools/ota_action_windows.py --manifest captures/manifest.yaml --capture <capture_id>`.

## Manifest format

See `manifest.example.yaml`. Each entry supports:

- `capture_id` (string, required)
- `pcap` (string path, required)
- `action_log` (string path, optional)
- `device_label` (string, optional)
- `short_addr` (string like `0x0001`, optional)
- `eui64` (string, optional)
- `task_type` (one of: `idle`, `ping`, `identify`, `point_viewer`,
  `setpoint_write`, `occupancy_write`, `outdoor_temp_override`, `mode_fan`,
  `unknown`)
- `notes` (string, optional)

## Privacy / Git hygiene

Raw pcapng files are gitignored. Only the manifest, README, and example
manifest are tracked.
