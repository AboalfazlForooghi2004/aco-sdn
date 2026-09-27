from __future__ import annotations

import math
from dataclasses import asdict, dataclass

from aco.cost import CostWeights, path_cost
from aco.models import LinkMetrics
from controller.topology import TopologyManager


@dataclass(frozen=True, slots=True)
class PathAssessment:
    path: tuple[int, ...]
    valid: bool
    available: bool
    hop_count: int
    cost: float | None
    latency_ms: float | None
    average_utilization: float | None
    maximum_utilization: float | None
    packet_loss: float | None
    violations: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class WhatIfResult:
    safe_to_apply: bool
    current: PathAssessment
    proposed: PathAssessment
    cost_improvement_ratio: float | None
    latency_change_ms: float | None
    packet_loss_change: float | None
    maximum_utilization_change: float | None
    warnings: tuple[str, ...]

    def to_dict(self) -> dict:
        return asdict(self)


class WhatIfSimulator:
    """Compare current and proposed paths without mutating the network."""

    def __init__(self, weights: CostWeights) -> None:
        self.weights = weights

    def compare(
        self,
        topology: TopologyManager,
        metrics: dict[tuple[int, int], LinkMetrics],
        current_path: tuple[int, ...],
        proposed_path: tuple[int, ...],
    ) -> WhatIfResult:
        current = self._assess(
            topology, metrics, current_path
        )
        proposed = self._assess(
            topology, metrics, proposed_path
        )
        endpoint_mismatch = (
            bool(current_path)
            and bool(proposed_path)
            and (
                current_path[0] != proposed_path[0]
                or current_path[-1] != proposed_path[-1]
            )
        )
        warnings = [
            "flow_bandwidth_not_modeled",
            "metrics_are_point_in_time_observations",
        ]
        if endpoint_mismatch:
            warnings.append("path_endpoints_do_not_match")
        safe = (
            proposed.valid
            and proposed.available
            and not endpoint_mismatch
        )
        return WhatIfResult(
            safe_to_apply=safe,
            current=current,
            proposed=proposed,
            cost_improvement_ratio=self._improvement(
                current.cost, proposed.cost
            ),
            latency_change_ms=self._difference(
                proposed.latency_ms, current.latency_ms
            ),
            packet_loss_change=self._difference(
                proposed.packet_loss, current.packet_loss
            ),
            maximum_utilization_change=self._difference(
                proposed.maximum_utilization,
                current.maximum_utilization,
            ),
            warnings=tuple(warnings),
        )

    def _assess(
        self,
        topology: TopologyManager,
        metrics: dict[tuple[int, int], LinkMetrics],
        path: tuple[int, ...],
    ) -> PathAssessment:
        violations: list[str] = []
        if not path:
            violations.append("empty_path")
        if len(set(path)) != len(path):
            violations.append("loop_detected")
        missing_nodes = [
            node
            for node in path
            if node not in topology.switches
        ]
        if missing_nodes:
            violations.append("unknown_switch")

        edges = tuple(zip(path, path[1:]))
        missing_edges = [
            edge for edge in edges if edge not in topology.links
        ]
        if missing_edges:
            violations.append("missing_link")
        edge_metrics = [
            metrics.get(edge)
            for edge in edges
            if edge in topology.links
        ]
        missing_metrics = any(
            value is None for value in edge_metrics
        )
        if missing_metrics:
            violations.append("missing_metrics")
        available = (
            not missing_edges
            and not missing_metrics
            and all(
                value is not None and value.available
                for value in edge_metrics
            )
        )
        if not available and edges:
            violations.append("unavailable_link")

        valid = not any(
            item
            in {
                "empty_path",
                "loop_detected",
                "unknown_switch",
                "missing_link",
            }
            for item in violations
        )
        usable = [
            value
            for value in edge_metrics
            if value is not None
        ]
        if not valid or len(usable) != len(edges):
            return PathAssessment(
                path=path,
                valid=valid,
                available=available,
                hop_count=max(len(path) - 1, 0),
                cost=None,
                latency_ms=None,
                average_utilization=None,
                maximum_utilization=None,
                packet_loss=None,
                violations=tuple(dict.fromkeys(violations)),
            )

        graph = topology.build_graph(metrics)
        calculated_cost = path_cost(
            graph,
            [str(node) for node in path],
            self.weights,
        )
        finite_cost = (
            calculated_cost
            if math.isfinite(calculated_cost)
            else None
        )
        latency = sum(item.latency_ms for item in usable)
        average_utilization = (
            sum(item.utilization for item in usable)
            / len(usable)
            if usable
            else 0.0
        )
        maximum_utilization = max(
            (item.utilization for item in usable),
            default=0.0,
        )
        delivery_probability = math.prod(
            1.0 - item.loss for item in usable
        )
        return PathAssessment(
            path=path,
            valid=valid,
            available=available,
            hop_count=max(len(path) - 1, 0),
            cost=finite_cost,
            latency_ms=latency,
            average_utilization=average_utilization,
            maximum_utilization=maximum_utilization,
            packet_loss=1.0 - delivery_probability,
            violations=tuple(dict.fromkeys(violations)),
        )

    @staticmethod
    def _difference(
        proposed: float | None,
        current: float | None,
    ) -> float | None:
        if proposed is None or current is None:
            return None
        return proposed - current

    @staticmethod
    def _improvement(
        current: float | None,
        proposed: float | None,
    ) -> float | None:
        if current is None or proposed is None:
            return None
        return (current - proposed) / max(current, 1e-9)