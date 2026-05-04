# OTA Sniffer Decoder

## Filters

- PAN ID: `0x00D2`
- Profile ID: `0xC1E4`
- Cluster ID: `0x0002`
- Endpoints: gateway `0x0A` and thermostat `0x32`

## Capture Location

- Store pcapng files under `captures/` (gitignored).

## cmd2 Decoding Rules

- The `cmd=2` rest payload length is tolerated.
- If the rest length is not 4 bytes, the decoder still emits a line with `kind=unknown` and `payload=<rest.hex()>`.
- When the rest length is 4 bytes, enum detection is based on known per-prefix enum-code sets, not on `rest[2] == 0x00`.
- When the rest length is 3 bytes, it is decoded as `u8` (`[prefix][code][value]`).

## cmd3 ACK Variant

- If `rest_len >= 3` and `rest[2] == 0x00`, it is treated as `ack`.
- Any trailing bytes after the ACK marker are preserved as `extra=<hex>` in the output line.
- If `rest_len < 3`, the decoder emits `kind=unknown` with `reason=short_rest`.

## Code Normalization

- Some devices report point values using report codes.
- Decoding normalizes report codes back to their canonical write codes per prefix.

## Point Map

- `gateway/ota/pointmap.json` stores known point codes and optional labels.
- New `u8` codes are added via `ota_labelgen.py` with placeholder labels.
- `ota_labelgen.py` skips `ack` placeholders by default; pass `--include-ack` to include them.
- Candidate points (for experiments) should be added with safe placeholder labels.

## Catalog CSV

- `--catalog` writes an observed points registry for the selected stats scope.
- The CSV captures prefix/code/rest length and observed kinds without assigning semantic names yet.

## TAP and FCS Tolerance

- The decoder strips an 802.15.4 TAP header using `--tap-len` (default `28`). If the packet is shorter than the TAP length, it is skipped.
- If NWK parsing fails, the decoder retries exactly once with the last 2 bytes removed to account for a possible 802.15.4 FCS trailer.

## Baseline Artifacts

Use `tools/ota_baseline.py` to generate deterministic artifacts:

- `<tag>.decoded.txt` — decoded lines (chronological)
- `<tag>.catalog.csv` — grouped counts + sample payloads
- `<tag>.unknowns.txt` — only `kind=unknown`
- `<tag>.u8-snippets.txt` — only `kind=u8`
- Store artifacts under `ota/baselines/` (gitignored).

## Workflow

1. Decode a capture and generate artifacts:
   `python tools/ota_baseline.py --pcap captures/pass4_rtc.pcapng --device 0x143e --tag pass4_rtc`
2. Generate placeholder labels from catalogs:
   `python tools/ota_labelgen.py --map gateway/ota/pointmap.json --apply ota/baselines/pass4_rtc.catalog.csv`
3. Re-run `ota_extract.py` to see `label=` fields.
4. Compare experiments with:
   `python tools/ota_diff.py --a ota/baselines/pass3_rtc.catalog.csv --b ota/baselines/pass4_rtc.catalog.csv`

## Experiment Workflow (Recommended)

1. Capture A (idle) and B (after one change).
2. Decode to a file:
   `python tools/ota_extract.py --pcap captures/exp1_rtc_B.pcapng --device 0x143e --out ota/baselines/exp1_rtc_B.decoded.txt`
3. Propose write/report pairs:
   `python tools/ota_experiment.py --decoded ota/baselines/exp1_rtc_B.decoded.txt`
4. Add candidate points or report maps:
   `python tools/ota_pointmap_edit.py add-point --family 0x08 --code 0x4c --kind analog_x10 --label candidate_0x08_0x4c_analog_x10`
5. Re-run `ota_extract.py` and confirm `label=` fields.
6. Validate invariants:
   `python tools/ota_validate.py`

Known experiment outcomes:

- Exp1: `0x49` write ↔ `0x0A` report → `occupied_heat_setpoint`
- Exp2: `0x4B` write ↔ `0x2D` report → `occupied_cool_setpoint`
- Exp3: `0x4C` write ↔ `0x45` report → candidate (no semantic name yet)

## CLI Examples

The tools include a small sys.path bootstrap so they can be run directly with `python tools/...`.

```bash
# Default filtering (profile + cluster)
python tools/ota_extract.py --pcap /mnt/data/pass4_rtc.pcapng --device 0x143e

# Write decoded output directly
python tools/ota_extract.py --pcap /mnt/data/pass4_rtc.pcapng --device 0x143e --out ota/baselines/pass4_rtc.decoded.txt

# JSONL output
python tools/ota_extract.py --pcap /mnt/data/pass4_rtc.pcapng --device 0x143e --format jsonl --out ota/baselines/pass4_rtc.decoded.jsonl

# Disable filtering
python tools/ota_extract.py --pcap /mnt/data/pass4_rtc.pcapng --no-only-app

# Unknown cmd2 triage stats (default)
python tools/ota_extract.py --pcap /mnt/data/pass4_rtc.pcapng --device 0x143e --stats

# Include all messages in stats
python tools/ota_extract.py --pcap /mnt/data/pass4_rtc.pcapng --stats --stats-all

# Write observed point registry CSV (stats mode)
python tools/ota_extract.py --pcap /mnt/data/pass4_rtc.pcapng --stats --stats-all --catalog ota/baselines/pass4_rtc.catalog.csv

# Baseline artifacts
python tools/ota_baseline.py --pcap captures/pass4_rtc.pcapng --device 0x143e --tag pass4_rtc

# Generate placeholder labels
python tools/ota_labelgen.py --map gateway/ota/pointmap.json --apply ota/baselines/pass4_rtc.catalog.csv

# Include ack labels if desired
python tools/ota_labelgen.py --include-ack --map gateway/ota/pointmap.json --apply ota/baselines/pass4_rtc.catalog.csv

# Propose experiment pairings from decoded output
python tools/ota_experiment.py --decoded ota/baselines/exp1_rtc_B.decoded.txt

# Edit pointmap safely
python tools/ota_pointmap_edit.py add-point --family 0x08 --code 0x4c --kind analog_x10 --label candidate_0x08_0x4c_analog_x10
python tools/ota_pointmap_edit.py add-report-map --family 0x08 --report-code 0x45 --write-code 0x4c
python tools/ota_pointmap_edit.py rename-label --family 0x08 --code 0x4c --kind analog_x10 --label occupied_heat_setpoint --force

# Validate pointmap invariants
python tools/ota_validate.py

# Diff catalogs
python tools/ota_diff.py --a ota/baselines/pass3_rtc.catalog.csv --b ota/baselines/pass4_rtc.catalog.csv
```
