#!/usr/bin/env python3
"""Inventory pcapng captures and emit suggested manifest stubs.

Recursively scans a directory for ``.pcapng`` files, looks for paired
``.txt`` action logs, and prints a readable table plus YAML manifest stubs
that can be copied into ``captures/manifest.yaml``.

Does not parse pcap contents.
"""
from __future__ import annotations

import argparse
import logging
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional, Sequence

import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from gateway.captures.manifest import (  # noqa: E402
    find_action_log,
    suggest_capture_id,
    suggest_task_type,
)

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class InventoryRow:
    pcap: Path
    capture_id: str
    action_log: Optional[Path]
    task_type: str

    @property
    def has_action_log(self) -> bool:
        return self.action_log is not None


def scan_directory(root: Path) -> list[InventoryRow]:
    rows: list[InventoryRow] = []
    if not root.exists():
        return rows
    pcaps: Iterable[Path] = sorted(root.rglob("*.pcapng"))
    for pcap in pcaps:
        action_log = find_action_log(pcap)
        rows.append(
            InventoryRow(
                pcap=pcap,
                capture_id=suggest_capture_id(pcap),
                action_log=action_log,
                task_type=suggest_task_type(pcap, action_log),
            )
        )
    return rows


def format_table(rows: Sequence[InventoryRow], root: Path) -> str:
    if not rows:
        return f"No .pcapng files found under {root}"
    headers = ("capture_path", "capture_id", "action_log", "task_type")
    table_rows: list[tuple[str, str, str, str]] = []
    for row in rows:
        try:
            display_path = str(row.pcap.relative_to(root))
        except ValueError:
            display_path = str(row.pcap)
        table_rows.append(
            (
                display_path,
                row.capture_id,
                "yes" if row.has_action_log else "no",
                row.task_type,
            )
        )
    widths = [
        max(len(headers[i]), *(len(r[i]) for r in table_rows)) for i in range(4)
    ]
    sep = "  "
    lines = [sep.join(headers[i].ljust(widths[i]) for i in range(4))]
    lines.append(sep.join("-" * widths[i] for i in range(4)))
    for r in table_rows:
        lines.append(sep.join(r[i].ljust(widths[i]) for i in range(4)))
    return "\n".join(lines)


def format_manifest_stub(rows: Sequence[InventoryRow]) -> str:
    if not rows:
        return "captures: []\n"
    entries: list[dict[str, object]] = []
    for row in rows:
        entry: dict[str, object] = {
            "capture_id": row.capture_id,
            "pcap": _repo_relative(row.pcap),
        }
        if row.action_log is not None:
            entry["action_log"] = _repo_relative(row.action_log)
        entry["task_type"] = row.task_type
        entries.append(entry)
    return yaml.safe_dump({"captures": entries}, sort_keys=False, allow_unicode=True)


def _repo_relative(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(ROOT)).replace("\\", "/")
    except ValueError:
        return str(path).replace("\\", "/")


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument(
        "directory",
        nargs="?",
        default="captures/raw",
        help="Directory to scan recursively (default: captures/raw)",
    )
    parser.add_argument(
        "--no-stubs",
        action="store_true",
        help="Skip the YAML manifest stub block",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.WARNING)

    root = Path(args.directory)
    rows = scan_directory(root)

    print(format_table(rows, root))
    if not args.no_stubs:
        print()
        print("# Suggested manifest entries (copy into captures/manifest.yaml):")
        print(format_manifest_stub(rows).rstrip())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
