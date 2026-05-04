from __future__ import annotations

import json
import sys
import unittest
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from gateway.captures.manifest import CaptureEntry, Manifest
from gateway.ota.app import OtaMsg
from tools.ota_batch_analyze import (
    CaptureSummary,
    analyze_pcap,
    format_summary_text,
    main as batch_main,
    summarize_messages,
)


def _msg(
    *,
    cmd_id: int,
    kind: str,
    device_short: int = 0x143E,
    direction: str = "gw->dev",
    prefix: int | None = 0x08,
    code: int | None = 0x35,
    value: float | int | None = None,
    raw_rest_hex: str = "",
    rest_len: int = 0,
    ack_extra_hex: str | None = None,
    label: str | None = None,
    reason: str | None = None,
    t_rel: float = 0.0,
) -> OtaMsg:
    return OtaMsg(
        t_rel=t_rel,
        direction=direction,  # type: ignore[arg-type]
        device_short=device_short,
        profile_id=0xC1E4,
        cluster_id=0x0002,
        src_ep=0x0A,
        dst_ep=0x32,
        cmd_id=cmd_id,
        prefix=prefix,
        code=code,
        kind=kind,  # type: ignore[arg-type]
        value=value,
        raw_rest_hex=raw_rest_hex,
        rest_len=rest_len,
        ack_extra_hex=ack_extra_hex,
        label=label,
        reason=reason,
    )


class SummarizeMessagesTests(unittest.TestCase):
    def test_empty_input(self) -> None:
        summary = summarize_messages([], capture_id="x", pcap="x.pcapng")
        self.assertEqual(summary.matching_vendor_frames, 0)
        self.assertEqual(summary.short_addrs, [])
        self.assertEqual(summary.cmd_counts, {})
        self.assertEqual(summary.unknown_events, 0)

    def test_mixed_kinds_counted(self) -> None:
        msgs = [
            _msg(cmd_id=2, kind="analog_x10", value=21.5, t_rel=0.1),
            _msg(cmd_id=2, kind="analog_x10", value=22.0, t_rel=0.2),
            _msg(cmd_id=2, kind="enum", value=2, prefix=0x08, code=0x3F, t_rel=0.3),
            _msg(cmd_id=3, kind="ack", t_rel=0.4),
            _msg(
                cmd_id=2,
                kind="unknown",
                raw_rest_hex="aabbccdd",
                rest_len=4,
                t_rel=0.5,
            ),
            _msg(cmd_id=2, kind="analog_x10", value=10, device_short=0x0001, t_rel=0.6),
        ]
        summary = summarize_messages(
            msgs,
            capture_id="x",
            pcap="x.pcapng",
            total_frames=42,
        )
        self.assertEqual(summary.total_frames, 42)
        self.assertEqual(summary.matching_vendor_frames, 6)
        self.assertEqual(summary.analog_x10_events, 3)
        self.assertEqual(summary.enum_events, 1)
        self.assertEqual(summary.ack_events, 1)
        self.assertEqual(summary.unknown_events, 1)
        self.assertEqual(summary.cmd_counts, {"2": 5, "3": 1})
        self.assertEqual(sorted(summary.short_addrs), ["0x0001", "0x143e"])

    def test_unknown_frames_preserved_count(self) -> None:
        msgs = [_msg(cmd_id=2, kind="unknown", raw_rest_hex="01", rest_len=1)] * 5
        summary = summarize_messages(msgs, capture_id="x", pcap="x.pcapng")
        self.assertEqual(summary.unknown_events, 5)
        self.assertEqual(summary.matching_vendor_frames, 5)

    def test_extra_eui64s_included(self) -> None:
        summary = summarize_messages(
            [],
            capture_id="x",
            pcap="x.pcapng",
            extra_eui64s=[0x0011223344556677],
        )
        self.assertEqual(summary.eui64s, ["0x0011223344556677"])

    def test_summary_to_dict_shape(self) -> None:
        summary = summarize_messages(
            [_msg(cmd_id=2, kind="analog_x10", value=1.0)],
            capture_id="cap",
            pcap="x.pcapng",
            total_frames=1,
        )
        d = summary.to_dict()
        for key in (
            "capture_id",
            "pcap",
            "pcap_exists",
            "total_frames",
            "matching_vendor_frames",
            "short_addrs",
            "eui64s",
            "cmd_counts",
            "analog_x10_events",
            "enum_events",
            "ack_events",
            "unknown_events",
            "error",
        ):
            self.assertIn(key, d)


