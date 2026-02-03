import unittest

from gateway.ota import decode_cmd2_rest, decode_cmd3_rest


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

    def test_cmd3_ack(self) -> None:
        prefix, code, kind, value = decode_cmd3_rest(bytes.fromhex("083f00"))
        self.assertEqual(prefix, 0x08)
        self.assertEqual(code, 0x3F)
        self.assertEqual(kind, "ack")
        self.assertIsNone(value)

    def test_cmd2_high_byte_zero_is_analog(self) -> None:
        prefix, code, kind, value = decode_cmd2_rest(bytes.fromhex("08350064"))
        self.assertEqual(prefix, 0x08)
        self.assertEqual(code, 0x35)
        self.assertEqual(kind, "analog_x10")
        self.assertAlmostEqual(float(value), 10.0, places=3)


if __name__ == "__main__":
    unittest.main()
