from __future__ import annotations

import sys
import textwrap
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from gateway.captures.manifest import (
    ALLOWED_TASK_TYPES,
    CaptureEntry,
    Manifest,
    ManifestError,
    find_action_log,
    load_manifest,
    parse_manifest,
    suggest_capture_id,
    suggest_task_type,
)
from tools.capture_inventory import (
    InventoryRow,
    format_manifest_stub,
    format_table,
    scan_directory,
)


class ManifestParseTests(unittest.TestCase):
    def test_empty_manifest(self) -> None:
        manifest = parse_manifest(None)
        self.assertEqual(manifest.captures, ())

    def test_empty_captures_list(self) -> None:
        manifest = parse_manifest({"captures": []})
        self.assertEqual(manifest.captures, ())

    def test_minimal_entry(self) -> None:
        manifest = parse_manifest(
            {"captures": [{"capture_id": "x", "pcap": "a.pcapng"}]}
        )
        self.assertEqual(len(manifest.captures), 1)
        entry = manifest.captures[0]
        self.assertEqual(entry.capture_id, "x")
        self.assertEqual(entry.pcap, "a.pcapng")
        self.assertEqual(entry.task_type, "unknown")
        self.assertIsNone(entry.action_log)

    def test_full_entry(self) -> None:
        raw = {
            "captures": [
                {
                    "capture_id": "pass4_rtc",
                    "pcap": "captures/pass4_rtc.pcapng",
                    "action_log": "captures/raw/pass4_rtc.txt",
                    "device_label": "rtc_thermostat",
                    "short_addr": "0x143E",
                    "eui64": "00:11:22:33:44:55:66:77",
                    "task_type": "setpoint_write",
                    "notes": "exp note",
                }
            ]
        }
        manifest = parse_manifest(raw)
        entry = manifest.captures[0]
        self.assertEqual(entry.short_addr, "0x143E")
        self.assertEqual(entry.eui64, "00:11:22:33:44:55:66:77")
        self.assertEqual(entry.task_type, "setpoint_write")

    def test_missing_required_field(self) -> None:
        with self.assertRaises(ManifestError):
            parse_manifest({"captures": [{"pcap": "a.pcapng"}]})

    def test_invalid_task_type(self) -> None:
        with self.assertRaises(ManifestError):
            parse_manifest(
                {
                    "captures": [
                        {"capture_id": "x", "pcap": "a.pcapng", "task_type": "writez"}
                    ]
                }
            )

    def test_all_allowed_task_types_pass(self) -> None:
        for task_type in ALLOWED_TASK_TYPES:
            manifest = parse_manifest(
                {
                    "captures": [
                        {
                            "capture_id": f"c_{task_type}",
                            "pcap": "x.pcapng",
                            "task_type": task_type,
                        }
                    ]
                }
            )
            self.assertEqual(manifest.captures[0].task_type, task_type)

    def test_invalid_short_addr(self) -> None:
        with self.assertRaises(ManifestError):
            parse_manifest(
                {
                    "captures": [
                        {
                            "capture_id": "x",
                            "pcap": "a.pcapng",
                            "short_addr": "bad",
                        }
                    ]
                }
            )

    def test_duplicate_capture_id(self) -> None:
        with self.assertRaises(ManifestError):
            parse_manifest(
                {
                    "captures": [
                        {"capture_id": "x", "pcap": "a.pcapng"},
                        {"capture_id": "x", "pcap": "b.pcapng"},
                    ]
                }
            )

    def test_root_must_be_mapping(self) -> None:
        with self.assertRaises(ManifestError):
            parse_manifest(["not", "a", "mapping"])

    def test_load_manifest_from_disk(self) -> None:
        with TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "manifest.yaml"
            path.write_text(
                textwrap.dedent(
                    """
                    captures:
                      - capture_id: c1
                        pcap: c1.pcapng
                        task_type: idle
                    """
                ).strip(),
                encoding="utf-8",
            )
            manifest = load_manifest(path)
            self.assertEqual(len(manifest.captures), 1)
            self.assertEqual(manifest.captures[0].task_type, "idle")
            self.assertEqual(manifest.source, path)

    def test_load_manifest_missing_file(self) -> None:
        with self.assertRaises(ManifestError):
            load_manifest("does_not_exist_xyz.yaml")

    def test_manifest_by_id_and_iter(self) -> None:
        manifest = Manifest(
            captures=(
                CaptureEntry(capture_id="a", pcap="a.pcapng"),
                CaptureEntry(capture_id="b", pcap="b.pcapng"),
            )
        )
        self.assertEqual(manifest.by_id("a").pcap, "a.pcapng")  # type: ignore[union-attr]
        self.assertIsNone(manifest.by_id("nope"))
        ids = [e.capture_id for e in manifest]
        self.assertEqual(ids, ["a", "b"])


