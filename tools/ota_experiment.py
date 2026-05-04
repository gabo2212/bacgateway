#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import logging
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from gateway.ota.decoded import DecodedLine, load_decoded_lines

LOGGER = logging.getLogger(__name__)

SUPPORTED_KINDS = {"analog_x10", "enum", "u8"}


@dataclass(frozen=True)
class Proposal:
    family: int
    write_code: int
    report_code: int
    kind: str
    value: float | int
    dt_ms: float
    confidence: float
    evidence_lines: list[str]


@dataclass
class _PairStats:
    count: int
    min_dt_ms: float
    value: float | int
    evidence_lines: list[str]


def _parse_hex(text: str) -> Optional[int]:
    if not text:
        return None
    try:
        return int(text, 0)
    except ValueError:
        return None


def _line_matches(
    line: DecodedLine,
    only_family: Optional[int],
    only_kind: Optional[str],
    only_label: Optional[str],
    only_unlabeled: bool,
) -> bool:
    if only_family is not None and line.prefix != only_family:
        return False
    if only_kind is not None and line.kind != only_kind:
        return False
    if only_unlabeled and line.label:
        return False
    if only_label is not None and (line.label is None or only_label not in line.label):
        return False
    return True


def propose_pairs(
    lines: list[DecodedLine],
    window_ms: int = 5000,
    min_confidence: float = 0.6,
    only_family: Optional[int] = None,
    only_kind: Optional[str] = None,
    only_label: Optional[str] = None,
    only_unlabeled: bool = False,
) -> list[Proposal]:
    filtered = [
        line
        for line in lines
        if _line_matches(line, only_family, only_kind, only_label, only_unlabeled)
    ]

    writes = [
        line
        for line in filtered
        if line.direction == "gw->dev"
        and line.cmd == 2
        and line.kind in SUPPORTED_KINDS
        and line.value is not None
        and line.prefix is not None
        and line.code is not None
    ]
    reports = [
        line
        for line in filtered
        if line.direction == "dev->gw"
        and line.cmd == 2
        and line.kind in SUPPORTED_KINDS
        and line.value is not None
        and line.prefix is not None
        and line.code is not None
    ]
    writes.sort(key=lambda item: item.t)
    reports.sort(key=lambda item: item.t)

    window_sec = window_ms / 1000.0
    pair_stats: dict[tuple[int, int, int, str], _PairStats] = {}
    write_counts: dict[tuple[int, int, str], int] = {}

    report_start = 0
    for write in writes:
        write_key = (write.prefix, write.code, write.kind)
        write_counts[write_key] = write_counts.get(write_key, 0) + 1
        while report_start < len(reports) and reports[report_start].t < write.t:
            report_start += 1
        best_report: Optional[DecodedLine] = None
        best_dt: Optional[float] = None
        idx = report_start
        while idx < len(reports):
            report = reports[idx]
            dt = report.t - write.t
            if dt > window_sec:
                break
            if report.prefix != write.prefix:
                idx += 1
                continue
            if report.kind != write.kind:
                idx += 1
                continue
            if report.value != write.value:
                idx += 1
                continue
            if best_dt is None or dt < best_dt:
                best_report = report
                best_dt = dt
            idx += 1
        if best_report is None or best_dt is None:
            continue
        pair_key = (write.prefix, write.code, best_report.code, write.kind)
        dt_ms = best_dt * 1000.0
        stats = pair_stats.get(pair_key)
        if stats is None:
            pair_stats[pair_key] = _PairStats(
                count=1,
                min_dt_ms=dt_ms,
                value=write.value,
                evidence_lines=[write.raw, best_report.raw],
            )
        else:
            stats.count += 1
            if dt_ms < stats.min_dt_ms:
                stats.min_dt_ms = dt_ms
                stats.evidence_lines = [write.raw, best_report.raw]

    proposals: list[Proposal] = []
    for key, stats in pair_stats.items():
        family, write_code, report_code, kind = key
        total = write_counts.get((family, write_code, kind), 0)
        confidence = stats.count / total if total else 0.0
        if confidence < min_confidence:
            continue
        proposals.append(
            Proposal(
                family=family,
                write_code=write_code,
                report_code=report_code,
                kind=kind,
                value=stats.value,
                dt_ms=stats.min_dt_ms,
                confidence=confidence,
                evidence_lines=stats.evidence_lines,
            )
        )
    proposals.sort(
        key=lambda item: (-item.confidence, item.family, item.write_code, item.report_code, item.kind)
    )
    return proposals


def _proposal_to_dict(proposal: Proposal) -> dict[str, object]:
    return {
        "family": f"0x{proposal.family:02x}",
        "write_code": f"0x{proposal.write_code:02x}",
        "report_code": f"0x{proposal.report_code:02x}",
        "kind": proposal.kind,
        "value": proposal.value,
        "dt_ms": round(proposal.dt_ms, 3),
        "confidence": round(proposal.confidence, 3),
        "evidence_lines": proposal.evidence_lines,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Propose OTA write/report code pairs")
    parser.add_argument("--decoded", required=True, help="Path to decoded .txt file")
    parser.add_argument("--window-ms", type=int, default=5000, help="Time window in ms")
    parser.add_argument("--min-confidence", type=float, default=0.6, help="Minimum confidence")
    parser.add_argument("--only-family", help="Only consider a family/prefix (e.g., 0x08)")
    parser.add_argument("--only-kind", choices=sorted(SUPPORTED_KINDS), help="Only consider kind")
    parser.add_argument("--only-label", help="Only consider labels containing substring")
    parser.add_argument("--only-unlabeled", action="store_true", help="Only consider lines without labels")
    parser.add_argument("--format", choices=["json", "table"], default="json", help="Output format")
    args = parser.parse_args()

    logging.basicConfig(level=logging.WARNING)

    only_family = _parse_hex(args.only_family) if args.only_family else None
    decoded_lines = load_decoded_lines(args.decoded)

    proposals = propose_pairs(
        decoded_lines,
        window_ms=args.window_ms,
        min_confidence=args.min_confidence,
        only_family=only_family,
        only_kind=args.only_kind,
        only_label=args.only_label,
        only_unlabeled=args.only_unlabeled,
    )

    if args.format == "table":
        for proposal in proposals:
            print(
                " ".join(
                    [
                        f"family=0x{proposal.family:02x}",
                        f"write=0x{proposal.write_code:02x}",
                        f"report=0x{proposal.report_code:02x}",
                        f"kind={proposal.kind}",
                        f"value={proposal.value}",
                        f"dt_ms={proposal.dt_ms:.1f}",
                        f"confidence={proposal.confidence:.3f}",
                    ]
                )
            )
        return

    data = [_proposal_to_dict(item) for item in proposals]
    print(json.dumps(data, indent=2))


if __name__ == "__main__":
    main()
