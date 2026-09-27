import json
import tempfile
import unittest
from pathlib import Path

from aco.cost import CostWeights
from aco.models import LinkMetrics
from controller.rerouting import ActiveFlow
from controller.topology import TopologyManager
from learning.dataset import LearningDataset
from learning.environment import OfflineRoutingEnv, load_episodes
from learning.shadow import (
    AlwaysKeepPolicy,
    GreedySafePolicy,
    ShadowEvaluator,
)


class LearningPipelineTests(unittest.TestCase):
    def build_context(self):
        topology = TopologyManager()
        topology.add_link(1, 2, 12, 21)
        topology.add_link(2, 1, 21, 12)
        topology.add_link(1, 3, 13, 31)
        topology.add_link(3, 1, 31, 13)
        topology.add_link(3, 2, 32, 23)
        topology.add_link(2, 3, 23, 32)
        metrics = {
            edge: LinkMetrics(
                latency_ms=5,
                utilization=0.2,
                loss=0.001,
            )
            for edge in topology.links
        }
        flow = ActiveFlow(
            source_mac="00:00:00:00:00:01",
            destination_mac="00:00:00:00:00:02",
            source_dpid=1,
            destination_dpid=2,
            source_host_port=1,
            destination_host_port=2,
            path=(1, 2),
            installed_cost=0.5,
            last_reroute_at=0,
        )
        return topology, metrics, flow

    def test_decision_and_delayed_outcome_are_joinable(self) -> None:
        topology, metrics, flow = self.build_context()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "learning.jsonl"
            dataset = LearningDataset(
                path, outcome_horizon_seconds=10
            )
            recorded = dataset.record_decision(
                decision_id="d1",
                observed_at=100,
                flow=flow,
                candidate_path=(1, 3, 2),
                current_cost=0.5,
                candidate_cost=0.3,
                algorithm="mmas",
                mode="recommend",
                simulation_safe=True,
                metrics=metrics,
            )
            self.assertTrue(recorded)
            self.assertFalse(
                dataset.record_decision(
                    decision_id="d1",
                    observed_at=100,
                    flow=flow,
                    candidate_path=(1, 3, 2),
                    current_cost=0.5,
                    candidate_cost=0.3,
                    algorithm="mmas",
                    mode="recommend",
                    simulation_safe=True,
                    metrics=metrics,
                )
            )
            self.assertEqual(
                dataset.settle_due(
                    observed_at=109,
                    flows=(flow,),
                    topology=topology,
                    metrics=metrics,
                    weights=CostWeights(),
                ),
                0,
            )
            self.assertEqual(
                dataset.settle_due(
                    observed_at=111,
                    flows=(flow,),
                    topology=topology,
                    metrics=metrics,
                    weights=CostWeights(),
                ),
                1,
            )
            episodes = load_episodes(path)
            self.assertEqual(len(episodes), 1)
            self.assertIsNotNone(episodes[0].outcome)
            self.assertFalse(episodes[0].outcome["applied"])

    def test_shadow_evaluation_never_touches_controller(self) -> None:
        topology, metrics, flow = self.build_context()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "learning.jsonl"
            dataset = LearningDataset(path, 1)
            dataset.record_decision(
                decision_id="d1",
                observed_at=0,
                flow=flow,
                candidate_path=(1, 3, 2),
                current_cost=0.5,
                candidate_cost=0.3,
                algorithm="mmas",
                mode="recommend",
                simulation_safe=True,
                metrics=metrics,
            )
            env = OfflineRoutingEnv(load_episodes(path))
            evaluator = ShadowEvaluator()

            keep = evaluator.evaluate(env, AlwaysKeepPolicy())
            greedy = evaluator.evaluate(env, GreedySafePolicy())

            self.assertEqual(keep.candidate_selection_rate, 0)
            self.assertEqual(greedy.candidate_selection_rate, 1)
            self.assertGreater(
                greedy.average_reward, keep.average_reward
            )
            self.assertEqual(greedy.unsafe_action_rate, 0)

    def test_invalid_json_lines_are_ignored(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "learning.jsonl"
            path.write_text(
                "not-json\n"
                + json.dumps(
                    {
                        "record_type": "decision",
                        "decision_id": "d1",
                        "source_dpid": 1,
                        "destination_dpid": 2,
                        "current_cost": 1,
                        "candidate_cost": 0.5,
                        "simulation_safe": True,
                        "current_path": [1, 2],
                        "candidate_path": [1, 3, 2],
                        "link_features": [],
                    }
                )
                + "\n"
            )
            self.assertEqual(len(load_episodes(path)), 1)


if __name__ == "__main__":
    unittest.main()