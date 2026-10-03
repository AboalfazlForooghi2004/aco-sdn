from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass

from aco.models import LinkMetrics
from controller.flow_demand import FlowDemandEstimator
from controller.rerouting import (
    ActiveFlow,
    MigrationPlan,
    RerouteManager,
)
from controller.simulation import WhatIfResult, WhatIfSimulator
from controller.state import MigrationProposal
from controller.telemetry import TelemetryCollector
from controller.topology import TopologyManager


@dataclass(frozen=True, slots=True)
class EvaluatedMigration:
    flow: ActiveFlow
    migration: MigrationPlan
    simulation: WhatIfResult
    proposal: MigrationProposal


class RerouteEvaluationService:
    """Build safe, demand-aware migration candidates without applying them."""

    def __init__(
        self,
        *,
        reroute_manager: RerouteManager,
        simulator: WhatIfSimulator,
        flow_demand: FlowDemandEstimator,
        telemetry: TelemetryCollector,
        default_link_capacity_bps: float,
    ) -> None:
        self.reroute_manager = reroute_manager
        self.simulator = simulator
        self.flow_demand = flow_demand
        self.telemetry = telemetry
        self.default_link_capacity_bps = (
            default_link_capacity_bps
        )

    def evaluate(
        self,
        *,
        flows: Iterable[ActiveFlow],
        topology: TopologyManager,
        metrics: dict[tuple[int, int], LinkMetrics],
        now: float,
        is_pending: Callable[[ActiveFlow], bool],
    ) -> tuple[EvaluatedMigration, ...]:
        capacities = self.telemetry.link_capacities(topology)
        candidates = []
        for flow in flows:
            if is_pending(flow):
                continue
            migration = self.reroute_manager.evaluate(
                flow, topology, metrics, now
            )
            if migration is None:
                continue
            demand = self.flow_demand.get_key(flow.key, now)
            simulation = self.simulator.compare(
                topology=topology,
                metrics=metrics,
                current_path=flow.path,
                proposed_path=migration.decision.path,
                flow_demand_bps=(
                    demand.bits_per_second
                    if demand is not None
                    else None
                ),
                link_capacity_bps=(
                    self.default_link_capacity_bps
                ),
                link_capacities_bps=capacities,
            )
            candidates.append(
                EvaluatedMigration(
                    flow=flow,
                    migration=migration,
                    simulation=simulation,
                    proposal=MigrationProposal.from_plan(
                        migration,
                        now,
                        simulation=simulation.to_dict(),
                    ),
                )
            )
        return tuple(candidates)
