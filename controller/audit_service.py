from __future__ import annotations

from typing import Iterable

from controller.change_service import RouteChangeOutcome
from controller.events import EventTimeline
from controller.state import MigrationProposal
from recommendation.engine import Recommendation


class ControllerAuditService:
    """Own deduplicated operator logs and durable control-plane events."""

    def __init__(self, timeline: EventTimeline, logger) -> None:
        self.timeline = timeline
        self.logger = logger
        self._recommendation_ids: set[str] = set()
        self._proposal_ids: set[str] = set()

    def controller_started(
        self, *, occurred_at: float, mode: str
    ) -> None:
        self.timeline.append(
            occurred_at=occurred_at,
            category="controller",
            severity="info",
            title="Controller started",
            details={"mode": mode},
        )

    def recovered_transactions(
        self,
        *,
        occurred_at: float,
        transaction_ids: Iterable[str],
    ) -> None:
        values = tuple(transaction_ids)
        if not values:
            return
        self.timeline.append(
            occurred_at=occurred_at,
            category="controller",
            severity="warning",
            title="Unfinished route transactions recovered",
            details={
                "transaction_ids": list(values),
                "action": "purge managed rules as switches reconnect",
            },
        )

    def recommendations(
        self,
        values: tuple[Recommendation, ...],
        *,
        occurred_at: float,
    ) -> None:
        current_ids = {
            recommendation.recommendation_id
            for recommendation in values
        }
        for recommendation in values:
            if (
                recommendation.recommendation_id
                in self._recommendation_ids
            ):
                continue
            self.logger.warning(
                "network recommendation: id=%s title=%s "
                "urgency=%s confidence=%.2f affected=%s "
                "signals=%s",
                recommendation.recommendation_id,
                recommendation.title,
                recommendation.urgency,
                recommendation.confidence,
                len(recommendation.affected_flows),
                recommendation.rationale,
            )
            self.timeline.append(
                occurred_at=occurred_at,
                category="recommendation",
                severity=(
                    "warning"
                    if recommendation.urgency == "high"
                    else "info"
                ),
                title=recommendation.title,
                details={
                    "recommendation_id": (
                        recommendation.recommendation_id
                    ),
                    "confidence": recommendation.confidence,
                    "urgency": recommendation.urgency,
                    "signals": list(recommendation.rationale),
                    "affected_flows": [
                        list(item)
                        for item in recommendation.affected_flows
                    ],
                },
            )
        self._recommendation_ids = current_ids

    def proposal(
        self,
        proposal: MigrationProposal,
        *,
        occurred_at: float,
    ) -> bool:
        if proposal.proposal_id in self._proposal_ids:
            return False
        self.timeline.append(
            occurred_at=occurred_at,
            category="migration",
            severity="warning" if proposal.forced else "info",
            title="Route migration proposed",
            details={
                "proposal_id": proposal.proposal_id,
                "source_mac": proposal.source_mac,
                "destination_mac": proposal.destination_mac,
                "old_path": list(proposal.old_path),
                "new_path": list(proposal.new_path),
                "old_cost": proposal.old_cost,
                "new_cost": proposal.new_cost,
                "forced": proposal.forced,
                "simulation": proposal.simulation,
            },
        )
        self._proposal_ids.add(proposal.proposal_id)
        return True

    def retain_proposals(
        self, proposals: Iterable[MigrationProposal]
    ) -> None:
        self._proposal_ids = {
            proposal.proposal_id for proposal in proposals
        }

    def migration_blocked(
        self,
        proposal: MigrationProposal,
        *,
        violations: Iterable[str],
        warnings: Iterable[str],
        occurred_at: float,
    ) -> None:
        violations = tuple(violations)
        self.logger.error(
            "reroute blocked by what-if simulation: id=%s "
            "violations=%s",
            proposal.proposal_id,
            violations,
        )
        self.timeline.append(
            occurred_at=occurred_at,
            category="migration",
            severity="error",
            title="Route migration blocked by simulation",
            details={
                "proposal_id": proposal.proposal_id,
                "violations": list(violations),
                "warnings": list(warnings),
            },
        )

    def transaction_started(
        self,
        *,
        transaction_id: str,
        proposal: MigrationProposal,
        old_path: tuple[int, ...],
        new_path: tuple[int, ...],
        topology_generation: int,
        occurred_at: float,
    ) -> None:
        self.logger.info(
            "route transaction started: id=%s old=%s new=%s "
            "generation=%s",
            transaction_id,
            old_path,
            new_path,
            topology_generation,
        )
        self.timeline.append(
            occurred_at=occurred_at,
            category="migration",
            severity="info",
            title="Route migration transaction started",
            details={
                "transaction_id": transaction_id,
                "proposal_id": proposal.proposal_id,
                "old_path": list(old_path),
                "new_path": list(new_path),
                "topology_generation": topology_generation,
            },
        )

    def migration_failed(
        self,
        proposal: MigrationProposal,
        error: Exception,
        *,
        occurred_at: float,
    ) -> None:
        self.logger.warning("flow reroute failed: %s", error)
        self.timeline.append(
            occurred_at=occurred_at,
            category="migration",
            severity="error",
            title="Route migration failed",
            details={
                "proposal_id": proposal.proposal_id,
                "error": str(error),
            },
        )

    def route_change_outcome(
        self,
        outcome: RouteChangeOutcome,
        *,
        occurred_at: float,
    ) -> None:
        result = outcome.result
        new_path = outcome.migration.decision.path
        if result.status == "committed":
            self.logger.info(
                "route transaction committed: id=%s old=%s new=%s",
                result.transaction_id,
                outcome.flow.path,
                new_path,
            )
            severity = "info"
            title = "Route migration committed"
        else:
            self.logger.error(
                "route transaction rolled back: id=%s reason=%s",
                result.transaction_id,
                result.reason,
            )
            severity = "error"
            title = "Route migration rolled back"
        self.timeline.append(
            occurred_at=occurred_at,
            category="migration",
            severity=severity,
            title=title,
            details={
                "transaction_id": result.transaction_id,
                "proposal_id": outcome.proposal.proposal_id,
                "status": result.status,
                "reason": result.reason,
                "old_path": list(outcome.flow.path),
                "new_path": list(new_path),
            },
        )
