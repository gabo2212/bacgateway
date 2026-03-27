import math
import unittest

from proto import codec
from gateway import codec as gcodec


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


    def test_validate_point_value_accepts_valid(self) -> None:
        """validate_point_value should not raise for a value within spec limits."""
        specs = codec.load_points_catalog()
        if not specs:
            self.skipTest("No point specs available")
        # Find a scaled-int point with known min/max bounds.
        addr = None
        for a, spec_list in specs.items():
            spec = spec_list[0]
            if spec.value_type == "scaled-int" and spec.min_raw is not None and spec.max_raw is not None:
                addr = a
                break
        if addr is None:
            self.skipTest("No bounded scaled-int point found in catalog")
        spec = codec.resolve_point_spec(addr)
        assert spec is not None
        mid_raw = (spec.min_raw + spec.max_raw) / 2.0
        mid_value = (mid_raw - spec.offset) / spec.scale
        codec.validate_point_value(addr, mid_value)  # must not raise

    def test_validate_point_value_rejects_below_min(self) -> None:
        """validate_point_value should raise when the encoded raw is below min_raw."""
        specs = codec.load_points_catalog()
        if not specs:
            self.skipTest("No point specs available")
        addr = None
        for a, spec_list in specs.items():
            spec = spec_list[0]
            if spec.value_type == "scaled-int" and spec.min_raw is not None:
                addr = a
                break
        if addr is None:
            self.skipTest("No bounded scaled-int point found in catalog")
        spec = codec.resolve_point_spec(addr)
        assert spec is not None
        # Build a value that encodes to (min_raw - 1).
        below_value = (spec.min_raw - 1 - spec.offset) / spec.scale
        with self.assertRaises(ValueError):
            codec.validate_point_value(addr, below_value)

    def test_validate_point_value_rejects_above_max(self) -> None:
        """validate_point_value should raise when the encoded raw exceeds max_raw."""
        specs = codec.load_points_catalog()
        if not specs:
            self.skipTest("No point specs available")
        addr = None
        for a, spec_list in specs.items():
            spec = spec_list[0]
            if spec.value_type == "scaled-int" and spec.max_raw is not None:
                addr = a
                break
        if addr is None:
            self.skipTest("No bounded scaled-int point found in catalog")
        spec = codec.resolve_point_spec(addr)
        assert spec is not None
        above_value = (spec.max_raw + 1 - spec.offset) / spec.scale
        with self.assertRaises(ValueError):
            codec.validate_point_value(addr, above_value)

    def test_validate_point_value_bool_rejects_non_bool(self) -> None:
        """validate_point_value for a bool point should reject non-boolean non-0/1 values."""
        specs = codec.load_points_catalog()
        bool_addr = None
        for addr, spec_list in specs.items():
            if spec_list[0].value_type == "bool":
                bool_addr = addr
                break
        if bool_addr is None:
            self.skipTest("No bool point found in catalog")
        with self.assertRaises(ValueError):
            codec.validate_point_value(bool_addr, 42)


class GatewayCodecTests(unittest.TestCase):
    """Tests for gateway.codec — the canonical raw frame layer."""

    def test_build_frame_length_and_crc(self) -> None:
        frame = gcodec.build_frame(0x1000, gcodec.CMD_READ_REQUEST, 1, 5)
        # LEN byte = total - 2
        self.assertEqual(frame[1], len(frame) - 2)
        # CRC byte = sum(frame[2:-1]) & 0xFF
        self.assertEqual(sum(frame[2:-1]) & 0xFF, frame[-1])
        self.assertEqual(frame[0], 0x40)

    def test_parse_frame_roundtrip(self) -> None:
        """A frame built by build_frame must parse back cleanly."""
        original = gcodec.build_frame(0x0F03, gcodec.CMD_READ_REQUEST, 0, 7)
        pf = gcodec.parse_frame(original)
        self.assertTrue(pf.crc_ok)
        self.assertTrue(pf.length_ok)
        self.assertEqual(pf.msg_type, 0x0F03)
        self.assertEqual(pf.cmd_type, gcodec.CMD_READ_REQUEST)
        self.assertEqual(pf.comm_addr, 0)
        self.assertEqual(pf.trans_seq, 7)
        self.assertIsNone(pf.status)  # READ_REQUEST has no status byte

    def test_parse_frame_status_present(self) -> None:
        """Response frames (cmd_type 1 or 3) must parse the status byte."""
        # Build a synthetic 0x3C response frame with status=0 and no extra payload
        # Structure: [0x3C, LEN, msg_hi, msg_lo, cmd, comm, seq, status, lq, crc]
        body = bytearray([0x0F, 0x03, 0x01, 0x00, 0x01, 0x00])  # msg, cmd=1, comm, seq, status=0
        lq = 0x5A
        body.append(lq)
        crc = sum(body) & 0xFF
        frame = bytes([0x3C, len(body) + 1]) + bytes(body) + bytes([crc])
        pf = gcodec.parse_frame(frame)
        self.assertTrue(pf.crc_ok)
        self.assertEqual(pf.status, 0)
        self.assertEqual(pf.link_quality_raw, lq)
        self.assertIsNotNone(pf.link_quality)

    def test_parse_frame_no_status_for_request(self) -> None:
        """Request frames (cmd_type 0 or 2) must NOT parse a status byte."""
        frame = gcodec.build_frame(0x1000, gcodec.CMD_READ_REQUEST, 3, 9)
        pf = gcodec.parse_frame(frame)
        self.assertIsNone(pf.status)

    def test_parse_frame_bad_crc_flagged(self) -> None:
        """A frame with a flipped CRC byte must have crc_ok=False but still parse."""
        good = gcodec.build_frame(0x1000, gcodec.CMD_READ_REQUEST, 1, 1)
        corrupted = bytearray(good)
        corrupted[-1] ^= 0xFF  # flip all bits in CRC
        pf = gcodec.parse_frame(bytes(corrupted))
        self.assertFalse(pf.crc_ok)
        self.assertTrue(pf.length_ok)

    def test_parse_frame_too_short_raises(self) -> None:
        with self.assertRaises(ValueError):
            gcodec.parse_frame(b"\x40\x04\x0F")  # only 3 bytes

    def test_parse_identify_payload(self) -> None:
        payload = bytes([1, 2, 0x12, 0x34] + [0xAB] * 8 + [5])
        info = gcodec.parse_identify_payload(payload)
        self.assertEqual(info["firmware_maj"], 1)
        self.assertEqual(info["firmware_min"], 2)
        self.assertEqual(info["zigbee_addr"], "0x1234")
        self.assertEqual(info["chip_rev"], 5)


if __name__ == "__main__":
    unittest.main()
