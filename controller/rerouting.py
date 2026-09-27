from __future__ import annotations

import math
from dataclasses import dataclass

from aco.cost import path_cost
from aco.models import LinkMetrics
from controller.routing import RoutingDecision, RoutingService
from controller.topology import TopologyManager


@dataclass(frozen=True, slots=True)
class ReroutePolicy:
    utilization_threshold: float
    loss_threshold: float
    minimum_improvement: float
    cooldown_seconds: float

    def __post_init__(self) -> None:
        for name, value in (
            ("utilization_threshold", self.utilization_threshold),
            ("loss_threshold", self.loss_threshold),
            ("minimum_improvement", self.minimum_improvement),
        ):
            if not 0.0 <= value <= 1.0:
                raise ValueError(f"{name} must be between 0 and 1")
        if self.cooldown_seconds < 0:
            raise ValueError("cooldown_seconds cannot be negative")


@dataclass(frozen=True, slots=True)
class ActiveFlow:
    source_mac: str
    destination_mac: str
    source_dpid: int
    destination_dpid: int
    source_host_port: int
    destination_host_port: int
    path: tuple[int, ...]
    installed_cost: float
    last_reroute_at: float

    @property
    def key(self) -> tuple[str, str]:
        return tuple(
            sorted(
                (
                    self.source_mac.lower(),
                    self.destination_mac.lower(),
                )
            )
        )


@dataclass(frozen=True, slots=True)
class MigrationPlan:
    flow: ActiveFlow
    decision: RoutingDecision
    current_cost: float
    forced: bool


class FlowRegistry:
    """Track each bidirectional MAC pair as one active flow."""

    def __init__(self) -> None:
        self._flows: dict[tuple[str, str], ActiveFlow] = {}

    def register_initial(self, flow: ActiveFlow) -> ActiveFlow:
        existing = self._flows.get(flow.key)
        if existing is not None:
            return existing
        self._flows[flow.key] = flow
        return flow

    def replace(
        self,
        flow: ActiveFlow,
        decision: RoutingDecision,
        changed_at: float,
    ) -> ActiveFlow:
        updated = ActiveFlow(
            source_mac=flow.source_mac,
            destination_mac=flow.destination_mac,
            source_dpid=flow.source_dpid,
            destination_dpid=flow.destination_dpid,
            source_host_port=flow.source_host_port,
            destination_host_port=flow.destination_host_port,
            path=decision.path,
            installed_cost=decision.cost,
            last_reroute_at=changed_at,
        )
        self._flows[updated.key] = updated
        return updated

    def remove(self, flow: ActiveFlow) -> None:
        self._flows.pop(flow.key, None)

    @property
    def flows(self) -> tuple[ActiveFlow, ...]:
        return tuple(self._flows.values())


class RerouteManager:
    """Evaluate active paths and propose stable route migrations."""

    def __init__(
        self,
        routing: RoutingService,
        policy: ReroutePolicy,
    ) -> None:
        self.routing = routing
        self.policy = policy

    def evaluate(
        self,
        flow: ActiveFlow,
        topology: TopologyManager,
        metrics: dict[tuple[int, int], LinkMetrics],
        now: float,
    ) -> MigrationPlan | None:
        path_edges = tuple(zip(flow.path, flow.path[1:]))
        edge_metrics = [metrics.get(edge) for edge in path_edges]
        forced = any(
            metric is None or not metric.available
            for metric in edge_metrics
        )
        threshold_crossed = any(
            metric is not None
            and metric.available
            and (
                metric.utilization
                >= self.policy.utilization_threshold
                or metric.loss >= self.policy.loss_threshold
            )
            for metric in edge_metrics
        )
        if not forced and not threshold_crossed:
            return None
        if (
            not forced
            and now - flow.last_reroute_at
            < self.policy.cooldown_seconds
        ):
            return None

        graph = topology.build_graph(metrics)
        current_path = [str(node) for node in flow.path]
        current_cost = path_cost(
            graph,
            current_path,
            self.routing.optimizer.weights,
        )
        try:
            decision = self.routing.select_path(
                topology,
                metrics,
                flow.source_dpid,
                flow.destination_dpid,
                allow_topology_fallback=False,
            )
        except ValueError:
            return None
        if decision.path == flow.path:
            return None

        improvement = (
            1.0
            if not math.isfinite(current_cost)
            else (current_cost - decision.cost)
            / max(current_cost, 1e-9)
        )
        if (
            not forced
            and improvement < self.policy.minimum_improvement
        ):
            return None
        return MigrationPlan(
            flow=flow,
            decision=decision,
            current_cost=current_cost,
            forced=forced,
        )