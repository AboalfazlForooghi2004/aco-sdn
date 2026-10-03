import tempfile
import unittest
from pathlib import Path

from controller.audit_service import ControllerAuditService
from controller.events import EventTimeline
from controller.state import MigrationProposal
from recommendation.engine import Recommendation


class FakeLogger:
    def __init__(self) -> None:
        self.warnings = []
        self.errors = []
        self.infos = []

    def warning(self, *args) -> None:
        self.warnings.append(args)

    def error(self, *args) -> None:
        self.errors.append(args)

    def info(self, *args) -> None:
        self.infos.append(args)


class ControllerAuditServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.timeline = EventTimeline(
            Path(self.directory.name) / "events.jsonl", 20
        )
        self.logger = FakeLogger()
        self.audit = ControllerAuditService(
            self.timeline, self.logger
        )

    def tearDown(self) -> None:
        self.directory.cleanup()

    def test_recommendations_are_deduplicated_until_cleared(self) -> None:
        recommendation = Recommendation(
            recommendation_id="edge-1-2-predictive",
            category="preventive_action",
            title="Evaluate rerouting",
            rationale=("predicted_congestion",),
            action_type="simulate_reroute",
            confidence=0.8,
            urgency="medium",
            affected_flows=(("a", "b"),),
            valid_until=100,
            auto_apply_allowed=False,
        )

        self.audit.recommendations((recommendation,), occurred_at=1)
        self.audit.recommendations((recommendation,), occurred_at=2)
        self.audit.recommendations((), occurred_at=3)
        self.audit.recommendations((recommendation,), occurred_at=4)

        self.assertEqual(len(self.logger.warnings), 2)
        self.assertEqual(
            len(self.timeline.recent(category="recommendation")), 2
        )

    def test_proposal_is_recorded_once_per_active_set(self) -> None:
        proposal = MigrationProposal(
            proposal_id="p1",
            source_mac="a",
            destination_mac="b",
            old_path=(1, 2),
            new_path=(1, 3, 2),
            old_cost=1.0,
            new_cost=0.5,
            forced=False,
            created_at=1,
            simulation={"safe_to_apply": True},
        )

        self.assertTrue(
            self.audit.proposal(proposal, occurred_at=1)
        )
        self.assertFalse(
            self.audit.proposal(proposal, occurred_at=2)
        )
        self.audit.retain_proposals(())
        self.assertTrue(
            self.audit.proposal(proposal, occurred_at=3)
        )

        self.assertEqual(
            len(self.timeline.recent(category="migration")), 2
        )


if __name__ == "__main__":
    unittest.main()
