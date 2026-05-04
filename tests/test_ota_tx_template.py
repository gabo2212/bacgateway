import unittest

from gateway.ota.tx_template import TxTemplate, build_from_template, diff_offsets, infer_volatile_offsets


class TxTemplateTests(unittest.TestCase):
    def test_build_from_template_patches_cmd_and_rest(self) -> None:
        frame = bytes.fromhex("618812d2003e140000001122020849028a3344")
        template = TxTemplate(
            device_short=0x143E,
            mac_frame=frame,
            nwk_off=9,
            aps_off=12,
            app_off=12,
            rest_off=13,
            rest_len=4,
            cmd_id=2,
            direction="gw->dev",
        )
        built = build_from_template(template, cmd_id=3, rest=bytes.fromhex("083f00"))
        self.assertEqual(built[:12], frame[:12])
        self.assertEqual(built[12], 3)
        self.assertEqual(built[13:16], bytes.fromhex("083f00"))
        self.assertEqual(built[16:], bytes.fromhex("3344"))
        self.assertEqual(len(built), len(frame) - 1)

    def test_diff_and_volatile_offsets_are_deterministic(self) -> None:
        frame_a = bytes.fromhex("001122334455")
        frame_b = bytes.fromhex("001122aa4455")
        frame_c = bytes.fromhex("001122bb4466")
        self.assertEqual(diff_offsets(frame_a, frame_b), [3])
        self.assertEqual(infer_volatile_offsets([frame_a, frame_b, frame_c]), [3, 5])


if __name__ == "__main__":
    unittest.main()
