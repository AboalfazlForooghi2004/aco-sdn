from __future__ import annotations

import math
from dataclasses import asdict, dataclass, replace

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

    def __init__(
        self,
        weights: CostWeights,
        utilization_safety_limit: float = 0.95,
    ) -> None:
        if not 0.0 < utilization_safety_limit <= 1.0:
            raise ValueError(
                "utilization_safety_limit must be in (0, 1]"
            )
        self.weights = weights
        self.utilization_safety_limit = (
            utilization_safety_limit
        )

    def compare(
        self,
        topology: TopologyManager,
        metrics: dict[tuple[int, int], LinkMetrics],
        current_path: tuple[int, ...],
        proposed_path: tuple[int, ...],
        flow_demand_bps: float | None = None,
        link_capacity_bps: float | None = None,
        link_capacities_bps: (
            dict[tuple[int, int], float] | None
        ) = None,
    ) -> WhatIfResult:
        current = self._assess(
            topology, metrics, current_path
        )
        projected_metrics = dict(metrics)
        warnings = [
            "metrics_are_point_in_time_observations",
        ]
        if flow_demand_bps is None:
            warnings.append("flow_bandwidth_not_modeled")
        else:
            if (
                not link_capacities_bps
                and (
                    link_capacity_bps is None
                    or link_capacity_bps <= 0
                )
            ):
                raise ValueError(
                    "positive link_capacity_bps is required "
                    "when flow demand is provided"
                )
            current_edges = set(
                zip(current_path, current_path[1:])
            )
            for edge in zip(
                proposed_path, proposed_path[1:]
            ):
                if edge in current_edges:
                    continue
                value = projected_metrics.get(edge)
                if value is None:
                    continue
                capacity = (
                    link_capacities_bps.get(edge)
                    if link_capacities_bps
                    else link_capacity_bps
                )
                if capacity is None or capacity <= 0:
                    warnings.append(
                        f"missing_capacity:{edge[0]}->{edge[1]}"
                    )
                    continue
                demand_fraction = max(
                    flow_demand_bps, 0.0
                ) / capacity
                projected_metrics[edge] = replace(
                    value,
                    utilization=min(
                        1.0,
                        value.utilization + demand_fraction,
                    ),
                )
            warnings.append(
                "flow_demand_estimated_from_openflow_counters"
            )
            warnings.append(
                "per_link_capacity_used"
                if link_capacities_bps
                else "global_link_capacity_assumed"
            )
        proposed = self._assess(
            topology, projected_metrics, proposed_path
        )
        endpoint_mismatch = (
            bool(current_path)
            and bool(proposed_path)
            and (
                current_path[0] != proposed_path[0]
                or current_path[-1] != proposed_path[-1]
            )
        )
        if endpoint_mismatch:
            warnings.append("path_endpoints_do_not_match")
        exceeds_safety_limit = (
            proposed.maximum_utilization is not None
            and proposed.maximum_utilization
            > self.utilization_safety_limit
        )
        if exceeds_safety_limit:
            proposed = replace(
                proposed,
                violations=tuple(
                    dict.fromkeys(
                        (
                            *proposed.violations,
                            "projected_utilization_exceeds_"
                            "safety_limit",
                        )
                    )
                ),
            )
        safe = (
            proposed.valid
            and proposed.available
            and not endpoint_mismatch
            and not exceeds_safety_limit
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