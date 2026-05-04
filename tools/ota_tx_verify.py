#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from gateway.ota.tx_template import (
    TxTemplate,
    build_from_template,
    diff_offsets,
    extract_templates_from_pcap,
    infer_volatile_offsets,
)


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


def _matching_frames(templates: list[TxTemplate], seed: TxTemplate) -> list[bytes]:
    seed_cmd = seed.mac_frame[seed.rest_off - 1]
    seed_rest = seed.mac_frame[seed.rest_off : seed.rest_off + seed.rest_len]
    result: list[bytes] = []
    for template in templates:
        cmd = template.mac_frame[template.rest_off - 1]
        rest = template.mac_frame[template.rest_off : template.rest_off + template.rest_len]
        if cmd == seed_cmd and rest == seed_rest:
            result.append(template.mac_frame)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Verify template rebuild against captured frame")
    parser.add_argument("--pcap", required=True, help="Capture path used to extract templates")
    parser.add_argument("--device", required=True, help="Device short address (e.g. 0x143e)")
    parser.add_argument("--pick", default="first", help="Template selector: first|last|index=N")
    args = parser.parse_args()

    device_short = _parse_int(args.device)
    templates = [t for t in extract_templates_from_pcap(Path(args.pcap)) if t.device_short == device_short]
    if not templates:
        raise SystemExit("no gw->dev cmd2/cmd3 templates found for requested device")
    idx, template = _pick_template(templates, args.pick)

    original = template.mac_frame
    cmd = original[template.rest_off - 1]
    rest = original[template.rest_off : template.rest_off + template.rest_len]
    rebuilt = build_from_template(template, cmd_id=cmd, rest=rest)
    changed = diff_offsets(original, rebuilt)
    if not changed:
        print(
            " ".join(
                [
                    f"selected_index={idx}",
                    f"device=0x{template.device_short:04x}",
                    "result=exact_match",
                    "volatile_offsets=none",
                ]
            )
        )
        return

    pool = _matching_frames(templates, template)
    volatile = infer_volatile_offsets(pool)
    volatile_set = set(volatile)
    non_volatile = [offset for offset in changed if offset not in volatile_set]
    print(
        " ".join(
            [
                f"selected_index={idx}",
                f"device=0x{template.device_short:04x}",
                "result=diff",
                f"changed_offsets={','.join(str(offset) for offset in changed)}",
                f"volatile_offsets={','.join(str(offset) for offset in volatile) if volatile else 'none'}",
            ]
        )
    )
    if non_volatile:
        raise SystemExit(
            "non-volatile mismatches: " + ",".join(str(offset) for offset in non_volatile)
        )
    print("result=tolerated_by_volatility")


if __name__ == "__main__":
    main()
