# nRF VWG Bridge RX Firmware

Phase 1B firmware skeleton for an nRF52840 receive-only bridge. It listens for
IEEE 802.15.4 frames on channel 15 and streams raw PSDUs to the Python host over
USB CDC ACM using the frame format implemented by
`gateway/radio/nrf_bridge_proto.py`.

## Safety

- RX only.
- No active coordinator behavior.
- No joining or association.
- No live PAN modification.
- No firmware command shell.
- `TX_RAW` is reserved in the host protocol, but this firmware does not expose
  a transmit path.

The existing JACE/VWG remains the only coordinator on the live Viconics PAN.

## Protocol Contract

Serial frames are:

```text
magic(2) | version(1) | type(1) | flags(1) | seq(1) | length(2) | payload | crc32(4)
```

- `magic`: `VW`
- `version`: `1`
- CRC: standard IEEE CRC-32 over header plus payload
- `HELLO_RESP`: ASCII capability payload, including `cap=RX_ONLY`
- `RX_FRAME`: `timestamp_us(8 LE) | channel(1) | rssi_dbm(1 signed) | lqi(1 signed) | raw_psdu(N)`

That `RX_FRAME` payload is the exact layout consumed by
`RxFramesPayload.decode()` on the host.

## Build

Use nRF Connect SDK / Zephyr from an initialized west workspace.
Board overlays under `boards/` instantiate the USB CDC ACM device for the DK
and dongle targets.

nRF52840 DK:

```powershell
west build -b nrf52840dk_nrf52840 firmware/nrf_vwg_bridge_rx -d build\nrf_vwg_bridge_rx_dk
west flash -d build\nrf_vwg_bridge_rx_dk
```

nRF52840 Dongle:

```powershell
west build -b nrf52840dongle_nrf52840 firmware/nrf_vwg_bridge_rx -d build\nrf_vwg_bridge_rx_dongle
```

The DK is easier for first bring-up and SWD debugging. The dongle commonly uses
its bootloader/DFU flow instead of `west flash`; use the nRF Connect Programmer
or the matching DFU tooling for that board.

## Host Smoke Test

Windows:

```powershell
python tools\nrf_live_rx.py --port COM7 --channel 15 --limit 50
```

Linux:

```bash
python tools/nrf_live_rx.py --port /dev/ttyACM0 --channel 15 --limit 50
```

Raw `RX_FRAME` output without decoded Viconics events is still useful: it proves
the radio-to-host chain is alive and leaves decoder/filter alignment as the next
debug target.
