import json
import tempfile
import unittest
from pathlib import Path

from tools import ota_pointmap_edit


class PointMapEditTests(unittest.TestCase):
    def _write_map(self) -> Path:
        data = {
            "families": {"0x08": {"enum_codes": [], "report_to_write": {}}},
            "points": [
                {
                    "family": "0x08",
                    "code": "0x35",
                    "kind": "analog_x10",
                    "label": "sp_0x35",
                },
                {
                    "family": "0x08",
                    "code": "0x36",
                    "kind": "analog_x10",
                    "label": "sp_0x36",
                },
            ],
        }
        tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(tmpdir.cleanup)
        path = Path(tmpdir.name) / "pointmap.json"
        path.write_text(json.dumps(data), encoding="utf-8")
        return path

    def test_add_point_conflict(self) -> None:
        path = self._write_map()
        data = ota_pointmap_edit.load_pointmap(path)
        with self.assertRaises(ValueError):
            ota_pointmap_edit.add_point(data, 0x08, 0x35, "analog_x10", "different_label")

    def test_rename_label_conflict(self) -> None:
        path = self._write_map()
        data = ota_pointmap_edit.load_pointmap(path)
        with self.assertRaises(ValueError):
            ota_pointmap_edit.rename_label(
                data,
                0x08,
                0x35,
                "analog_x10",
                "sp_0x36",
                force=False,
            )
        changed = ota_pointmap_edit.rename_label(
            data,
            0x08,
            0x35,
            "analog_x10",
            "sp_0x36",
            force=True,
        )
        self.assertTrue(changed)


if __name__ == "__main__":
    unittest.main()
