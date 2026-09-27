import unittest

from aco.models import LinkMetrics
from aco.optimizer import ACOConfig, AntColonyOptimizer
from controller.flow_manager import build_bidirectional_plan
from controller.routing import RoutingService
from controller.topology import TopologyManager


class RoutingAndFlowTests(unittest.TestCase):
    def build_topology(self) -> TopologyManager:
        topology = TopologyManager()
        topology.add_link(1, 2, 12, 21)
        topology.add_link(2, 1, 21, 12)
        topology.add_link(2, 4, 24, 42)
        topology.add_link(4, 2, 42, 24)
        topology.add_link(1, 3, 13, 31)
        topology.add_link(3, 1, 31, 13)
        topology.add_link(3, 4, 34, 43)
        topology.add_link(4, 3, 43, 34)
        return topology

    def test_routing_uses_lower_cost_telemetry_path(self) -> None:
        topology = self.build_topology()
        congested = LinkMetrics(
            latency_ms=30,
            utilization=0.95,
            loss=0.05,
        )
        healthy = LinkMetrics(
            latency_ms=5,
            utilization=0.10,
            loss=0.0,
        )
        metrics = {
            (1, 2): congested,
            (2, 4): congested,
            (1, 3): healthy,
            (3, 4): healthy,
        }
        router = RoutingService(
            AntColonyOptimizer(
                ACOConfig(ants=25, iterations=20, seed=4)
            )
        )

        decision = router.select_path(topology, metrics, 1, 4)

        self.assertEqual(decision.path, (1, 3, 4))
        self.assertFalse(decision.used_fallback)

    def test_missing_telemetry_uses_minimum_hop_fallback(self) -> None:
        topology = self.build_topology()
        unavailable = {
            edge: LinkMetrics(available=False)
            for edge in topology.links
        }
        router = RoutingService(
            AntColonyOptimizer(
                ACOConfig(ants=5, iterations=5, seed=1)
            )
        )

        decision = router.select_path(
            topology, unavailable, 1, 4
        )

        self.assertEqual(decision.path, (1, 2, 4))
        self.assertTrue(decision.used_fallback)

    def test_bidirectional_plan_uses_link_and_host_ports(self) -> None:
        topology = self.build_topology()

        rules = build_bidirectional_plan(
            topology=topology,
            path=(1, 3, 4),
            source_mac="00:00:00:00:00:01",
            destination_mac="00:00:00:00:00:02",
            source_host_port=1,
            destination_host_port=9,
        )
        outputs = {
            (
                rule.dpid,
                rule.source_mac,
                rule.destination_mac,
            ): rule.output_port
            for rule in rules
        }

        self.assertEqual(
            outputs[
                (
                    1,
                    "00:00:00:00:00:01",
                    "00:00:00:00:00:02",
                )
            ],
            13,
        )
        self.assertEqual(
            outputs[
                (
                    4,
                    "00:00:00:00:00:01",
                    "00:00:00:00:00:02",
                )
            ],
            9,
        )
        self.assertEqual(
            outputs[
                (
                    1,
                    "00:00:00:00:00:02",
                    "00:00:00:00:00:01",
                )
            ],
            1,
        )


if __name__ == "__main__":
    unittest.main()