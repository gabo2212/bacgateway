# Phase A2 — JACE Ethernet Discovery Checklist

This document anchors discovery work to already-confirmed facts in the repo and lists
what has been confirmed and what still requires live verification against `192.168.15.12`.

---

## Already Confirmed (repo-backed facts)

These facts exist in `configs/demo_a2.yaml` and have been exercised by
`gateway/demo_a2_mirror.py` against the live JACE.

| Field | Confirmed value |
|---|---|
| Host | `192.168.15.12` |
| HTTP port | `80` |
| Scheme | `http` |
| Login mode | `cookieDigest` |
| Known device | `Device2` |
| Proven read path | `/ord` via `NiagaraClient.read_real()` |

### Confirmed readable ORD paths (Device2)

```
station:|slot:/Drivers/WirelessTstatNetwork/Device2/points/RoomTemperature/out
station:|slot:/Drivers/WirelessTstatNetwork/Device2/points/OccupiedHeatingSetpoint/out
station:|slot:/Drivers/WirelessTstatNetwork/Device2/points/OccupiedCoolingSetpoint/out
```

### Known-facts config shape (from `configs/demo_a2.yaml`)

```yaml
jace:
  host: 192.168.15.12
  port: 80
  login_scheme: cookieDigest
  device_ref: Device2
  points:
    - RoomTemperature
    - OccupiedHeatingSetpoint
    - OccupiedCoolingSetpoint
```

---

## Still to Verify (manual probing required before finalizing A3)

Use `python tools/jace_probe.py --config configs/demo_a2.yaml <subcommand>` for each item.

### Network access

- [ ] `ping` — confirm TCP reachability from dev PC / Pi to `192.168.15.12:80`
- [ ] `probe-fox` — confirm whether Fox port 1911 is reachable (non-blocking; HTTP path is proven)

### HTTP / login

- [ ] `probe-http` — confirm HTTP 200 from root, verify login page shape
- [ ] `probe-login` — confirm `cookieDigest` is still the active scheme (not changed to SCRAM)

### oBIX / ORD

- [ ] `probe-obix` — confirm `/obix/` root endpoint responds (nice-to-have for nav; /ord is already proven)
- [ ] Verify whether additional devices beyond `Device2` are visible under
  `station:|slot:/Drivers/WirelessTstatNetwork/`
- [ ] Confirm full point list for Device2 (are there points beyond the 3 confirmed ones?)

### Firmware / model (optional, not A3 blockers)

- [ ] Firmware version string (visible in JACE web UI header or `/ord` root response)
- [ ] Model string (e.g. JACE-8000 vs JACE-2)

### BACnet/IP on the JACE itself (separate question from our BACnet server)

- [ ] Is BACnet/IP enabled on the JACE?
- [ ] What device ID does the JACE present?
- [ ] Is it exposing the same thermostats that `/ord` shows?
- [ ] Is reading the JACE's own BACnet server useful, or is `/ord` the better path?

---

## Phase A3 Status

### A3a — scaffold and tests ✅ complete

`JaceClient` ABC, `JaceEthernetClient` constructor wiring, `JaceStubClient`, and
`tools/jace_probe.py` subcommands (ping, probe-http, probe-login, probe-obix,
probe-fox, summary) are implemented and covered by 74 passing network-free tests.

### A3 — initial 3-point read slice ✅ implemented in code

`JaceEthernetClient.read_points()` is now implemented for the 3 confirmed Device2 points
(RoomTemperature, OccupiedHeatingSetpoint, OccupiedCoolingSetpoint) using
`NiagaraClient.read_real()` as the underlying transport.  The `read-known-points`
CLI subcommand wires this end-to-end.

**Live verification against `192.168.15.12` is still pending** (requires the borrowed
JACE to be reachable and `NIAGARA_USER` / `NIAGARA_PASS` to be set).

Expected live values based on previous confirmed readings:

| Point | Expected value |
| --- | --- |
| RoomTemperature | ≈ 88.2 |
| OccupiedHeatingSetpoint | ≈ 86.3 |
| OccupiedCoolingSetpoint | ≈ 88.3 |

Gate conditions that were already satisfied before implementation began:

1. `probe-login` returns `login_ok=True` with `scheme=cookieDigest` ✓
2. `probe-obix` confirms `ord_probe` returns a live value for RoomTemperature ✓
3. Point names are fixed to the 3 confirmed ones — no arbitrary ORD browsing ✓

---

## Probe command reference

```bash
# Check TCP reachability
python tools/jace_probe.py --config configs/demo_a2.yaml ping

# Check HTTP response and login page shape
python tools/jace_probe.py --config configs/demo_a2.yaml probe-http

# Attempt login and detect scheme
python tools/jace_probe.py --config configs/demo_a2.yaml probe-login

# Check /obix/ and /ord/ endpoints (reads live RoomTemperature)
python tools/jace_probe.py --config configs/demo_a2.yaml probe-obix

# Check Fox port reachability
python tools/jace_probe.py --config configs/demo_a2.yaml probe-fox

# Full summary (all probes)
python tools/jace_probe.py --config configs/demo_a2.yaml summary

# A3 3-point read (implemented — requires live JACE and credentials)
python tools/jace_probe.py --config configs/demo_a2.yaml read-known-points \
  --device-ref Device2 \
  --points RoomTemperature OccupiedHeatingSetpoint OccupiedCoolingSetpoint

# Same with JSON output
python tools/jace_probe.py --config configs/demo_a2.yaml read-known-points \
  --device-ref Device2 --json
```

---

## Architecture note

`JaceEthernetClient` wraps `gateway/niagara_client.NiagaraClient` internally.
No second login/read stack is built.  Stage B will replace `JaceEthernetClient`
with the direct-radio stack (`RadioSession` + `VwgSerialTransport` + codec)
while keeping the same `JaceClient` ABC contract.

