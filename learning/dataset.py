from __future__ import annotations

import json
import math
import threading
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from aco.cost import CostWeights, path_cost
from aco.models import LinkMetrics
from controller.rerouting import ActiveFlow
from controller.topology import TopologyManager


@dataclass(frozen=True, slots=True)
class PendingDecision:
    decision_id: str
    observed_at: float
    due_at: float
    flow_key: tuple[str, str]
    current_path: tuple[int, ...]
    candidate_path: tuple[int, ...]


class LearningDataset:
    """Append-only decision/outcome dataset for safe offline learning."""

    def __init__(
        self,
        path: str | Path,
        outcome_horizon_seconds: float,
        enabled: bool = True,
    ) -> None:
        if outcome_horizon_seconds <= 0:
            raise ValueError(
                "outcome_horizon_seconds must be positive"
            )
        self.path = Path(path)
        self.outcome_horizon_seconds = outcome_horizon_seconds
        self.enabled = enabled
        self._lock = threading.RLock()
        self._pending: dict[str, PendingDecision] = {}
        self._recorded: set[str] = set()
        self._load_existing()

    @property
    def pending_count(self) -> int:
        with self._lock:
            return len(self._pending)

    def _load_existing(self) -> None:
        if not self.enabled or not self.path.exists():
            return
        decisions: dict[str, dict[str, Any]] = {}
        outcomes: set[str] = set()
        with self.path.open(encoding="utf-8") as records:
            for line in records:
                try:
                    record = json.loads(line)
                    decision_id = str(record["decision_id"])
                    if record["record_type"] == "decision":
                        decisions[decision_id] = record
                    elif record["record_type"] == "outcome":
                        outcomes.add(decision_id)
                except (
                    json.JSONDecodeError,
                    KeyError,
                    TypeError,
                ):
                    continue
        self._recorded.update(decisions)
        for decision_id, record in decisions.items():
            if decision_id in outcomes:
                continue
            try:
                observed_at = float(record["observed_at"])
                self._pending[decision_id] = PendingDecision(
                    decision_id=decision_id,
                    observed_at=observed_at,
                    due_at=(
                        observed_at
                        + self.outcome_horizon_seconds
                    ),
                    flow_key=self._freeze(
                        record["flow_key"]
                    ),
                    current_path=tuple(
                        int(node)
                        for node in record["current_path"]
                    ),
                    candidate_path=tuple(
                        int(node)
                        for node in record["candidate_path"]
                    ),
                )
            except (KeyError, TypeError, ValueError):
                continue

    @classmethod
    def _freeze(cls, value):
        if isinstance(value, list):
            return tuple(cls._freeze(item) for item in value)
        if isinstance(value, dict):
            return tuple(
                sorted(
                    (key, cls._freeze(item))
                    for key, item in value.items()
                )
            )
        return value

    def record_decision(
        self,
        *,
        decision_id: str,
        observed_at: float,
        flow: ActiveFlow,
        candidate_path: tuple[int, ...],
        current_cost: float | None,
        candidate_cost: float,
        algorithm: str,
        mode: str,
        simulation_safe: bool,
        metrics: dict[tuple[int, int], LinkMetrics],
    ) -> bool:
        if not self.enabled:
            return False
        with self._lock:
            if decision_id in self._recorded:
                return False
            record = {
                "schema_version": 1,
                "record_type": "decision",
                "decision_id": decision_id,
                "observed_at": observed_at,
                "flow_key": list(flow.key),
                "source_dpid": flow.source_dpid,
                "destination_dpid": flow.destination_dpid,
                "current_path": list(flow.path),
                "candidate_path": list(candidate_path),
                "current_cost": current_cost,
                "candidate_cost": candidate_cost,
                "algorithm": algorithm,
                "mode": mode,
                "simulation_safe": simulation_safe,
                "link_features": [
                    {
                        "source": edge[0],
                        "target": edge[1],
                        **asdict(value),
                    }
                    for edge, value in sorted(metrics.items())
                ],
            }
            self._append(record)
            self._recorded.add(decision_id)
            self._pending[decision_id] = PendingDecision(
                decision_id=decision_id,
                observed_at=observed_at,
                due_at=(
                    observed_at + self.outcome_horizon_seconds
                ),
                flow_key=flow.key,
                current_path=flow.path,
                candidate_path=candidate_path,
            )
        return True

    def settle_due(
        self,
        *,
        observed_at: float,
        flows: tuple[ActiveFlow, ...],
        topology: TopologyManager,
        metrics: dict[tuple[int, int], LinkMetrics],
        weights: CostWeights,
    ) -> int:
        if not self.enabled:
            return 0
        by_key = {flow.key: flow for flow in flows}
        settled = 0
        with self._lock:
            due = tuple(
                item
                for item in self._pending.values()
                if item.due_at <= observed_at
            )
            graph = topology.build_graph(metrics)
            for pending in due:
                flow = by_key.get(pending.flow_key)
                active_path = flow.path if flow is not None else ()
                path_metrics = [
                    metrics.get(edge)
                    for edge in zip(
                        active_path, active_path[1:]
                    )
                ]
                available = bool(active_path) and all(
                    item is not None and item.available
                    for item in path_metrics
                )
                active_cost: float | None = None
                if available:
                    value = path_cost(
                        graph,
                        [str(node) for node in active_path],
                        weights,
                    )
                    if math.isfinite(value):
                        active_cost = value
                average_utilization = self._average(
                    path_metrics, "utilization"
                )
                average_loss = self._average(
                    path_metrics, "loss"
                )
                total_latency_ms = (
                    sum(
                        item.latency_ms
                        for item in path_metrics
                        if item is not None
                    )
                    if path_metrics
                    else None
                )
                reward = (
                    -active_cost
                    if active_cost is not None
                    else -10.0
                )
                self._append(
                    {
                        "schema_version": 1,
                        "record_type": "outcome",
                        "decision_id": pending.decision_id,
                        "observed_at": observed_at,
                        "delay_seconds": (
                            observed_at - pending.observed_at
                        ),
                        "flow_present": flow is not None,
                        "applied": (
                            active_path == pending.candidate_path
                        ),
                        "active_path": list(active_path),
                        "available": available,
                        "active_cost": active_cost,
                        "average_utilization": (
                            average_utilization
                        ),
                        "average_loss": average_loss,
                        "total_latency_ms": total_latency_ms,
                        "reward": reward,
                    }
                )
                self._pending.pop(pending.decision_id, None)
                settled += 1
        return settled

    @staticmethod
    def _average(
        values: list[LinkMetrics | None],
        attribute: str,
    ) -> float | None:
        present = [item for item in values if item is not None]
        if not present:
            return None
        return sum(
            float(getattr(item, attribute)) for item in present
        ) / len(present)

    def _append(self, record: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as destination:
            destination.write(
                json.dumps(
                    record,
                    separators=(",", ":"),
                    sort_keys=True,
                )
                + "\n"
            )
            destination.flush()