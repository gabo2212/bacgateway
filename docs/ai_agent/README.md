# AI Agent Packet: Viconics nRF to BACnet

This directory is a compact handoff packet for an AI agent working on:

```text
legacy Viconics W thermostat -> nRF52/nRF52840 USB radio path -> Python gateway -> BACnet/IP from Raspberry Pi
```

Read in this order:

1. `thermostat_nrf_bacnet_handoff.md` - the goal, confirmed facts, current implementation, missing pieces, and safe next work.
2. `evidence_index.md` - file-by-file index of the useful repo/workspace evidence.
3. `agent_task_brief.md` - a scoped prompt/task brief you can paste into another agent.

Hard boundaries:

- Do not transmit on the live Viconics PAN `0x00D2` or channel `15`.
- Do not start a stock Zigbee coordinator against the live network.
- Treat the current nRF firmware as receive-only by design.
- Treat `eui64` as stable device identity; treat 16-bit short addresses as volatile cache/evidence.
- Do not invent point mappings. Promote mappings only from action-window or repeated A/B capture evidence.
