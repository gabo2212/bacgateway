# BACnet Viconics Gateway

This project bridges Viconics VWG wireless thermostats to BACnet/IP using the extracted wireless protocol.

## Setup

```
pip install -r requirements.txt
```

## Configuration

- `points.yaml`: Thermostats and BACnet object mapping. Each thermostat should define `comm_addr` and optional `logical_id`; points include `point_addr_hex` and BACnet object metadata. Use `poll_points` to restrict polling to a subset of addresses.
- `config/radio.yaml`: Serial radio configuration.

Example `config/radio.yaml`:

```
serial_port: "COM3"
baud: 57600
rtscts: true
poll_interval_seconds: 5
```

## Run the probe

```
python tools/vwg_probe.py identify --port COM3
python tools/vwg_probe.py netcfg --port COM3
python tools/vwg_probe.py read --comm-addr 10 --point 0x1000 --port COM3
python tools/vwg_probe.py write --comm-addr 10 --point 0x1005 --value 72.0 --port COM3
```

## Run the gateway

```
python gateway/bacnet_server.py --config points.yaml --radio-config config/radio.yaml
```

## Tests

```
python -m unittest discover -s tests
```
