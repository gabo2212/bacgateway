from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from gateway.captures.manifest import CaptureEntry
from gateway.ota.app import OtaMsg
from gateway.ota.pointmap import PointMap
from tools.ota_action_windows import ActionEvent
from tools.ota_mapping_report import (
    DecodedOtaFrame,
    MappingReport,
    UnknownPayloadGroup,
    format_csv,
    format_markdown,
    frame_to_row,
    group_unknown_payloads,
    infer_candidate_point,
    rows_from_frames,
)


def _msg(
    *,
    cmd_id: int = 2,
    kind: str = "analog_x10",
    prefix: int | None = 0x08,
    code: int | None = 0x4B,
    value: float | int | None = 69.0,
    raw_rest_hex: str = "084b02b2",
    rest_len: int = 4,
    t_rel: float = 10.0,
    direction: str = "gw->dev",
) -> OtaMsg:
    return OtaMsg(
        t_rel=t_rel,
        direction=direction,  # type: ignore[arg-type]
        device_short=0x143E,
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
        ack_extra_hex=None,
        label=None,
        reason=None,
    )


def _entry() -> CaptureEntry:
    return CaptureEntry(
        capture_id="pass4_rtc",
        pcap="captures/raw/pass4_rtc.pcapng",
        device_label="RTC",
        short_addr="0x143E",
        task_type="setpoint_write",
    )


