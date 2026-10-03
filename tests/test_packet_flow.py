import unittest

from controller.flow_identity import FlowSelector
from controller.flow_manager import FlowManager
from controller.packet_flow import PacketFlowService
from controller.rerouting import FlowRegistry
from controller.routing import RoutingDecision
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
    def OFPInstructionActions(kind, actions):
        return {"type": kind, "actions": actions}

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


class PacketFlowServiceTests(unittest.TestCase):
    def test_initial_install_registers_generation_and_rules(
        self,
    ) -> None:
        topology = TopologyManager()
        topology.add_link(1, 2, 12, 21)
        topology.add_link(2, 1, 21, 12)
        registry = FlowRegistry()
        service = PacketFlowService(
            FlowManager(), registry
        )
        selector = FlowSelector(
            source_mac="00:00:00:00:00:01",
            destination_mac="00:00:00:00:00:02",
            eth_type=0x0800,
            ipv4_source="10.0.0.1",
            ipv4_destination="10.0.0.2",
            ip_protocol=6,
            source_port=10000,
            destination_port=443,
        )

        result = service.install_initial(
            topology=topology,
            datapaths={
                1: FakeDatapath(1),
                2: FakeDatapath(2),
            },
            decision=RoutingDecision(
                path=(1, 2),
                cost=0.5,
                used_fallback=False,
                topology_generation=topology.generation,
            ),
            selector=selector,
            source_dpid=1,
            destination_dpid=2,
            source_host_port=1,
            destination_host_port=9,
            installed_at=10,
        )

        self.assertEqual(result.first_output_port, 12)
        self.assertEqual(result.flow.route_generation, 1)
        self.assertEqual(result.flow.expected_rule_count, 4)
        self.assertEqual(registry.flows, (result.flow,))


if __name__ == "__main__":
    unittest.main()