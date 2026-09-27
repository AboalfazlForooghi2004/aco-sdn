import unittest

from controller.flow_identity import FlowSelector
from controller.rerouting import ActiveFlow, FlowRegistry


def active(selector: FlowSelector) -> ActiveFlow:
    return ActiveFlow(
        source_mac=selector.source_mac,
        destination_mac=selector.destination_mac,
        source_dpid=1,
        destination_dpid=2,
        source_host_port=1,
        destination_host_port=2,
        path=(1, 2),
        installed_cost=0.5,
        last_reroute_at=0,
        selector=selector,
    )


class FlowIdentityTests(unittest.TestCase):
    def test_reverse_selector_has_same_bidirectional_key(self) -> None:
        selector = FlowSelector(
            source_mac="00:00:00:00:00:01",
            destination_mac="00:00:00:00:00:02",
            eth_type=0x0800,
            ipv4_source="10.0.0.1",
            ipv4_destination="10.0.0.2",
            ip_protocol=6,
            source_port=12345,
            destination_port=443,
        )

        self.assertEqual(selector.key, selector.reverse().key)
        self.assertEqual(
            selector.openflow_match()["tcp_dst"], 443
        )
        self.assertEqual(
            selector.reverse().openflow_match()["tcp_src"],
            443,
        )

    def test_registry_distinguishes_same_macs_by_ports(self) -> None:
        base = {
            "source_mac": "00:00:00:00:00:01",
            "destination_mac": "00:00:00:00:00:02",
            "eth_type": 0x0800,
            "ipv4_source": "10.0.0.1",
            "ipv4_destination": "10.0.0.2",
            "ip_protocol": 6,
            "destination_port": 443,
        }
        first = active(
            FlowSelector(source_port=10001, **base)
        )
        second = active(
            FlowSelector(source_port=10002, **base)
        )
        registry = FlowRegistry()

        registry.register_initial(first)
        registry.register_initial(second)

        self.assertEqual(len(registry.flows), 2)
        removed = registry.remove_by_match(
            first.selector.openflow_match()
        )
        self.assertEqual(removed, first)
        self.assertEqual(registry.flows, (second,))

    def test_selector_matches_exact_five_tuple(self) -> None:
        selector = FlowSelector(
            source_mac="00:00:00:00:00:01",
            destination_mac="00:00:00:00:00:02",
            eth_type=0x0800,
            ipv4_source="10.0.0.1",
            ipv4_destination="10.0.0.2",
            ip_protocol=17,
            source_port=53000,
            destination_port=53,
        )
        match = selector.openflow_match()

        self.assertTrue(selector.matches(match))
        match["udp_dst"] = 5353
        self.assertFalse(selector.matches(match))


if __name__ == "__main__":
    unittest.main()