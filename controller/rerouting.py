from __future__ import annotations

import math
from dataclasses import dataclass

from aco.cost import path_cost
from aco.models import LinkMetrics
from controller.flow_identity import FlowSelector
from controller.routing import RoutingDecision, RoutingService
from controller.topology import TopologyManager


@dataclass(frozen=True, slots=True)
class ReroutePolicy:
    utilization_threshold: float
    utilization_hysteresis: float
    loss_threshold: float
    loss_hysteresis: float
    minimum_improvement: float
    cooldown_seconds: float

    def __post_init__(self) -> None:
        for name, value in (
            ("utilization_threshold", self.utilization_threshold),
            ("utilization_hysteresis", self.utilization_hysteresis),
            ("loss_threshold", self.loss_threshold),
            ("loss_hysteresis", self.loss_hysteresis),
            ("minimum_improvement", self.minimum_improvement),
        ):
            if not 0.0 <= value <= 1.0:
                raise ValueError(f"{name} must be between 0 and 1")
        if self.cooldown_seconds < 0:
            raise ValueError("cooldown_seconds cannot be negative")
        if (
            self.utilization_hysteresis
            > self.utilization_threshold
        ):
            raise ValueError(
                "utilization_hysteresis cannot exceed threshold"
            )
        if self.loss_hysteresis > self.loss_threshold:
            raise ValueError(
                "loss_hysteresis cannot exceed threshold"
            )


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
    selector: FlowSelector | None = None
    route_generation: int = 1
    expected_rule_count: int = 0

    @property
    def key(self) -> tuple:
        if self.selector is not None:
            return self.selector.key
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
    """Track bidirectional L2 or normalized five-tuple flows."""

    def __init__(self) -> None:
        self._flows: dict[tuple, ActiveFlow] = {}
        self._removed_rules: dict[
            tuple[tuple, int], set[tuple]
        ] = {}

    def next_route_generation(self, key: tuple) -> int:
        current = self._flows.get(key)
        if current is None:
            return 1
        return current.route_generation + 1

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
        route_generation: int | None = None,
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
            selector=flow.selector,
            route_generation=(
                flow.route_generation + 1
                if route_generation is None
                else route_generation
            ),
            expected_rule_count=2 * len(decision.path),
        )
        self._flows[updated.key] = updated
        self._removed_rules.pop(
            (flow.key, flow.route_generation), None
        )
        return updated

    def remove(self, flow: ActiveFlow) -> None:
        self._flows.pop(flow.key, None)
        self._removed_rules.pop(
            (flow.key, flow.route_generation), None
        )

    def remove_by_macs(
        self, source_mac: str, destination_mac: str
    ) -> ActiveFlow | None:
        pair = {
            source_mac.lower(),
            destination_mac.lower(),
        }
        for key, flow in tuple(self._flows.items()):
            if {
                flow.source_mac.lower(),
                flow.destination_mac.lower(),
            } == pair:
                return self._flows.pop(key)
        return None

    def remove_by_match(
        self, match: dict[str, object]
    ) -> ActiveFlow | None:
        selector = FlowSelector.from_match(match)
        if selector is not None:
            removed = self._flows.pop(selector.key, None)
            if removed is not None:
                return removed
        source = match.get("eth_src")
        destination = match.get("eth_dst")
        if source is None or destination is None:
            return None
        return self.remove_by_macs(str(source), str(destination))

    def mark_rule_removed(
        self,
        *,
        match: dict[str, object],
        cookie: int,
        dpid: int,
        route_generation: int | None,
    ) -> ActiveFlow | None:
        selector = FlowSelector.from_match(match)
        flow = (
            self._flows.get(selector.key)
            if selector is not None
            else None
        )
        if flow is None:
            return None
        if (
            route_generation is not None
            and route_generation != flow.route_generation
        ):
            return None
        token = (
            dpid,
            tuple(
                sorted(
                    (name, str(value))
                    for name, value in match.items()
                )
            ),
            cookie,
        )
        key = (flow.key, flow.route_generation)
        removed = self._removed_rules.setdefault(key, set())
        removed.add(token)
        expected = max(flow.expected_rule_count, 1)
        if len(removed) < expected:
            return None
        self.remove(flow)
        return flow

    def flows_for_host(self, mac: str) -> tuple[ActiveFlow, ...]:
        normalized = mac.lower()
        return tuple(
            flow
            for flow in self._flows.values()
            if normalized
            in {
                flow.source_mac.lower(),
                flow.destination_mac.lower(),
            }
        )

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
        self._congested_edges: set[tuple[int, int]] = set()

    @property
    def congested_edges(self) -> frozenset[tuple[int, int]]:
        return frozenset(self._congested_edges)

    def update_congestion_state(
        self,
        metrics: dict[tuple[int, int], LinkMetrics],
    ) -> None:
        for edge, metric in metrics.items():
            if not metric.available:
                self._congested_edges.discard(edge)
                continue
            if edge in self._congested_edges:
                utilization_clear = (
                    metric.utilization
                    < self.policy.utilization_threshold
                    - self.policy.utilization_hysteresis
                )
                loss_clear = (
                    metric.loss
                    < self.policy.loss_threshold
                    - self.policy.loss_hysteresis
                )
                if utilization_clear and loss_clear:
                    self._congested_edges.discard(edge)
            elif (
                metric.utilization
                >= self.policy.utilization_threshold
                or metric.loss >= self.policy.loss_threshold
            ):
                self._congested_edges.add(edge)

    def evaluate(
        self,
        flow: ActiveFlow,
        topology: TopologyManager,
        metrics: dict[tuple[int, int], LinkMetrics],
        now: float,
    ) -> MigrationPlan | None:
        self.update_congestion_state(metrics)
        path_edges = tuple(zip(flow.path, flow.path[1:]))
        edge_metrics = [metrics.get(edge) for edge in path_edges]
        forced = any(
            metric is None or not metric.available
            for metric in edge_metrics
        )
        threshold_crossed = any(
            edge in self._congested_edges for edge in path_edges
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