import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import yaml

from tools.ota_promote_mapping import main

class PromoteMappingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.pointmap_path = Path(self.tmpdir.name) / "pointmap.yaml"
        self.csv_path = Path(self.tmpdir.name) / "report.csv"
        
        # Create dummy pointmap
        data = {
            "mappings": [
                {
                    "device_label": "Device2",
                    "prefix": "0x08",
                    "write_code": "0x49",
                    "report_code": "0x0a",
                    "canonical_point": "occupied_heat_setpoint",
                    "kind": "analog_x10",
                    "direction": "bidirectional",
                    "status": "confirmed",
                    "evidence": "Exp1"
                }
            ]
        }
        with open(self.pointmap_path, "w") as f:
            yaml.dump(data, f)
            
        # Create dummy csv
        with open(self.csv_path, "w") as f:
            f.write("capture_id,device_label,short_addr,action_label,action_window,t_rel,direction,source_short,destination_short,cmd,prefix,code,raw_payload,decoded_kind,decoded_value,candidate_point,confidence,evidence_note\n")
            f.write("log1,Device2,0x143e,,unwindowed,0,gw->dev,0,1,2,0x08,0x4b,,analog_x10,,occupied_cool_setpoint,candidate,\n")
            f.write("log1,Device2,0x143e,,unwindowed,0,gw->dev,0,1,2,0x08,0x99,,unknown,,unknown_pt,candidate,\n")

    def tearDown(self) -> None:
        self.tmpdir.cleanup()

    def test_promote_from_csv(self) -> None:
        test_args = ["ota_promote_mapping.py", "--csv", str(self.csv_path), "--point", "occupied_cool_setpoint", "--status", "confirmed", "--pointmap", str(self.pointmap_path)]
        with patch.object(sys, 'argv', test_args):
            main()
            
        with open(self.pointmap_path, "r") as f:
            data = yaml.safe_load(f)
        
        self.assertEqual(len(data["mappings"]), 2)
        new_map = data["mappings"][1]
        self.assertEqual(new_map["canonical_point"], "occupied_cool_setpoint")
        self.assertEqual(new_map["status"], "confirmed")

    def test_refuse_duplicate(self) -> None:
        test_args = ["ota_promote_mapping.py", "--point", "occupied_heat_setpoint", "--prefix", "0x08", "--device-label", "Device2", "--kind", "analog_x10", "--status", "confirmed", "--pointmap", str(self.pointmap_path)]
        with patch.object(sys, 'argv', test_args):
            with self.assertRaises(SystemExit) as cm:
                main()
            self.assertEqual(cm.exception.code, 1)

    def test_replace_duplicate(self) -> None:
        test_args = ["ota_promote_mapping.py", "--point", "occupied_heat_setpoint", "--prefix", "0x08", "--device-label", "Device2", "--kind", "analog_x10", "--status", "candidate", "--replace", "--pointmap", str(self.pointmap_path)]
        with patch.object(sys, 'argv', test_args):
            main()
            
        with open(self.pointmap_path, "r") as f:
            data = yaml.safe_load(f)
        self.assertEqual(len(data["mappings"]), 1)
        self.assertEqual(data["mappings"][0]["status"], "candidate")

    def test_refuse_unknown(self) -> None:
        test_args = ["ota_promote_mapping.py", "--csv", str(self.csv_path), "--point", "unknown_pt", "--status", "candidate", "--pointmap", str(self.pointmap_path)]
        with patch.object(sys, 'argv', test_args):
            with self.assertRaises(SystemExit) as cm:
                main()
            self.assertEqual(cm.exception.code, 1)
