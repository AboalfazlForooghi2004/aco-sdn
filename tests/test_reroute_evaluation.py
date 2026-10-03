import unittest

from aco.models import LinkMetrics
from aco.optimizer import ACOConfig, AntColonyOptimizer
from controller.flow_demand import FlowDemandEstimator
from controller.reroute_evaluation import RerouteEvaluationService
from controller.rerouting import (
    ActiveFlow,
    RerouteManager,
    ReroutePolicy,
)
from controller.routing import RoutingService
from controller.simulation import WhatIfSimulator
from controller.telemetry import TelemetryCollector
from controller.topology import TopologyManager


class RerouteEvaluationServiceTests(unittest.TestCase):
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
        optimizer = AntColonyOptimizer(
            ACOConfig(ants=30, iterations=20, seed=8)
        )
        routing = RoutingService(optimizer)
        manager = RerouteManager(
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
        telemetry = TelemetryCollector(1_000_000, 5)
        self.service = RerouteEvaluationService(
            reroute_manager=manager,
            simulator=WhatIfSimulator(optimizer.weights),
            flow_demand=FlowDemandEstimator(0.5, 5),
            telemetry=telemetry,
            default_link_capacity_bps=1_000_000,
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

    def metrics(self):
        healthy = LinkMetrics(
            latency_ms=5, utilization=0.1, loss=0.0
        )
        congested = LinkMetrics(
            latency_ms=30, utilization=0.95, loss=0.08
        )
        return {
            edge: (
                congested
                if edge in {(1, 2), (2, 4)}
                else healthy
            )
            for edge in self.topology.links
        }

    def test_builds_demand_aware_simulated_candidate(self) -> None:
        candidates = self.service.evaluate(
            flows=(self.flow,),
            topology=self.topology,
            metrics=self.metrics(),
            now=20,
            is_pending=lambda _flow: False,
        )

        self.assertEqual(len(candidates), 1)
        candidate = candidates[0]
        self.assertEqual(
            candidate.migration.decision.path, (1, 3, 4)
        )
        self.assertTrue(candidate.simulation.safe_to_apply)
        self.assertEqual(candidate.proposal.new_path, (1, 3, 4))

    def test_pending_flow_is_skipped(self) -> None:
        candidates = self.service.evaluate(
            flows=(self.flow,),
            topology=self.topology,
            metrics=self.metrics(),
            now=20,
            is_pending=lambda _flow: True,
        )

        self.assertEqual(candidates, ())


if __name__ == "__main__":
    unittest.main()
