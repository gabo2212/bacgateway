import json
import tempfile
import unittest
from pathlib import Path

from gateway.ota.pointmap import PointMap


class PointMapTests(unittest.TestCase):
    def test_canonicalize_and_enum(self) -> None:
        data = {
            "families": {
                "0x08": {
                    "enum_codes": ["0x3e"],
                    "report_to_write": {"0xc4": "0x35"},
                }
            },
            "points": [
                {
                    "family": "0x08",
                    "code": "0x35",
                    "kind": "analog_x10",
                    "label": "sp_0x35",
                }
            ],
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "pointmap.json"
            path.write_text(json.dumps(data), encoding="utf-8")
            point_map = PointMap.from_json(path)

        self.assertEqual(point_map.canonicalize(0x08, 0xC4), 0x35)
        self.assertTrue(point_map.is_enum(0x08, 0x3E))
        self.assertEqual(point_map.label_for(0x08, 0x35, "analog_x10"), "sp_0x35")
