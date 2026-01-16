import math
import unittest

from proto import codec


class CodecRoundtripTests(unittest.TestCase):
    def test_crc_and_length(self) -> None:
        frame = codec.build_read_point(1, 0x1000, 1)
        self.assertEqual(frame[1], len(frame) - 2)
        self.assertEqual(sum(frame[2:-1]) & 0xFF, frame[-1])

    def test_encode_decode_examples(self) -> None:
        specs = codec.load_points_catalog()
        if not specs:
            self.skipTest("No point specs available")

        target_types = {"scaled-int": None, "bool": None, "enum": None}
        for addr, spec_list in specs.items():
            spec = spec_list[0]
            if spec.value_type in target_types and target_types[spec.value_type] is None:
                target_types[spec.value_type] = addr
            if all(target_types.values()):
                break

        for value_type, addr in target_types.items():
            if addr is None:
                continue
            if value_type == "scaled-int":
                value = 72.5
                encoded = codec.encode_point_value(addr, value)
                decoded = codec.parse_point_value(addr, encoded)
                self.assertTrue(math.isclose(float(decoded), value, rel_tol=0.01))
            elif value_type == "bool":
                encoded = codec.encode_point_value(addr, True)
                decoded = codec.parse_point_value(addr, encoded)
                self.assertIs(decoded, True)
            elif value_type == "enum":
                enums, _ = codec.load_enums()
                spec = codec.resolve_point_spec(addr)
                mapping = enums.get(spec.enum_name, {}) if spec else {}
                if not mapping:
                    continue
                label = next(iter(mapping.values()))
                encoded = codec.encode_point_value(addr, label)
                decoded = codec.parse_point_value(addr, encoded)
                self.assertEqual(decoded, label)


if __name__ == "__main__":
    unittest.main()
