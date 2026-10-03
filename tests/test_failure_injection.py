import unittest

from experiments.failure_injection import (
    parse_output_ports,
)


class FailureInjectionTests(unittest.TestCase):
    def test_parse_output_ports_is_unique_and_sorted(self) -> None:
        dump = """
 cookie=0x1, priority=100 actions=output:3
 cookie=0x2, priority=100 actions=output:2
 cookie=0x3, priority=100 actions=output:3
 """

        self.assertEqual(
            parse_output_ports(dump), (2, 3)
        )

    def test_parse_output_ports_ignores_non_output_actions(
        self,
    ) -> None:
        self.assertEqual(
            parse_output_ports(
                "priority=0 actions=CONTROLLER:65535"
            ),
            (),
        )


if __name__ == "__main__":
    unittest.main()