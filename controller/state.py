from __future__ import annotations

import threading
import math
from dataclasses import asdict, dataclass
from enum import Enum
from typing import Any

from aco.models import LinkMetrics
from controller.events import EventRecord
from controller.rerouting import ActiveFlow, MigrationPlan
from prediction.engine import EdgeForecast
from recommendation.engine import Recommendation


class OperatingMode(str, Enum):
    OBSERVE = "observe"
    RECOMMEND = "recommend"
    AUTOPILOT = "autopilot"


@dataclass(frozen=True, slots=True)
class MigrationProposal:
    proposal_id: str
    source_mac: str
    destination_mac: str
    old_path: tuple[int, ...]
    new_path: tuple[int, ...]
    old_cost: float | None
    new_cost: float
    forced: bool
    created_at: float
    simulation: dict[str, Any] | None = None

    @classmethod
    def from_plan(
        cls,
        plan: MigrationPlan,
        created_at: float,
        simulation: dict[str, Any] | None = None,
    ) -> "MigrationProposal":
        flow = plan.flow
        return cls(
            proposal_id=(
                f"{flow.key[0]}-{flow.key[1]}-"
                f"{'-'.join(map(str, plan.decision.path))}"
            ),
            source_mac=flow.source_mac,
            destination_mac=flow.destination_mac,
            old_path=flow.path,
            new_path=plan.decision.path,
            old_cost=(
                plan.current_cost
                if math.isfinite(plan.current_cost)
                else None
            ),
            new_cost=plan.decision.cost,
            forced=plan.forced,
            created_at=created_at,
            simulation=simulation,
        )


@dataclass(frozen=True, slots=True)
class ControlSnapshot:
    generated_at: float
    mode: str
    health_score: int
    switches: tuple[int, ...]
    links: tuple[dict[str, Any], ...]
    metrics: tuple[dict[str, Any], ...]
    forecasts: tuple[dict[str, Any], ...]
    recommendations: tuple[dict[str, Any], ...]
    flows: tuple[dict[str, Any], ...]
    migration_proposals: tuple[dict[str, Any], ...]
    events: tuple[dict[str, Any], ...]

    @classmethod
    def empty(cls, mode: OperatingMode) -> "ControlSnapshot":
        return cls(
            generated_at=0.0,
            mode=mode.value,
            health_score=0,
            switches=(),
            links=(),
            metrics=(),
            forecasts=(),
            recommendations=(),
            flows=(),
            migration_proposals=(),
            events=(),
        )


def calculate_health_score(
    metrics: dict[tuple[int, int], LinkMetrics],
    forecasts: tuple[EdgeForecast, ...],
) -> int:
    if not metrics:
        return 0
    values = tuple(metrics.values())
    availability = (
        sum(1 for item in values if item.available) / len(values)
    )
    available = tuple(item for item in values if item.available)
    average_utilization = (
        sum(item.utilization for item in available)
        / len(available)
        if available
        else 1.0
    )
    average_loss = (
        sum(item.loss for item in available) / len(available)
        if available
        else 1.0
    )
    critical_ratio = (
        sum(
            1
            for forecast in forecasts
            if forecast.risk == "critical"
        )
        / max(len(forecasts), 1)
    )
    score = (
        availability * 40.0
        + (1.0 - min(average_utilization, 1.0)) * 25.0
        + (1.0 - min(average_loss * 10.0, 1.0)) * 20.0
        + (1.0 - critical_ratio) * 15.0
    )
    return round(max(0.0, min(100.0, score)))


class SnapshotStore:
    """Thread-safe immutable control-plane state for API readers."""

    def __init__(self, mode: OperatingMode) -> None:
        self._lock = threading.RLock()
        self._snapshot = ControlSnapshot.empty(mode)

    def publish(
        self,
        *,
        generated_at: float,
        mode: OperatingMode,
        switches: tuple[int, ...],
        links: dict,
        metrics: dict[tuple[int, int], LinkMetrics],
        forecasts: tuple[EdgeForecast, ...],
        recommendations: tuple[Recommendation, ...],
        flows: tuple[ActiveFlow, ...],
        proposals: tuple[MigrationProposal, ...],
        events: tuple[EventRecord, ...],
    ) -> ControlSnapshot:
        snapshot = ControlSnapshot(
            generated_at=generated_at,
            mode=mode.value,
            health_score=calculate_health_score(
                metrics, forecasts
            ),
            switches=tuple(sorted(switches)),
            links=tuple(
                {
                    "source": source,
                    "target": target,
                    "source_port": ports.source_port,
                    "target_port": ports.target_port,
                }
                for (source, target), ports in sorted(
                    links.items()
                )
            ),
            metrics=tuple(
                {
                    "source": source,
                    "target": target,
                    **asdict(value),
                }
                for (source, target), value in sorted(
                    metrics.items()
                )
            ),
            forecasts=tuple(
                {
                    **asdict(item),
                    "edge": list(item.edge),
                }
                for item in forecasts
            ),
            recommendations=tuple(
                asdict(item) for item in recommendations
            ),
            flows=tuple(asdict(item) for item in flows),
            migration_proposals=tuple(
                asdict(item) for item in proposals
            ),
            events=tuple(asdict(item) for item in events),
        )
        with self._lock:
            self._snapshot = snapshot
        return snapshot

    def get(self) -> ControlSnapshot:
        with self._lock:
            return self._snapshot