import tempfile
import unittest
from pathlib import Path

from aco.cost import CostWeights
from controller.control_cycle import ControlCycleService
from controller.latency import LatencyTracker
from controller.rerouting import FlowRegistry
from controller.telemetry import (
    PortCounters,
    TelemetryCollector,
)
from controller.telemetry_history import TelemetryHistory
from controller.topology import TopologyManager
from learning.dataset import LearningDataset
from prediction.engine import PredictionConfig, PredictionEngine
from recommendation.engine import RecommendationEngine


class ControlCycleServiceTests(unittest.TestCase):
    def test_cycle_builds_metrics_and_persists_history(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            topology = TopologyManager()
            topology.add_link(1, 2, 7, 8)
            telemetry = TelemetryCollector(10_000, 5)
            telemetry.update(
                1,
                7,
                PortCounters(0, 0, 0, 0, 0, 0, 10),
            )
            telemetry.update(
                1,
                7,
                PortCounters(
                    0, 100, 0, 10, 0, 0, 11
                ),
            )
            history_path = (
                Path(directory) / "telemetry.jsonl"
            )
            service = ControlCycleService(
                telemetry=telemetry,
                latency=LatencyTracker(0.3, 5),
                telemetry_history=TelemetryHistory(
                    history_path, 1, 100
                ),
                prediction=PredictionEngine(
                    PredictionConfig()
                ),
                recommendation=RecommendationEngine(
                    minimum_confidence=0.6,
                    validity_seconds=90,
                ),
                learning_dataset=LearningDataset(
                    Path(directory) / "learning.jsonl",
                    10,
                ),
                flow_registry=FlowRegistry(),
                optimizer_weights=CostWeights(),
            )

            result = service.analyze(
                topology=topology,
                monotonic_now=12,
                wall_now=100,
            )

            self.assertTrue(result.metrics[(1, 2)].available)
            self.assertEqual(result.forecasts, ())
            self.assertEqual(result.recommendations, ())
            self.assertEqual(
                result.settled_learning_outcomes, 0
            )
            self.assertTrue(history_path.exists())


if __name__ == "__main__":
    unittest.main()