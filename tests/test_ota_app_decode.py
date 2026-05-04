import unittest

from gateway.ota import decode_cmd2_rest, decode_cmd3_rest, normalize_code, parse_ota_msg
from gateway.ota.zigbee import ApsFrame


class OtaRestDecodeTests(unittest.TestCase):
    def test_cmd2_analog(self) -> None:
        prefix, code, kind, value = decode_cmd2_rest(bytes.fromhex("08350398"))
        self.assertEqual(prefix, 0x08)
        self.assertEqual(code, 0x35)
        self.assertEqual(kind, "analog_x10")
        self.assertAlmostEqual(float(value), 92.0, places=3)

        prefix, code, kind, value = decode_cmd2_rest(bytes.fromhex("0a2a02b2"))
        self.assertEqual(prefix, 0x0A)
        self.assertEqual(code, 0x2A)
        self.assertEqual(kind, "analog_x10")
        self.assertAlmostEqual(float(value), 69.0, places=3)

    def test_cmd2_enum(self) -> None:
        prefix, code, kind, value = decode_cmd2_rest(bytes.fromhex("083f0002"))
        self.assertEqual(prefix, 0x08)
        self.assertEqual(code, 0x3F)
        self.assertEqual(kind, "enum")
        self.assertEqual(value, 2)

    def test_cmd2_u8(self) -> None:
        prefix, code, kind, value = decode_cmd2_rest(bytes.fromhex("08c602"))
        self.assertEqual(prefix, 0x08)
        self.assertEqual(code, 0xC6)
        self.assertEqual(kind, "u8")
        self.assertEqual(value, 2)

        prefix, code, kind, value = decode_cmd2_rest(bytes.fromhex("0a0b02"))
        self.assertEqual(prefix, 0x0A)
        self.assertEqual(code, 0x0B)
        self.assertEqual(kind, "u8")
        self.assertEqual(value, 2)

        prefix, code, kind, value = decode_cmd2_rest(bytes.fromhex("083202"))
        self.assertEqual(prefix, 0x08)
        self.assertEqual(code, 0x32)
        self.assertEqual(kind, "u8")
        self.assertEqual(value, 2)

        prefix, code, kind, value = decode_cmd2_rest(bytes.fromhex("0afe02"))
        self.assertEqual(prefix, 0x0A)
        self.assertEqual(code, 0xFE)
        self.assertEqual(kind, "u8")
        self.assertEqual(value, 2)

    def test_cmd3_ack(self) -> None:
        prefix, code, kind, value, extra, reason = decode_cmd3_rest(bytes.fromhex("083f00"))
        self.assertEqual(prefix, 0x08)
        self.assertEqual(code, 0x3F)
        self.assertEqual(kind, "ack")
        self.assertIsNone(value)
        self.assertIsNone(extra)
        self.assertIsNone(reason)

    def test_cmd3_ack_extra(self) -> None:
        prefix, code, kind, value, extra, reason = decode_cmd3_rest(bytes.fromhex("00020000fe70"))
        self.assertEqual(prefix, 0x00)
        self.assertEqual(code, 0x02)
        self.assertEqual(kind, "ack")
        self.assertIsNone(value)
        self.assertEqual(extra, "fe70")
        self.assertIsNone(reason)

    def test_cmd3_short_rest(self) -> None:
        prefix, code, kind, value, extra, reason = decode_cmd3_rest(b"")
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
        msg = parse_ota_msg(0.0, 0x143E, 0x0000, aps)
        self.assertIsNotNone(msg)
        assert msg is not None
        self.assertEqual(msg.kind, "unknown")
        self.assertEqual(msg.reason, "short_rest")
        self.assertEqual(msg.rest_len, 0)

    def test_cmd2_high_byte_zero_is_analog(self) -> None:
        prefix, code, kind, value = decode_cmd2_rest(bytes.fromhex("08350064"))
        self.assertEqual(prefix, 0x08)
        self.assertEqual(code, 0x35)
        self.assertEqual(kind, "analog_x10")
        self.assertAlmostEqual(float(value), 10.0, places=3)

    def test_normalize_code(self) -> None:
        prefix, code = normalize_code(0x08, 0xC4)
        self.assertEqual(prefix, 0x08)
        self.assertEqual(code, 0x35)

        prefix, code = normalize_code(0x0A, 0xCA)
        self.assertEqual(prefix, 0x0A)
        self.assertEqual(code, 0x2A)

        prefix, code = normalize_code(0x99, 0x01)
        self.assertEqual(prefix, 0x99)
        self.assertEqual(code, 0x01)


if __name__ == "__main__":
    unittest.main()
