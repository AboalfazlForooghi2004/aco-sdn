import unittest

from aco.cost import CostWeights
from aco.models import LinkMetrics
from controller.simulation import WhatIfSimulator
from controller.topology import TopologyManager


class WhatIfSimulatorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.topology = TopologyManager()
        for source, target, source_port, target_port in (
            (1, 2, 12, 21),
            (2, 4, 24, 42),
            (1, 3, 13, 31),
            (3, 4, 34, 43),
        ):
            self.topology.add_link(
                source, target, source_port, target_port
            )
            self.topology.add_link(
                target, source, target_port, source_port
            )
        congested = LinkMetrics(
            latency_ms=30,
            utilization=0.9,
            loss=0.05,
        )
        healthy = LinkMetrics(
            latency_ms=5,
            utilization=0.2,
            loss=0.001,
        )
        self.metrics = {
            edge: (
                congested
                if edge in {(1, 2), (2, 4)}
                else healthy
            )
            for edge in self.topology.links
        }
        self.simulator = WhatIfSimulator(CostWeights())

    def test_better_available_path_is_safe(self) -> None:
        result = self.simulator.compare(
            self.topology,
            self.metrics,
            current_path=(1, 2, 4),
            proposed_path=(1, 3, 4),
        )

        self.assertTrue(result.safe_to_apply)
        self.assertTrue(result.proposed.valid)
        self.assertTrue(result.proposed.available)
        self.assertGreater(
            result.cost_improvement_ratio or 0, 0
        )
        self.assertLess(result.latency_change_ms or 0, 0)
        self.assertLess(
            result.maximum_utilization_change or 0, 0
        )
        self.assertIn(
            "flow_bandwidth_not_modeled", result.warnings
        )

    def test_looped_candidate_is_blocked(self) -> None:
        result = self.simulator.compare(
            self.topology,
            self.metrics,
            current_path=(1, 2, 4),
            proposed_path=(1, 3, 1, 2, 4),
        )

        self.assertFalse(result.safe_to_apply)
        self.assertIn(
            "loop_detected", result.proposed.violations
        )

    def test_unavailable_candidate_is_blocked(self) -> None:
        self.metrics[(1, 3)] = LinkMetrics(available=False)

        result = self.simulator.compare(
            self.topology,
            self.metrics,
            current_path=(1, 2, 4),
            proposed_path=(1, 3, 4),
        )

        self.assertFalse(result.safe_to_apply)
        self.assertFalse(result.proposed.available)
        self.assertIn(
            "unavailable_link", result.proposed.violations
        )

    def test_estimated_flow_load_can_block_candidate(self) -> None:
        result = self.simulator.compare(
            self.topology,
            self.metrics,
            current_path=(1, 2, 4),
            proposed_path=(1, 3, 4),
            flow_demand_bps=80_000_000,
            link_capacity_bps=100_000_000,
        )

        self.assertFalse(result.safe_to_apply)
        self.assertEqual(
            result.proposed.maximum_utilization, 1.0
        )
        self.assertIn(
            "projected_utilization_exceeds_safety_limit",
            result.proposed.violations,
        )
        self.assertNotIn(
            "flow_bandwidth_not_modeled", result.warnings
        )

    def test_small_estimated_flow_load_is_projected(self) -> None:
        result = self.simulator.compare(
            self.topology,
            self.metrics,
            current_path=(1, 2, 4),
            proposed_path=(1, 3, 4),
            flow_demand_bps=10_000_000,
            link_capacity_bps=100_000_000,
        )

        self.assertTrue(result.safe_to_apply)
        self.assertAlmostEqual(
            result.proposed.maximum_utilization or 0,
            0.3,
        )
        self.assertIn(
            "flow_demand_estimated_from_openflow_counters",
            result.warnings,
        )

    def test_different_endpoints_are_blocked(self) -> None:
        result = self.simulator.compare(
            self.topology,
            self.metrics,
            current_path=(1, 2, 4),
            proposed_path=(1, 3),
        )

        self.assertFalse(result.safe_to_apply)
        self.assertIn(
            "path_endpoints_do_not_match", result.warnings
        )


if __name__ == "__main__":
    unittest.main()