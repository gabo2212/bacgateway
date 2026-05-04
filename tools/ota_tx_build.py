#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from gateway.ota.tx_template import TxTemplate, build_from_template, diff_byte_ranges, extract_templates_from_pcap


def _parse_int(text: str) -> int:
    value = text.strip().lower()
    if value.startswith("0x"):
        return int(value, 16)
    if any(char in "abcdef" for char in value):
        return int(value, 16)
    return int(value)


def _pick_template(templates: list[TxTemplate], pick: str) -> tuple[int, TxTemplate]:
    if not templates:
        raise ValueError("no templates available")
    pick_norm = pick.strip().lower()
    if pick_norm == "first":
        return 0, templates[0]
    if pick_norm == "last":
        return len(templates) - 1, templates[-1]
    if pick_norm.startswith("index="):
        idx = int(pick_norm.split("=", 1)[1])
        if idx < 0 or idx >= len(templates):
            raise ValueError(f"index out of range: {idx}")
        return idx, templates[idx]
    raise ValueError(f"invalid --pick value: {pick}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Build OTA TX MAC frame from extracted template")
    parser.add_argument("--pcap", required=True, help="Capture path used to extract templates")
    parser.add_argument("--device", required=True, help="Device short address (e.g. 0x143e)")
    parser.add_argument("--pick", default="first", help="Template selector: first|last|index=N")
    parser.add_argument("--cmd", required=True, type=int, choices=[2, 3], help="Command id to patch")
    parser.add_argument("--rest", required=True, help="Rest payload hex")
    parser.add_argument("--emit-bin", help="Write built MAC frame bytes to path")
    args = parser.parse_args()

    device_short = _parse_int(args.device)
    rest = bytes.fromhex(args.rest.strip())

    templates = [t for t in extract_templates_from_pcap(Path(args.pcap)) if t.device_short == device_short]
    if not templates:
        raise SystemExit("no gw->dev cmd2/cmd3 templates found for requested device")
    idx, template = _pick_template(templates, args.pick)

    original_cmd = template.mac_frame[template.rest_off - 1]
    original_rest = template.mac_frame[template.rest_off : template.rest_off + template.rest_len]
    built = build_from_template(template, cmd_id=args.cmd, rest=rest)

    print(
        " ".join(
            [
                f"selected_index={idx}",
                f"device=0x{template.device_short:04x}",
                f"direction={template.direction}",
                f"cmd_original={original_cmd}",
                f"rest_original={original_rest.hex()}",
                f"offset_nwk={template.nwk_off}",
                f"offset_aps={template.aps_off}",
                f"offset_app={template.app_off}",
                f"offset_rest={template.rest_off}",
                f"rest_len={template.rest_len}",
            ]
        )
    )
    print(f"built_len={len(built)}")
    print(f"built_hex={built.hex()}")
    diffs = diff_byte_ranges(template.mac_frame, built)
    if not diffs:
        print("diff=none")
    else:
        for diff in diffs:
            print(
                " ".join(
                    [
                        f"diff_range={diff.start}:{diff.end}",
                        f"before={diff.before.hex()}",
                        f"after={diff.after.hex()}",
                    ]
                )
            )
    if args.emit_bin:
        out_path = Path(args.emit_bin)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(built)
        print(f"emit_bin={out_path}")


if __name__ == "__main__":
    main()