class FormatSummaryTextTests(unittest.TestCase):
    def test_format_includes_required_fields(self) -> None:
        summary = summarize_messages(
            [_msg(cmd_id=2, kind="analog_x10", value=21.5)],
            capture_id="cap",
            pcap="x.pcapng",
            total_frames=10,
        )
        text = format_summary_text(summary)
        self.assertIn("capture_id: cap", text)
        self.assertIn("total_frames", text)
        self.assertIn("matching_vendor_frames", text)
        self.assertIn("analog_x10_events", text)
        self.assertIn("unknown_events", text)
        self.assertIn("0x143e", text)

    def test_format_error_summary(self) -> None:
        summary = CaptureSummary(
            capture_id="missing",
            pcap="nope.pcapng",
            pcap_exists=False,
            error="pcap file not found: nope.pcapng",
        )
        text = format_summary_text(summary)
        self.assertIn("error:", text)
        self.assertNotIn("total_frames", text)


class AnalyzePcapTests(unittest.TestCase):
    def test_missing_pcap_returns_error_summary(self) -> None:
        with TemporaryDirectory() as tmpdir:
            entry = CaptureEntry(capture_id="missing", pcap="does_not_exist.pcapng")
            summary = analyze_pcap(entry, base_dir=Path(tmpdir))
            self.assertFalse(summary.pcap_exists)
            self.assertIsNotNone(summary.error)
            self.assertEqual(summary.matching_vendor_frames, 0)


class BatchMainTests(unittest.TestCase):
    def test_main_json_output_one_entry_per_capture(self) -> None:
        with TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            manifest_path = tmp / "manifest.yaml"
            manifest_path.write_text(
                "captures:\n"
                "  - capture_id: a\n"
                "    pcap: missing_a.pcapng\n"
                "  - capture_id: b\n"
                "    pcap: missing_b.pcapng\n",
                encoding="utf-8",
            )
            buf = StringIO()
            with patch("sys.stdout", buf):
                rc = batch_main(["--manifest", str(manifest_path), "--json"])
            self.assertEqual(rc, 0)
            payload = json.loads(buf.getvalue())
            self.assertEqual(len(payload), 2)
            ids = sorted(item["capture_id"] for item in payload)
            self.assertEqual(ids, ["a", "b"])
            for item in payload:
                self.assertIn("error", item)
                self.assertIn("matching_vendor_frames", item)
                self.assertIn("unknown_events", item)

    def test_main_text_output_empty_manifest(self) -> None:
        with TemporaryDirectory() as tmpdir:
            manifest_path = Path(tmpdir) / "manifest.yaml"
            manifest_path.write_text("captures: []\n", encoding="utf-8")
            buf = StringIO()
            with patch("sys.stdout", buf):
                rc = batch_main(["--manifest", str(manifest_path)])
            self.assertEqual(rc, 0)
            self.assertIn("manifest contains no captures", buf.getvalue())

    def test_manifest_with_one_entry_yields_one_summary(self) -> None:
        manifest = Manifest(
            captures=(
                CaptureEntry(capture_id="only", pcap="not_real.pcapng"),
            )
        )
        from tools.ota_batch_analyze import analyze_manifest

        with TemporaryDirectory() as tmpdir:
            summaries = analyze_manifest(manifest, base_dir=Path(tmpdir))
        self.assertEqual(len(summaries), 1)
        self.assertEqual(summaries[0].capture_id, "only")


if __name__ == "__main__":
    unittest.main()