class CaptureInventoryTests(unittest.TestCase):
    def test_suggest_capture_id_basic(self) -> None:
        self.assertEqual(suggest_capture_id("pass4_rtc.pcapng"), "pass4_rtc")
        self.assertEqual(
            suggest_capture_id(Path("/tmp/exp1_rtc_A.pcapng")), "exp1_rtc_A"
        )

    def test_suggest_capture_id_sanitizes(self) -> None:
        self.assertEqual(
            suggest_capture_id("weird name (1).pcapng"), "weird_name_1"
        )

    def test_suggest_capture_id_leading_digit(self) -> None:
        self.assertEqual(suggest_capture_id("01_idle.pcapng"), "c_01_idle")

    def test_find_action_log_sibling_txt(self) -> None:
        with TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            pcap = tmp / "log2.pcapng"
            pcap.write_bytes(b"")
            txt = tmp / "log2.txt"
            txt.write_text("00:00 idle\n", encoding="utf-8")
            self.assertEqual(find_action_log(pcap), txt)

    def test_find_action_log_missing(self) -> None:
        with TemporaryDirectory() as tmpdir:
            pcap = Path(tmpdir) / "lonely.pcapng"
            pcap.write_bytes(b"")
            self.assertIsNone(find_action_log(pcap))

    def test_suggest_task_type_setpoint(self) -> None:
        self.assertEqual(
            suggest_task_type("captures/raw/setpoint_write_1.pcapng"),
            "setpoint_write",
        )

    def test_suggest_task_type_idle(self) -> None:
        self.assertEqual(
            suggest_task_type("captures/raw/exp1_idle.pcapng"), "idle"
        )

    def test_suggest_task_type_default_unknown(self) -> None:
        self.assertEqual(suggest_task_type("captures/raw/blob.pcapng"), "unknown")

    def test_suggest_task_type_reads_action_log(self) -> None:
        with TemporaryDirectory() as tmpdir:
            log = Path(tmpdir) / "notes.txt"
            log.write_text("00:00 occupancy override\n", encoding="utf-8")
            self.assertEqual(
                suggest_task_type("captures/raw/blob.pcapng", log),
                "occupancy_write",
            )

    def test_scan_directory_empty(self) -> None:
        with TemporaryDirectory() as tmpdir:
            rows = scan_directory(Path(tmpdir))
            self.assertEqual(rows, [])

    def test_scan_directory_finds_pcaps_recursively(self) -> None:
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "a").mkdir()
            (root / "a" / "first.pcapng").write_bytes(b"")
            (root / "a" / "first.txt").write_text("00:00 ping\n", encoding="utf-8")
            (root / "second.pcapng").write_bytes(b"")
            rows = scan_directory(root)
            self.assertEqual(len(rows), 2)
            ids = sorted(r.capture_id for r in rows)
            self.assertEqual(ids, ["first", "second"])
            first = next(r for r in rows if r.capture_id == "first")
            self.assertTrue(first.has_action_log)
            self.assertEqual(first.task_type, "ping")
            second = next(r for r in rows if r.capture_id == "second")
            self.assertFalse(second.has_action_log)

    def test_scan_directory_missing_root(self) -> None:
        rows = scan_directory(Path("nope_does_not_exist_xyz"))
        self.assertEqual(rows, [])

    def test_format_table_empty(self) -> None:
        out = format_table([], Path("captures/raw"))
        self.assertIn("No .pcapng files found", out)

    def test_format_table_renders_rows(self) -> None:
        rows = [
            InventoryRow(
                pcap=Path("captures/raw/x.pcapng"),
                capture_id="x",
                action_log=Path("captures/raw/x.txt"),
                task_type="ping",
            )
        ]
        out = format_table(rows, Path("captures/raw"))
        self.assertIn("capture_path", out)
        self.assertIn("capture_id", out)
        self.assertIn("yes", out)
        self.assertIn("ping", out)

    def test_format_manifest_stub(self) -> None:
        rows = [
            InventoryRow(
                pcap=Path("captures/raw/x.pcapng"),
                capture_id="x",
                action_log=None,
                task_type="idle",
            )
        ]
        text = format_manifest_stub(rows)
        self.assertIn("captures:", text)
        self.assertIn("capture_id: x", text)
        self.assertIn("task_type: idle", text)
        self.assertNotIn("action_log:", text)

    def test_format_manifest_stub_empty(self) -> None:
        self.assertEqual(format_manifest_stub([]).strip(), "captures: []")


if __name__ == "__main__":
    unittest.main()
