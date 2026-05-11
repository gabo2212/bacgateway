import yaml
import tempfile
import unittest
from pathlib import Path

from gateway.ota.pointmap import load_pointmap


class PointMapTests(unittest.TestCase):
    def test_canonicalize_and_enum(self) -> None:
        data = {
            "mappings": [
                {
                    "device_label": "Device2",
                    "prefix": "0x08",
                    "write_code": "0x35",
                    "report_code": "0xc4",
                    "canonical_point": "sp_0x35",
                    "kind": "analog_x10",
                    "direction": "bidirectional",
                    "status": "candidate"
                },
                {
                    "device_label": "Device2",
                    "prefix": "0x08",
                    "write_code": "0x3e",
                    "canonical_point": "occ_0x3e",
                    "kind": "enum",
                    "direction": "gw->dev",
                    "status": "candidate"
                }
            ]
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "pointmap.yaml"
            with open(path, "w") as f:
                yaml.dump(data, f)
            point_map = load_pointmap(path)

        self.assertEqual(point_map.canonicalize(0x08, 0xC4), 0x35)
        self.assertTrue(point_map.is_enum(0x08, 0x3E))
        self.assertEqual(point_map.label_for(0x08, 0x35, "analog_x10"), "sp_0x35")

