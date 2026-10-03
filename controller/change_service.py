from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from controller.flow_manager import (
    FlowManager,
    build_bidirectional_plan,
)
from controller.rerouting import (
    ActiveFlow,
    FlowRegistry,
    MigrationPlan,
)
from controller.topology import TopologyManager
from controller.transactions import (
    RouteTransactionManager,
    TransactionResult,
)


@dataclass(frozen=True, slots=True)
class RouteChangeOutcome:
    result: TransactionResult
    flow: ActiveFlow
    migration: MigrationPlan
    proposal: Any
    updated_flow: ActiveFlow | None


@dataclass(frozen=True, slots=True)
class _ChangeContext:
    flow: ActiveFlow
    migration: MigrationPlan
    proposal: Any
    new_route_generation: int


class RouteChangeService:
    """Own route plans, transaction context, and registry commits."""

    def __init__(
        self,
        flow_manager: FlowManager,
        transactions: RouteTransactionManager,
        registry: FlowRegistry,
    ) -> None:
        self.flow_manager = flow_manager
        self.transactions = transactions
        self.registry = registry
        self._pending: dict[str, _ChangeContext] = {}
        self._by_flow: dict[tuple, str] = {}

    def is_pending(self, flow: ActiveFlow) -> bool:
        return flow.key in self._by_flow

    def start(
        self,
        *,
        flow: ActiveFlow,
        migration: MigrationPlan,
        proposal: Any,
        topology: TopologyManager,
        datapaths: dict[int, object],
        now: float,
    ) -> str:
        if self.is_pending(flow):
            raise ValueError(
                "a route transaction is already pending for flow"
            )
        new_generation = flow.route_generation + 1
        new_rules = build_bidirectional_plan(
            topology=topology,
            path=migration.decision.path,
            source_mac=flow.source_mac,
            destination_mac=flow.destination_mac,
            source_host_port=flow.source_host_port,
            destination_host_port=flow.destination_host_port,
            selector=flow.selector,
            cookie=self.flow_manager.cookie_for(
                flow.key, new_generation
            ),
        )
        old_rules = build_bidirectional_plan(
            topology=topology,
            path=flow.path,
            source_mac=flow.source_mac,
            destination_mac=flow.destination_mac,
            source_host_port=flow.source_host_port,
            destination_host_port=flow.destination_host_port,
            selector=flow.selector,
            cookie=self.flow_manager.cookie_for(
                flow.key, flow.route_generation
            ),
        )
        transaction_id = (
            f"{proposal.proposal_id}-{time.monotonic_ns()}"
        )
        self.transactions.begin(
            transaction_id=transaction_id,
            topology_generation=(
                migration.decision.topology_generation
            ),
            current_generation=topology.generation,
            datapaths=datapaths,
            old_rules=old_rules,
            new_rules=new_rules,
            now=now,
        )
        self._pending[transaction_id] = _ChangeContext(
            flow=flow,
            migration=migration,
            proposal=proposal,
            new_route_generation=new_generation,
        )
        self._by_flow[flow.key] = transaction_id
        return transaction_id

    def acknowledge(
        self,
        *,
        dpid: int,
        xid: int,
        topology: TopologyManager,
        datapaths: dict[int, object],
        now: float,
    ) -> RouteChangeOutcome | None:
        result = self.transactions.acknowledge(
            dpid=dpid,
            xid=xid,
            current_generation=topology.generation,
            datapaths=datapaths,
            now=now,
        )
        return (
            None
            if result is None
            else self._finalize(result, now)
        )

    def expire(
        self,
        *,
        topology: TopologyManager,
        datapaths: dict[int, object],
        now: float,
    ) -> tuple[RouteChangeOutcome, ...]:
        return tuple(
            outcome
            for result in self.transactions.expire(
                now=now,
                current_generation=topology.generation,
                datapaths=datapaths,
            )
            if (
                outcome := self._finalize(result, now)
            )
            is not None
        )

    def _finalize(
        self,
        result: TransactionResult,
        now: float,
    ) -> RouteChangeOutcome | None:
        context = self._pending.pop(
            result.transaction_id, None
        )
        if context is None:
            return None
        self._by_flow.pop(context.flow.key, None)
        updated = None
        if result.status == "committed":
            updated = self.registry.replace(
                context.flow,
                context.migration.decision,
                changed_at=now,
                route_generation=(
                    context.new_route_generation
                ),
            )
        return RouteChangeOutcome(
            result=result,
            flow=context.flow,
            migration=context.migration,
            proposal=context.proposal,
            updated_flow=updated,
        )