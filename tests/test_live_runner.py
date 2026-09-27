import unittest

from experiments.live_runner import parse_iperf3, parse_ping


class LiveRunnerParserTests(unittest.TestCase):
    def test_parse_linux_ping(self) -> None:
        output = """
10 packets transmitted, 9 received, 10% packet loss
rtt min/avg/max/mdev = 1.000/2.500/4.000/0.200 ms
"""
        self.assertEqual(parse_ping(output), (2.5, 10.0))

    def test_parse_iperf3_json(self) -> None:
        output = (
            '{"end":{"sum_received":'
            '{"bits_per_second":98765432}}}'
        )
        self.assertAlmostEqual(
            parse_iperf3(output) or 0.0,
            98.765432,
            places=6,
        )

    def test_invalid_outputs_return_none(self) -> None:
        self.assertEqual(parse_ping("failed"), (None, None))
        self.assertIsNone(parse_iperf3("not-json"))


if __name__ == "__main__":
    unittest.main()