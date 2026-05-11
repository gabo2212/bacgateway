import argparse
import logging
import sys
from pathlib import Path
import json
import dataclasses

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).parent.parent))

from gateway.radio.nrf_bridge_client import NrfBridgeClient
from gateway.radio.nrf_bridge_proto import FrameType, RxFramesPayload
from gateway.radio.live_adapter import LiveAdapter
from gateway.ota.events import OtaPointEvent, OtaAckEvent, OtaUnknownEvent

def main():
    parser = argparse.ArgumentParser(description="Phase 1A: Receive-only nRF live bridge")
    parser.add_argument("--port", required=True, help="Serial port (e.g. COM7 or /dev/ttyACM0)")
    parser.add_argument("--channel", type=int, required=True, help="IEEE 802.15.4 channel")
    parser.add_argument("--format", choices=["text", "jsonl"], default="text", help="Output format")
    parser.add_argument("--limit", type=int, help="Exit after N events")
    args = parser.parse_args()

    logging.basicConfig(level=logging.WARNING)

    print("WARNING: This is a receive-only live bridge. Active TX is NOT enabled.", file=sys.stderr)
    print(f"Connecting to {args.port} on channel {args.channel}...", file=sys.stderr)

    client = NrfBridgeClient(port=args.port)
    adapter = LiveAdapter()

    try:
        client.connect()
    except Exception as e:
        print(f"Failed to connect: {e}", file=sys.stderr)
        sys.exit(1)

    count = 0
    try:
        for frame in client.read_frames():
            if frame.frame_type == FrameType.RX_FRAME:
                rx_payload = RxFramesPayload.decode(frame.payload)
                event = adapter.process_rx_frame(rx_payload)
                if event:
                    if args.format == "jsonl":
                        print(json.dumps(dataclasses.asdict(event)))
                    else:
                        print(f"Event: {event}")
                    
                    count += 1
                    if args.limit and count >= args.limit:
                        break
            elif frame.frame_type == FrameType.ERROR:
                print(f"Bridge Error: {frame.payload.decode(errors='replace')}", file=sys.stderr)
    except KeyboardInterrupt:
        print("\nExiting...", file=sys.stderr)
    finally:
        client.disconnect()

if __name__ == "__main__":
    main()
