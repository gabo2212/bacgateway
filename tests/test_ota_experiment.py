import unittest

from tools.ota_experiment import propose_pairs
from gateway.ota.decoded import parse_decoded_line


def _lines() -> list[str]:
    return [
        "t=0.000 dir=gw->dev dev=0x143e cmd=2 payload=0849028a kind=analog_x10 value=65.0 label=occupied_heat_setpoint",
        "t=0.120 dir=dev->gw dev=0x143e cmd=2 payload=080a028a kind=analog_x10 value=65.0 label=occupied_heat_setpoint",
        "t=5.000 dir=gw->dev dev=0x143e cmd=2 payload=084b029e kind=analog_x10 value=67.0 label=occupied_cool_setpoint",
        "t=5.180 dir=dev->gw dev=0x143e cmd=2 payload=082d029e kind=analog_x10 value=67.0 label=occupied_cool_setpoint",
        "t=10.000 dir=gw->dev dev=0x143e cmd=2 payload=084c0294 kind=analog_x10 value=66.0",
        "t=10.150 dir=dev->gw dev=0x143e cmd=2 payload=08450294 kind=analog_x10 value=66.0",
    ]


class ExperimentProposalTests(unittest.TestCase):
    def test_proposes_known_pairs(self) -> None:
        decoded = [parse_decoded_line(line) for line in _lines()]
        decoded = [line for line in decoded if line is not None]
        proposals = propose_pairs(decoded, window_ms=5000, min_confidence=0.6)
        pairs = {(p.family, p.write_code, p.report_code, p.kind) for p in proposals}
        self.assertIn((0x08, 0x49, 0x0A, "analog_x10"), pairs)
        self.assertIn((0x08, 0x4B, 0x2D, "analog_x10"), pairs)
        self.assertIn((0x08, 0x4C, 0x45, "analog_x10"), pairs)
        for proposal in proposals:
            if proposal.write_code in (0x49, 0x4B, 0x4C):
                self.assertGreaterEqual(proposal.confidence, 0.6)


if __name__ == "__main__":
    unittest.main()
