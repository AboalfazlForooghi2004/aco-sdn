import unittest

from aco.models import LinkMetrics
from aco.optimizer import ACOConfig, AntColonyOptimizer
from controller.flow_manager import (
    FlowManager,
    build_bidirectional_plan,
)
from controller.rerouting import (
    ActiveFlow,
    RerouteManager,
    ReroutePolicy,
)
from controller.routing import RoutingService
from controller.topology import TopologyManager


class FakeOfproto:
    OFPIT_APPLY_ACTIONS = 4
    OFPFF_SEND_FLOW_REM = 1
    OFPFC_DELETE_STRICT = 4
    OFPP_ANY = 0xFFFFFFFF
    OFPG_ANY = 0xFFFFFFFF


class FakeParser:
    @staticmethod
    def OFPMatch(**kwargs):
        return {"kind": "match", **kwargs}

    @staticmethod
    def OFPActionOutput(port):
        return {"kind": "output", "port": port}

    @staticmethod
    def OFPInstructionActions(instruction_type, actions):
        return {
            "kind": "instructions",
            "type": instruction_type,
            "actions": actions,
        }

    @staticmethod
    def OFPFlowMod(**kwargs):
        return {"kind": "flow_mod", **kwargs}


class FakeDatapath:
    def __init__(self, dpid: int) -> None:
        self.id = dpid
        self.ofproto = FakeOfproto()
        self.ofproto_parser = FakeParser()
        self.messages = []

    def send_msg(self, message) -> None:
        self.messages.append(message)


class EndToEndSimulationTests(unittest.TestCase):
    def build_topology(self) -> TopologyManager:
        topology = TopologyManager()
        links = (
            (1, 2, 12, 21),
            (2, 4, 24, 42),
            (4, 6, 46, 64),
            (1, 3, 13, 31),
            (3, 5, 35, 53),
            (5, 6, 56, 65),
        )
        for source, target, source_port, target_port in links:
            topology.add_link(
                source, target, source_port, target_port
            )
            topology.add_link(
                target, source, target_port, source_port
            )
        return topology

    def test_congestion_causes_make_before_break_messages(self) -> None:
        topology = self.build_topology()
        router = RoutingService(
            AntColonyOptimizer(
                ACOConfig(ants=30, iterations=25, seed=42)
            )
        )
        manager = RerouteManager(
            router,
            ReroutePolicy(
                utilization_threshold=0.8,
                utilization_hysteresis=0.1,
                loss_threshold=0.05,
                loss_hysteresis=0.01,
                minimum_improvement=0.1,
                cooldown_seconds=10,
            ),
        )
        healthy = LinkMetrics(
            latency_ms=4,
            utilization=0.15,
            loss=0.001,
        )
        congested = LinkMetrics(
            latency_ms=20,
            utilization=0.95,
            loss=0.08,
        )
        metrics = {
            edge: (
                congested
                if edge in {(1, 2), (2, 4), (4, 6)}
                else healthy
            )
            for edge in topology.links
        }
        flow = ActiveFlow(
            source_mac="00:00:00:00:00:01",
            destination_mac="00:00:00:00:00:02",
            source_dpid=1,
            destination_dpid=6,
            source_host_port=1,
            destination_host_port=9,
            path=(1, 2, 4, 6),
            installed_cost=1.0,
            last_reroute_at=0,
        )
        migration = manager.evaluate(
            flow, topology, metrics, now=20
        )
        self.assertIsNotNone(migration)
        assert migration is not None
        self.assertEqual(
            migration.decision.path, (1, 3, 5, 6)
        )

        old_rules = build_bidirectional_plan(
            topology,
            flow.path,
            flow.source_mac,
            flow.destination_mac,
            flow.source_host_port,
            flow.destination_host_port,
        )
        new_rules = build_bidirectional_plan(
            topology,
            migration.decision.path,
            flow.source_mac,
            flow.destination_mac,
            flow.source_host_port,
            flow.destination_host_port,
        )
        datapaths = {
            dpid: FakeDatapath(dpid)
            for dpid in topology.switches
        }
        flow_manager = FlowManager()
        flow_manager.install(datapaths, new_rules)
        flow_manager.delete(
            datapaths,
            old_rules,
            exclude_dpids=frozenset(
                migration.decision.path
            ),
        )

        install_messages = sum(
            1
            for datapath in datapaths.values()
            for message in datapath.messages
            if "command" not in message
        )
        delete_messages = sum(
            1
            for datapath in datapaths.values()
            for message in datapath.messages
            if message.get("command")
            == FakeOfproto.OFPFC_DELETE_STRICT
        )
        self.assertEqual(install_messages, 8)
        self.assertEqual(delete_messages, 4)
        self.assertNotIn(
            "command", datapaths[1].messages[-1]
        )
        self.assertNotIn(
            "command", datapaths[6].messages[-1]
        )


if __name__ == "__main__":
    unittest.main()