class CandidateInferenceTests(unittest.TestCase):
    def test_action_text_to_candidate_point(self) -> None:
        cases = {
            "occupied cooling setpoint 69": "occCoolSP",
            "coolSP override": "occCoolSP",
            "setpoint change": "occCoolSP",
            "occupied heat setpoint 72": "occHeatSP",
            "heatSP override": "occHeatSP",
            "occupancy command unoccupied": "occupancyCmd",
            "standby selected": "occupancyCmd",
            "outside air temp override": "outdoorTemp",
            "ping only": "unknown",
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(infer_candidate_point(text), expected)


class RowExtractionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.point_map = PointMap({}, {}, {})
        self.action = ActionEvent(
            t_rel=10.0,
            label="occupied cooling setpoint 69",
            raw_line="00:10 occupied cooling setpoint 69",
        )

    def test_cmd2_analog_row_extraction(self) -> None:
        frame = DecodedOtaFrame(msg=_msg(), src_short=0x0000, dst_short=0x143E)
        row = frame_to_row(
            _entry(),
            frame,
            action=self.action,
            window=2.0,
            point_map=self.point_map,
            known_text="",
        )
        self.assertEqual(row.capture_id, "pass4_rtc")
        self.assertEqual(row.action_label, "occupied cooling setpoint 69")
        self.assertEqual(row.direction, "gw->dev")
        self.assertEqual(row.source_short, "0x0000")
        self.assertEqual(row.destination_short, "0x143e")
        self.assertEqual(row.cmd, 2)
        self.assertEqual(row.prefix, "0x08")
        self.assertEqual(row.code, "0x4b")
        self.assertEqual(row.raw_payload, "084b02b2")
        self.assertEqual(row.decoded_kind, "analog_x10")
        self.assertEqual(row.decoded_value, "69")
        self.assertEqual(row.candidate_point, "occCoolSP")
        self.assertEqual(row.confidence, "candidate")

    def test_cmd2_enum_row_extraction(self) -> None:
        action = ActionEvent(t_rel=5.0, label="occupancy command occupied", raw_line="00:05 occupancy command occupied")
        frame = DecodedOtaFrame(
            msg=_msg(kind="enum", prefix=0x08, code=0x3F, value=2, raw_rest_hex="083f0002", t_rel=5.2),
            src_short=0x0000,
            dst_short=0x143E,
        )
        row = frame_to_row(_entry(), frame, action=action, window=2.0, point_map=self.point_map, known_text="")
        self.assertEqual(row.decoded_kind, "enum")
        self.assertEqual(row.decoded_value, "2")
        self.assertEqual(row.candidate_point, "occupancyCmd")

    def test_cmd3_ack_row_extraction(self) -> None:
        action = ActionEvent(t_rel=8.0, label="occupied heat setpoint 70", raw_line="00:08 occupied heat setpoint 70")
        frame = DecodedOtaFrame(
            msg=_msg(cmd_id=3, kind="ack", prefix=0x08, code=0x49, value=None, raw_rest_hex="084900", rest_len=3, t_rel=8.1),
            src_short=0x143E,
            dst_short=0x0000,
        )
        row = frame_to_row(_entry(), frame, action=action, window=2.0, point_map=self.point_map, known_text="")
        self.assertEqual(row.cmd, 3)
        self.assertEqual(row.decoded_kind, "ack")
        self.assertEqual(row.decoded_value, "")
        self.assertEqual(row.candidate_point, "occHeatSP")

    def test_rows_from_frames_filters_to_cmd2_cmd3_and_windows(self) -> None:
        frames = [
            DecodedOtaFrame(msg=_msg(cmd_id=0, kind="identify_req", raw_rest_hex="", rest_len=0, value=None, t_rel=9.9), src_short=0, dst_short=0x143E),
            DecodedOtaFrame(msg=_msg(t_rel=10.1), src_short=0, dst_short=0x143E),
            DecodedOtaFrame(msg=_msg(t_rel=20.0), src_short=0, dst_short=0x143E),
        ]
        rows = rows_from_frames(_entry(), frames, actions=[self.action], window=2.0, point_map=self.point_map, known_text="")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].t_rel, 10.1)

    def test_rows_without_actions_are_unwindowed(self) -> None:
        frame = DecodedOtaFrame(msg=_msg(), src_short=0x0000, dst_short=0x143E)
        rows = rows_from_frames(_entry(), [frame], actions=[], window=2.0, point_map=self.point_map, known_text="")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].action_label, "(unwindowed)")
        self.assertEqual(rows[0].action_window, "unwindowed")

    def test_unknown_payload_grouping(self) -> None:
        action = ActionEvent(t_rel=1.0, label="ping", raw_line="00:01 ping")
        rows = []
        for _ in range(2):
            frame = DecodedOtaFrame(
                msg=_msg(kind="unknown", value=None, prefix=0x08, code=0x99, raw_rest_hex="0899", rest_len=2, t_rel=1.0),
                src_short=0x143E,
                dst_short=0,
            )
            rows.append(frame_to_row(_entry(), frame, action=action, window=2.0, point_map=self.point_map, known_text=""))
        groups = group_unknown_payloads(rows)
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0].count, 2)
        self.assertEqual(groups[0].payload_len, 2)
        self.assertEqual(groups[0].raw_payload, "0899")


class OutputFormatTests(unittest.TestCase):
    def test_markdown_and_csv_output_shape(self) -> None:
        row = frame_to_row(
            _entry(),
            DecodedOtaFrame(msg=_msg(), src_short=0, dst_short=0x143E),
            action=ActionEvent(t_rel=10.0, label="occupied cooling setpoint", raw_line="00:10 occupied cooling setpoint"),
            window=2.0,
            point_map=PointMap({}, {}, {}),
            known_text="",
        )
        unknown = UnknownPayloadGroup("cap", "dev", "gw->dev", 4, "0x08", "0x99", "08990000", 3)
        report = MappingReport(rows=(row,), unknown_groups=(unknown,), captures_processed=("pass4_rtc",), errors=())
        md = format_markdown(report)
        self.assertIn("# Candidate OTA Mappings", md)
        self.assertIn("## Candidate mappings", md)
        self.assertIn("## Unknown payload groups", md)
        self.assertIn("occCoolSP", md)
        csv_text = format_csv((row,))
        self.assertIn("capture_id,device_label,short_addr", csv_text)
        self.assertIn("pass4_rtc,RTC,0x143E", csv_text)
        self.assertIn("occCoolSP", csv_text)


if __name__ == "__main__":
    unittest.main()
