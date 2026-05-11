# Known OTA Mappings

Human-readable companion to `gateway/ota/pointmap.yaml`. Only mappings
already backed by existing code, docs, or capture evidence are listed here.
Do not invent new mappings in this file; promote an entry only after the
evidence rules below are satisfied.

## Confirmed network facts

Anchors observed in the existing Wireshark notes
(`WiresharkN/viconics_w_ota_reverse_engineering_passes_1_4.md`) and used
across the OTA decoder code:

- Channel: `15`
- PAN ID: `0x00D2`
- Coordinator / gateway short address: `0x0000`
- Thermostat short addresses observed: `0x0001`, `0x143E`
- Profile ID: `0xC1E4`
- Cluster ID: `0x0002`
- Endpoint pair: gateway `0x0A`, thermostat `0x32`

## Confirmed command meanings

From `gateway/ota/app.py` and `docs/ota/README.md`:

- `cmd 0`: identify request (`identify_req`)
- `cmd 1`: identify response (`identify_rsp`)
- `cmd 2`: point data
  - 4-byte rest, enum prefix/code in family enum set: `enum`
    (`prefix | code | 0x00 | enum_value`)
  - 4-byte rest otherwise: `analog_x10`
    (`prefix | code | u16_be / 10.0`)
  - 3-byte rest: `u8` (`prefix | code | u8_value`)
  - other lengths: `unknown` (preserved, not dropped)
- `cmd 3`: ACK / write response
  - `prefix | code | 0x00` followed by optional trailing bytes captured as
    `extra=<hex>`
  - `rest_len < 3`: `unknown` with `reason=short_rest`

## Confirmed point mappings

From `gateway/ota/pointmap.yaml` (curated entries with non-placeholder
labels) and `docs/ota/README.md` experiment outcomes:

- Family `0x08`:
  - write `0x49` ↔ report `0x0A` → `occupied_heat_setpoint` (Exp1)
  - write `0x4B` ↔ report `0x2D` → `occupied_cool_setpoint` (Exp2)
  - write `0x4C` ↔ report `0x45` → `heat_setpoint_candidate_2` (Exp3,
    candidate; semantic name not finalized)

Report-to-write normalization for both families is preserved in
`pointmap.yaml` by ensuring `report_code` is mapped inside the same entry.

## Candidate mappings

These are present in `pointmap.yaml` with placeholder labels (`auto_…` or
`sp_…`) and have not yet been promoted to confirmed semantic names. Treat
them as evidence anchors only; do not rename them without action-log or
A/B experiment evidence.

- All `auto_<family>_<code>_<kind>` entries
- All `sp_<code>` analog entries (setpoint-shaped, exact role unconfirmed)
- All `occ_<code>` enum entries (occupancy-shaped, exact role unconfirmed)

## Unknown payload groups

Surfaced by `tools/ota_batch_analyze.py` and the `--stats`/catalog flow of
`tools/ota_extract.py`. Categories tracked but not promoted:

- `cmd=2` frames whose `(prefix, code, rest_len)` tuple is not in
  `pointmap.yaml`
- `cmd=3` frames with `rest_len < 3` (`reason=short_rest`)
- Vendor frames whose `cmd_id` is neither `0`, `1`, `2`, nor `3`
- Non-vendor frames (any frame not matching profile `0xC1E4` /
  cluster `0x0002`); counted as `total_frames - matching_vendor_frames`

These must continue to be counted, not dropped silently.

## Evidence rules

A mapping may only be promoted from candidate to confirmed when **at least
one** of the following is true:

1. **Action-log evidence**: a paired action log records an operator action
   (e.g. setpoint write) and the OTA frames within +/- the action window
   (`tools/ota_action_windows.py`) match the proposed mapping.
2. **Repeated A/B experiment evidence**: an idle baseline capture (A) and a
   single-change capture (B) differ exactly on the proposed write/report
   pair, reproduced across at least two independent capture sessions.
3. **Cross-capture corroboration**: the same `(prefix, code)` pair appears
   with the same `kind` and consistent ranges across at least two
   captures, *and* its semantic name is corroborated by a Wireshark note
   already committed to the repo.

Additional rules:

- Report codes must be normalized through
  a `report_code` property mapped to `write_code` in `pointmap.yaml` before the write
  code is renamed.
- Candidate labels must remain clearly marked (`candidate_…`,
  `auto_…`, `sp_…`, `occ_…`) until promoted.
- `tools/ota_validate.py` must pass after any promotion.
- Removing or renaming a confirmed label requires a corresponding update
  in this file with the new evidence reference.
