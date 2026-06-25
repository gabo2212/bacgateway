# Milestone Order

## 1. Synthetic Proof

Prove the missing software boundary without hardware:

```text
OtaPointEvent -> PointRegistry -> BACnet-visible value
```

Acceptance:

- A synthetic `OtaPointEvent(canonical_point="occupied_cool_setpoint", value=69.0)` updates the configured `OccupiedCoolingSetpoint` point.
- Unknown points and ACK-only events are ignored without crashing.
- BACnet server internals stay free of OTA parser imports.

## 2. Live RX Proof

Use the current receive-only nRF bridge to prove real OTA decoding:

```powershell
python tools\nrf_live_rx.py --port COMx --channel 15 --limit 50
```

Acceptance:

- The CLI prints `RX_FRAME` summaries.
- At least one decoded `OtaPointEvent`, `OtaAckEvent`, or preserved `OtaUnknownEvent` is visible when matching traffic exists.
- No TX support is introduced.

## 3. Live BACnet Mirror Proof

Wire the live event stream to the new adapter in a controlled runtime path:

```text
nRF captures a real setpoint report
-> live adapter emits OtaPointEvent
-> OTA registry adapter updates PointRegistry
-> BACnet client reads the Pi-presented value
```

Acceptance:

- The BACnet value changes after a real decoded OTA point event.
- The first proof uses only confirmed YAML mappings: `occupied_heat_setpoint` and `occupied_cool_setpoint`.
- Missing identity or unmapped thermostat state is logged and ignored, not fatal.

## 4. Parity Proof

Prove the nRF live path agrees with the offline capture path:

```text
nRF live RX window
parallel pcap decode
same decoded OTA events
```

Acceptance:

- Matching raw traffic produces equivalent decoded event records.
- Unknowns are preserved, not dropped.
- Short address changes are treated as identity cache changes, not new permanent devices.

## 5. Later TX / Write Path

Only after the passive path is proven:

```text
spare thermostat
lab-only PAN/channel
second sniffer
one defensible low-risk TX experiment
```

Hard limits:

- Never transmit on live PAN `0x00D2`.
- Never transmit on channel `15` in the live environment.
- Never start a second coordinator on the live Viconics network.
- Do not attempt write replacement until ACK/report/readback behavior is proven on isolated hardware.
