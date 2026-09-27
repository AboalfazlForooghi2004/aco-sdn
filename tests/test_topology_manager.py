import unittest

from aco.models import LinkMetrics
from controller.topology import TopologyManager


class TopologyManagerTests(unittest.TestCase):
    def test_directed_links_and_ports_are_recorded(self) -> None:
        topology = TopologyManager()
        topology.add_link(1, 2, 11, 22)
        topology.add_link(2, 1, 22, 11)

        self.assertEqual(topology.switches, frozenset({1, 2}))
        self.assertEqual(topology.output_port(1, 2), 11)
        self.assertEqual(topology.output_port(2, 1), 22)
        self.assertTrue(topology.is_link_port(1, 11))
        self.assertFalse(topology.is_link_port(1, 99))

    def test_host_learning_detects_moves(self) -> None:
        topology = TopologyManager()

        self.assertTrue(
            topology.learn_host(
                "AA:BB:CC:DD:EE:FF", 1, 3, observed_at=1.0
            )
        )
        self.assertFalse(
            topology.learn_host(
                "aa:bb:cc:dd:ee:ff", 1, 3, observed_at=2.0
            )
        )
        self.assertTrue(
            topology.learn_host(
                "aa:bb:cc:dd:ee:ff", 2, 7, observed_at=3.0
            )
        )
        location = topology.host_location(
            "AA:BB:CC:DD:EE:FF"
        )
        self.assertIsNotNone(location)
        assert location is not None
        self.assertEqual((location.dpid, location.port), (2, 7))

    def test_switch_removal_cleans_links_and_hosts(self) -> None:
        topology = TopologyManager()
        topology.add_link(1, 2, 11, 22)
        topology.add_link(2, 1, 22, 11)
        topology.learn_host("00:00:00:00:00:01", 1, 1)

        topology.remove_switch(1)

        self.assertNotIn(1, topology.switches)
        self.assertEqual(topology.links, {})
        self.assertIsNone(
            topology.host_location("00:00:00:00:00:01")
        )

    def test_graph_uses_supplied_link_metrics(self) -> None:
        topology = TopologyManager()
        topology.add_link(1, 2, 11, 22)
        metrics = LinkMetrics(
            latency_ms=12,
            utilization=0.4,
            loss=0.01,
        )

        graph = topology.build_graph({(1, 2): metrics})

        self.assertEqual(graph.metrics("1", "2"), metrics)


if __name__ == "__main__":
    unittest.main()