import unittest

from aco.models import LinkMetrics
from aco.optimizer import ACOConfig, AntColonyOptimizer
from controller.rerouting import (
    ActiveFlow,
    FlowRegistry,
    RerouteManager,
    ReroutePolicy,
)
from controller.routing import RoutingService
from controller.topology import TopologyManager


class ReroutingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.topology = TopologyManager()
        for source, target, source_port, target_port in (
            (1, 2, 12, 21),
            (2, 1, 21, 12),
            (2, 4, 24, 42),
            (4, 2, 42, 24),
            (1, 3, 13, 31),
            (3, 1, 31, 13),
            (3, 4, 34, 43),
            (4, 3, 43, 34),
        ):
            self.topology.add_link(
                source, target, source_port, target_port
            )
        routing = RoutingService(
            AntColonyOptimizer(
                ACOConfig(ants=30, iterations=20, seed=8)
            )
        )
        self.manager = RerouteManager(
            routing,
            ReroutePolicy(
                utilization_threshold=0.8,
                utilization_hysteresis=0.1,
                loss_threshold=0.05,
                loss_hysteresis=0.01,
                minimum_improvement=0.1,
                cooldown_seconds=10,
            ),
        )
        self.flow = ActiveFlow(
            source_mac="00:00:00:00:00:01",
            destination_mac="00:00:00:00:00:02",
            source_dpid=1,
            destination_dpid=4,
            source_host_port=1,
            destination_host_port=9,
            path=(1, 2, 4),
            installed_cost=1.0,
            last_reroute_at=0,
        )

    def metrics(
        self,
    ) -> dict[tuple[int, int], LinkMetrics]:
        healthy = LinkMetrics(
            latency_ms=5,
            utilization=0.1,
            loss=0.0,
        )
        congested = LinkMetrics(
            latency_ms=30,
            utilization=0.95,
            loss=0.08,
        )
        return {
            edge: (
                congested
                if edge in {(1, 2), (2, 4)}
                else healthy
            )
            for edge in self.topology.links
        }

    def test_threshold_crossing_proposes_better_path(self) -> None:
        migration = self.manager.evaluate(
            self.flow,
            self.topology,
            self.metrics(),
            now=20,
        )

        self.assertIsNotNone(migration)
        assert migration is not None
        self.assertEqual(migration.decision.path, (1, 3, 4))
        self.assertFalse(migration.forced)

    def test_cooldown_blocks_non_failure_reroute(self) -> None:
        migration = self.manager.evaluate(
            self.flow,
            self.topology,
            self.metrics(),
            now=5,
        )

        self.assertIsNone(migration)

    def test_link_failure_bypasses_cooldown(self) -> None:
        metrics = self.metrics()
        metrics[(1, 2)] = LinkMetrics(available=False)

        migration = self.manager.evaluate(
            self.flow,
            self.topology,
            metrics,
            now=1,
        )

        self.assertIsNotNone(migration)
        assert migration is not None
        self.assertEqual(migration.decision.path, (1, 3, 4))
        self.assertTrue(migration.forced)

    def test_registry_deduplicates_reverse_direction(self) -> None:
        registry = FlowRegistry()
        registry.register_initial(self.flow)
        reverse = ActiveFlow(
            source_mac=self.flow.destination_mac,
            destination_mac=self.flow.source_mac,
            source_dpid=4,
            destination_dpid=1,
            source_host_port=9,
            destination_host_port=1,
            path=(4, 2, 1),
            installed_cost=1.0,
            last_reroute_at=2,
        )

        existing = registry.register_initial(reverse)

        self.assertEqual(existing, self.flow)
        self.assertEqual(len(registry.flows), 1)

    def test_hysteresis_keeps_edge_latched_until_clear(self) -> None:
        high = {
            (1, 2): LinkMetrics(utilization=0.85)
        }
        between = {
            (1, 2): LinkMetrics(utilization=0.75)
        }
        clear = {
            (1, 2): LinkMetrics(utilization=0.65)
        }

        self.manager.update_congestion_state(high)
        self.assertIn((1, 2), self.manager.congested_edges)
        self.manager.update_congestion_state(between)
        self.assertIn((1, 2), self.manager.congested_edges)
        self.manager.update_congestion_state(clear)
        self.assertNotIn(
            (1, 2), self.manager.congested_edges
        )

    def test_registry_finds_and_removes_host_flows(self) -> None:
        registry = FlowRegistry()
        registry.register_initial(self.flow)

        self.assertEqual(
            registry.flows_for_host(self.flow.source_mac),
            (self.flow,),
        )
        removed = registry.remove_by_macs(
            self.flow.destination_mac,
            self.flow.source_mac,
        )

        self.assertEqual(removed, self.flow)
        self.assertEqual(registry.flows, ())


if __name__ == "__main__":
    unittest.main()