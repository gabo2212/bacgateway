# Demo A2: Niagara ORD to BACnet/IP mirror

This demo mirrors selected Niagara `/ord` oBIX points into a local BACnet/IP device without requiring Niagara BACnet export licensing.

## Environment variables

Set credentials using environment variables (names can be overridden in `configs/demo_a2.yaml`):

- `NIAGARA_USER`
- `NIAGARA_PASS`
- Optional fallback session cookie: `NIAGARA_SESSION`

## Windows firewall (BACnet UDP 47808)

Run once as Administrator:

```powershell
netsh advfirewall firewall add rule name="BACnet UDP 47808" dir=in action=allow protocol=UDP localport=47808
```

## Run

```powershell
python -m gateway.demo_a2_mirror --config configs/demo_a2.yaml
```

If PyYAML is unavailable, you can pass a JSON config file instead.

## Verify

1. Open `http://127.0.0.1:8090/health` and confirm values/timestamps refresh every second.
2. Use YABE or BACnet Explorer to discover Device `5001`.
3. Read points and confirm values match Niagara:
   - `analogInput,1101` room temperature
   - `analogValue,1201` occupied heating setpoint
   - `analogValue,1202` occupied cooling setpoint
4. Write `AV1201` and `AV1202`:
   - Success path: Niagara setpoint updates and poll loop confirms
   - Failure path: write error is logged, BACnet value reverts, reads continue

## Notes

- Read mirroring continues even if Niagara write attempts fail.
- If auth expires (redirect/login page), the service re-authenticates and retries once.
- If SCRAM login fails, cookie injection mode via `NIAGARA_SESSION` can still be used.
