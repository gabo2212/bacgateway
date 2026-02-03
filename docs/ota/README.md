# OTA Sniffer Decoder

## Filters

- PAN ID: `0x00D2`
- Profile ID: `0xC1E4`
- Cluster ID: `0x0002`
- Endpoints: gateway `0x0A` and thermostat `0x32`

## cmd2 Decoding Rules

- The `cmd=2` rest payload length is tolerated.
- If the rest length is not 4 bytes, the decoder still emits a line with `kind=unknown` and `payload=<rest.hex()>`.
- When the rest length is 4 bytes, enum detection is based on known per-prefix enum-code sets, not on `rest[2] == 0x00`.

## TAP and FCS Tolerance

- The decoder strips an 802.15.4 TAP header using `--tap-len` (default `28`). If the packet is shorter than the TAP length, it is skipped.
- If NWK parsing fails, the decoder retries exactly once with the last 2 bytes removed to account for a possible 802.15.4 FCS trailer.

## CLI Examples

```bash
# Default filtering (profile + cluster)
python tools/ota_extract.py --pcap /mnt/data/pass4_rtc.pcapng --device 0x143e

# Disable filtering
python tools/ota_extract.py --pcap /mnt/data/pass4_rtc.pcapng --no-only-app
```
