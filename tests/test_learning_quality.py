import json
import tempfile
import unittest
from pathlib import Path

from learning.encoder import GraphObservationEncoder
from learning.quality import inspect_dataset
from learning.shadow import (
    PromotionGate,
    ShadowEvaluation,
)


def decision(decision_id: str, safe: bool = True) -> dict:
    return {
        "schema_version": 1,
        "record_type": "decision",
        "decision_id": decision_id,
        "source_dpid": 1,
        "destination_dpid": 3,
        "current_path": [1, 2, 3],
        "candidate_path": [1, 3],
        "current_cost": 0.8,
        "candidate_cost": 0.4,
        "simulation_safe": safe,
        "link_features": [
            {
                "source": 1,
                "target": 2,
                "latency_ms": 10,
                "utilization": 0.7,
                "loss": 0.01,
                "available": True,
            },
            {
                "source": 2,
                "target": 3,
                "latency_ms": 10,
                "utilization": 0.7,
                "loss": 0.01,
                "available": True,
            },
            {
                "source": 1,
                "target": 3,
                "latency_ms": 5,
                "utilization": 0.2,
                "loss": 0.0,
                "available": True,
            },
        ],
    }


class LearningQualityTests(unittest.TestCase):
    def test_encoder_builds_stable_graph_features(self) -> None:
        encoded = GraphObservationEncoder().encode(
            decision("d1")
        )

        self.assertEqual(encoded.node_ids, (1, 2, 3))
        self.assertEqual(
            encoded.edge_index, ((0, 1), (0, 2), (1, 2))
        )
        self.assertEqual(len(encoded.node_features[0]), 4)
        self.assertEqual(len(encoded.edge_features[0]), 6)
        self.assertEqual(len(encoded.global_features), 7)
        self.assertEqual(encoded.edge_features[1][-1], 1.0)
        self.assertEqual(encoded.edge_features[0][-2], 1.0)

    def test_quality_report_detects_gaps_and_duplicates(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "learning.jsonl"
            records = [
                decision("d1"),
                decision("d1"),
                decision("d2", safe=False),
                {
                    "record_type": "outcome",
                    "decision_id": "d1",
                },
                {
                    "record_type": "outcome",
                    "decision_id": "orphan",
                },
            ]
            path.write_text(
                "\n".join(json.dumps(item) for item in records)
                + "\ninvalid\n"
            )

            report = inspect_dataset(path)

            self.assertEqual(report.decisions, 2)
            self.assertEqual(report.completed_episodes, 1)
            self.assertEqual(report.pending_outcomes, 1)
            self.assertEqual(report.orphan_outcomes, 1)
            self.assertEqual(report.duplicate_decisions, 1)
            self.assertEqual(report.invalid_records, 1)
            self.assertEqual(report.outcome_coverage, 0.5)
            self.assertEqual(report.unsafe_candidate_rate, 0.5)

    def test_promotion_gate_blocks_unsafe_or_small_runs(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "learning.jsonl"
            path.write_text(json.dumps(decision("d1")) + "\n")
            quality = inspect_dataset(path)
        baseline = ShadowEvaluation(1, -0.8, 0.0, 0.0)
        candidate = ShadowEvaluation(1, -0.4, 1.0, 0.1)

        assessment = PromotionGate().assess(
            candidate=candidate,
            baseline=baseline,
            quality=quality,
        )

        self.assertFalse(assessment.eligible)
        self.assertIn(
            "insufficient_episodes", assessment.violations
        )
        self.assertIn(
            "insufficient_outcome_coverage",
            assessment.violations,
        )
        self.assertIn(
            "unsafe_action_rate_exceeded",
            assessment.violations,
        )


if __name__ == "__main__":
    unittest.main()