import unittest

from gateway.ota import decode_cmd2_rest, decode_cmd3_rest, normalize_code, parse_ota_msg
from gateway.ota.zigbee import ApsFrame
from gateway.ota.pointmap import PointMap, MappingEntry

class OtaRestDecodeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.pm = PointMap([
            MappingEntry(
                device_label="Device2", prefix=0x08, kind="enum", direction="gw->dev", status="candidate", evidence="",
                write_code=0x3f, canonical_point="occ_0x3f"
            ),
            MappingEntry(
                device_label="Device2", prefix=0x08, kind="analog_x10", direction="gw->dev", status="candidate", evidence="",
                write_code=0x35, report_code=0xc4, canonical_point="sp_0x35"
            ),
            MappingEntry(
                device_label="Device2", prefix=0x0a, kind="analog_x10", direction="gw->dev", status="candidate", evidence="",
                write_code=0x2a, report_code=0xca, canonical_point="sp_0x2a"
            )
        ])

    def test_cmd2_analog(self) -> None:
        prefix, code, kind, value = decode_cmd2_rest(bytes.fromhex("08350398"), point_map=self.pm)
        self.assertEqual(prefix, 0x08)
        self.assertEqual(code, 0x35)
        self.assertEqual(kind, "analog_x10")
        self.assertAlmostEqual(float(value), 92.0, places=3)

        prefix, code, kind, value = decode_cmd2_rest(bytes.fromhex("0a2a02b2"), point_map=self.pm)
        self.assertEqual(prefix, 0x0A)
        self.assertEqual(code, 0x2A)
        self.assertEqual(kind, "analog_x10")
        self.assertAlmostEqual(float(value), 69.0, places=3)

    def test_cmd2_enum(self) -> None:
        prefix, code, kind, value = decode_cmd2_rest(bytes.fromhex("083f0002"), point_map=self.pm)
        self.assertEqual(prefix, 0x08)
        self.assertEqual(code, 0x3F)
        self.assertEqual(kind, "enum")
        self.assertEqual(value, 2)

    def test_cmd2_u8(self) -> None:
        prefix, code, kind, value = decode_cmd2_rest(bytes.fromhex("08c602"), point_map=self.pm)
        self.assertEqual(prefix, 0x08)
        self.assertEqual(code, 0xC6)
        self.assertEqual(kind, "u8")
        self.assertEqual(value, 2)

        prefix, code, kind, value = decode_cmd2_rest(bytes.fromhex("0a0b02"), point_map=self.pm)
        self.assertEqual(prefix, 0x0A)
        self.assertEqual(code, 0x0B)
        self.assertEqual(kind, "u8")
        self.assertEqual(value, 2)

        prefix, code, kind, value = decode_cmd2_rest(bytes.fromhex("083202"), point_map=self.pm)
        self.assertEqual(prefix, 0x08)
        self.assertEqual(code, 0x32)
        self.assertEqual(kind, "u8")
        self.assertEqual(value, 2)

        prefix, code, kind, value = decode_cmd2_rest(bytes.fromhex("0afe02"), point_map=self.pm)
        self.assertEqual(prefix, 0x0A)
        self.assertEqual(code, 0xFE)
        self.assertEqual(kind, "u8")
        self.assertEqual(value, 2)

    def test_cmd3_ack(self) -> None:
        prefix, code, kind, value, extra, reason = decode_cmd3_rest(bytes.fromhex("083f00"), point_map=self.pm)
        self.assertEqual(prefix, 0x08)
        self.assertEqual(code, 0x3F)
        self.assertEqual(kind, "ack")
        self.assertIsNone(value)
        self.assertIsNone(extra)
        self.assertIsNone(reason)

    def test_cmd3_ack_extra(self) -> None:
        prefix, code, kind, value, extra, reason = decode_cmd3_rest(bytes.fromhex("00020000fe70"), point_map=self.pm)
        self.assertEqual(prefix, 0x00)
        self.assertEqual(code, 0x02)
        self.assertEqual(kind, "ack")
        self.assertIsNone(value)
        self.assertEqual(extra, "fe70")
        self.assertIsNone(reason)

    def test_cmd3_short_rest(self) -> None:
        prefix, code, kind, value, extra, reason = decode_cmd3_rest(b"", point_map=self.pm)
        self.assertIsNone(prefix)
        self.assertIsNone(code)
        self.assertEqual(kind, "unknown")
        self.assertIsNone(value)
        self.assertIsNone(extra)
        self.assertEqual(reason, "short_rest")

    def test_cmd3_short_rest_message(self) -> None:
        aps = ApsFrame(
            profile_id=0xC1E4,
            cluster_id=0x0002,
            src_ep=0x32,
            dst_ep=0x0A,
            payload=b"\x00\x01\x03",
        )
        msg = parse_ota_msg(0.0, 0x143E, 0x0000, aps, point_map=self.pm)
        self.assertIsNotNone(msg)
        assert msg is not None
        self.assertEqual(msg.kind, "unknown")
        self.assertEqual(msg.reason, "short_rest")
        self.assertEqual(msg.rest_len, 0)

    def test_cmd2_high_byte_zero_is_analog(self) -> None:
        prefix, code, kind, value = decode_cmd2_rest(bytes.fromhex("08350064"), point_map=self.pm)
        self.assertEqual(prefix, 0x08)
        self.assertEqual(code, 0x35)
        self.assertEqual(kind, "analog_x10")
        self.assertAlmostEqual(float(value), 10.0, places=3)

    def test_normalize_code(self) -> None:
        prefix, code = normalize_code(0x08, 0xC4, point_map=self.pm)
        self.assertEqual(prefix, 0x08)
        self.assertEqual(code, 0x35)

        prefix, code = normalize_code(0x0A, 0xCA, point_map=self.pm)
        self.assertEqual(prefix, 0x0A)
        self.assertEqual(code, 0x2A)

        prefix, code = normalize_code(0x99, 0x01, point_map=self.pm)
        self.assertEqual(prefix, 0x99)
        self.assertEqual(code, 0x01)


if __name__ == "__main__":
    unittest.main()
