# Phase 1B nRF Firmware Bring-Up

Phase 1B adds the first nRF52840 receive-only firmware bridge. The firmware is
safe to run near the existing JACE/VWG because it only listens. It does not
join, associate, coordinate, or transmit on the live Viconics PAN.

## Chain Under Test

```text
802.15.4 radio RX
-> raw PSDU + RSSI/LQI/timestamp
-> USB CDC ACM serial
-> gateway/radio/nrf_bridge_client.py
-> tools/nrf_live_rx.py
```

## Firmware Tree

```text
firmware/nrf_vwg_bridge_rx/
```

The bridge uses Nordic's `nrf_802154` receive API, defaults to channel `15`,
enables promiscuous receive, and emits:

- `HELLO_RESP` with `cap=RX_ONLY`.
- `RX_FRAME` with `timestamp_us(8 LE) | channel(1) | rssi_dbm(1 signed) |
  lqi(1 signed) | raw_psdu(N)`.

`TX_RAW` remains a reserved protocol type only. There is no CLI or firmware
command that sends a radio frame.

## Build And Flash

Use nRF Connect SDK / Zephyr from an initialized west workspace.
The app includes board overlays for `nrf52840dk_nrf52840` and
`nrf52840dongle_nrf52840` so the USB CDC ACM device exists at boot.

nRF52840 DK:

```powershell
west build -b nrf52840dk_nrf52840 firmware/nrf_vwg_bridge_rx -d build\nrf_vwg_bridge_rx_dk
west flash -d build\nrf_vwg_bridge_rx_dk
```

nRF52840 Dongle:

```powershell
west build -b nrf52840dongle_nrf52840 firmware/nrf_vwg_bridge_rx -d build\nrf_vwg_bridge_rx_dongle
```

The DK is the easier first target because SWD flash/debug is straightforward.
The dongle may require bootloader/DFU flashing through nRF Connect Programmer or
board-specific DFU tooling.

## Host Smoke Test

Windows:

```powershell
python tools\nrf_live_rx.py --port COM7 --channel 15 --limit 50
```

Linux:

```bash
python tools/nrf_live_rx.py --port /dev/ttyACM0 --channel 15 --limit 50
```

Expected output starts with bridge connection text and then decoded events when
the live frames match the offline decoder:

```text
RX_FRAME channel=15 rssi=-xx lqi=xx len=...
OtaPointEvent / OtaAckEvent / OtaUnknownEvent
```

If raw frames arrive but no Viconics events decode, keep the firmware unchanged
and debug filter, FCS, channel, and decoder alignment against known captures.

## Validation Boundary

Python CI does not build firmware and requires no radio hardware:

```powershell
python -m pytest tests/ -q
python tools\nrf_live_rx.py --help
```
