import unittest
from dataclasses import dataclass

from controller.change_service import RouteChangeService
from controller.flow_identity import FlowSelector
from controller.flow_manager import FlowManager
from controller.rerouting import (
    ActiveFlow,
    FlowRegistry,
    MigrationPlan,
)
from controller.routing import RoutingDecision
from controller.topology import TopologyManager
from controller.transactions import TransactionResult


@dataclass(frozen=True)
class Proposal:
    proposal_id: str


class FakeTransactions:
    def __init__(self) -> None:
        self.started = None
        self.result = None

    def begin(self, **kwargs) -> None:
        self.started = kwargs

    def acknowledge(self, **kwargs):
        return self.result

    def expire(self, **kwargs):
        return () if self.result is None else (self.result,)


class ChangeServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.topology = TopologyManager()
        for source, target, source_port, target_port in (
            (1, 2, 12, 21),
            (2, 4, 24, 42),
            (1, 3, 13, 31),
            (3, 4, 34, 43),
        ):
            self.topology.add_link(
                source, target, source_port, target_port
            )
            self.topology.add_link(
                target, source, target_port, source_port
            )
        selector = FlowSelector(
            source_mac="00:00:00:00:00:01",
            destination_mac="00:00:00:00:00:02",
        )
        self.flow = ActiveFlow(
            source_mac=selector.source_mac,
            destination_mac=selector.destination_mac,
            source_dpid=1,
            destination_dpid=4,
            source_host_port=1,
            destination_host_port=9,
            path=(1, 2, 4),
            installed_cost=1.0,
            last_reroute_at=0,
            selector=selector,
            route_generation=3,
            expected_rule_count=6,
        )
        self.registry = FlowRegistry()
        self.registry.register_initial(self.flow)
        self.transactions = FakeTransactions()
        self.service = RouteChangeService(
            FlowManager(),
            self.transactions,
            self.registry,
        )
        self.migration = MigrationPlan(
            flow=self.flow,
            decision=RoutingDecision(
                path=(1, 3, 4),
                cost=0.5,
                used_fallback=False,
                topology_generation=self.topology.generation,
            ),
            current_cost=1.0,
            forced=False,
        )

    def test_commit_updates_registry_only_after_ack(self) -> None:
        transaction_id = self.service.start(
            flow=self.flow,
            migration=self.migration,
            proposal=Proposal("p1"),
            topology=self.topology,
            datapaths={},
            now=10,
        )
        self.assertTrue(self.service.is_pending(self.flow))
        self.assertEqual(
            self.registry.flows[0].path, (1, 2, 4)
        )
        self.transactions.result = TransactionResult(
            transaction_id,
            "committed",
            "barriers_acknowledged",
        )

        outcome = self.service.acknowledge(
            dpid=1,
            xid=1,
            topology=self.topology,
            datapaths={},
            now=11,
        )

        self.assertIsNotNone(outcome)
        self.assertEqual(outcome.updated_flow.path, (1, 3, 4))
        self.assertEqual(outcome.updated_flow.route_generation, 4)
        self.assertFalse(self.service.is_pending(self.flow))

    def test_rollback_keeps_old_registry_path(self) -> None:
        transaction_id = self.service.start(
            flow=self.flow,
            migration=self.migration,
            proposal=Proposal("p1"),
            topology=self.topology,
            datapaths={},
            now=10,
        )
        self.transactions.result = TransactionResult(
            transaction_id,
            "rolled_back",
            "barrier_timeout",
        )

        outcome = self.service.acknowledge(
            dpid=1,
            xid=1,
            topology=self.topology,
            datapaths={},
            now=11,
        )

        self.assertIsNone(outcome.updated_flow)
        self.assertEqual(
            self.registry.flows[0].path, (1, 2, 4)
        )


if __name__ == "__main__":
    unittest.main()