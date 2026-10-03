from __future__ import annotations

from dataclasses import dataclass

from controller.flow_identity import FlowSelector
from controller.flow_manager import (
    FlowManager,
    PlannedRule,
    build_bidirectional_plan,
)
from controller.rerouting import ActiveFlow, FlowRegistry
from controller.routing import RoutingDecision
from controller.topology import TopologyManager


@dataclass(frozen=True, slots=True)
class InitialFlowInstall:
    flow: ActiveFlow
    rules: tuple[PlannedRule, ...]
    first_output_port: int


@dataclass(frozen=True, slots=True)
class HostCleanup:
    removed: tuple[ActiveFlow, ...]
    errors: tuple[str, ...]


class PacketFlowService:
    """Own initial installation, host cleanup, and expiry accounting."""

    def __init__(
        self,
        flow_manager: FlowManager,
        registry: FlowRegistry,
    ) -> None:
        self.flow_manager = flow_manager
        self.registry = registry

    def install_initial(
        self,
        *,
        topology: TopologyManager,
        datapaths: dict[int, object],
        decision: RoutingDecision,
        selector: FlowSelector,
        source_dpid: int,
        destination_dpid: int,
        source_host_port: int,
        destination_host_port: int,
        installed_at: float,
    ) -> InitialFlowInstall:
        route_generation = (
            self.registry.next_route_generation(selector.key)
        )
        rules = build_bidirectional_plan(
            topology=topology,
            path=decision.path,
            source_mac=selector.source_mac,
            destination_mac=selector.destination_mac,
            source_host_port=source_host_port,
            destination_host_port=destination_host_port,
            selector=selector,
            cookie=self.flow_manager.cookie_for(
                selector.key, route_generation
            ),
        )
        self.flow_manager.install(datapaths, rules)
        flow = self.registry.register_initial(
            ActiveFlow(
                source_mac=selector.source_mac,
                destination_mac=selector.destination_mac,
                source_dpid=source_dpid,
                destination_dpid=destination_dpid,
                source_host_port=source_host_port,
                destination_host_port=destination_host_port,
                path=decision.path,
                installed_cost=decision.cost,
                last_reroute_at=installed_at,
                selector=selector,
                route_generation=route_generation,
                expected_rule_count=len(rules),
            )
        )
        first = next(
            rule
            for rule in rules
            if rule.dpid == source_dpid
            and rule.selector == selector
        )
        return InitialFlowInstall(
            flow=flow,
            rules=rules,
            first_output_port=first.output_port,
        )

    def cleanup_host(
        self,
        *,
        topology: TopologyManager,
        datapaths: dict[int, object],
        mac: str,
    ) -> HostCleanup:
        removed = []
        errors = []
        for flow in self.registry.flows_for_host(mac):
            try:
                rules = build_bidirectional_plan(
                    topology=topology,
                    path=flow.path,
                    source_mac=flow.source_mac,
                    destination_mac=flow.destination_mac,
                    source_host_port=flow.source_host_port,
                    destination_host_port=(
                        flow.destination_host_port
                    ),
                    selector=flow.selector,
                    cookie=self.flow_manager.cookie_for(
                        flow.key, flow.route_generation
                    ),
                )
                self.flow_manager.delete(datapaths, rules)
            except (KeyError, ValueError) as exc:
                errors.append(str(exc))
            self.registry.remove(flow)
            removed.append(flow)
        return HostCleanup(
            removed=tuple(removed),
            errors=tuple(errors),
        )

    def rule_removed(
        self,
        *,
        match: dict[str, object],
        cookie: int,
        dpid: int,
    ) -> ActiveFlow | None:
        decoded = self.flow_manager.decode_cookie(cookie)
        return self.registry.mark_rule_removed(
            match=match,
            cookie=cookie,
            dpid=dpid,
            route_generation=(
                decoded[0] if decoded is not None else None
            ),
        )