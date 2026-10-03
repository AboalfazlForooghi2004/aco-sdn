import unittest

from controller.latency import LatencyTracker
from controller.link_probe_service import LinkProbeService
from controller.topology import TopologyManager


class FakeOfproto:
    OFP_NO_BUFFER = 0xFFFFFFFF
    OFPP_CONTROLLER = 0xFFFFFFFD


class FakeParser:
    def __getattr__(self, name):
        def build(*args, **kwargs):
            return {"kind": name, "args": args, **kwargs}

        return build


class FakeDatapath:
    def __init__(self, dpid: int) -> None:
        self.id = dpid
        self.ofproto = FakeOfproto()
        self.ofproto_parser = FakeParser()
        self.messages = []

    def send_msg(self, message) -> None:
        self.messages.append(message)


class LinkProbeServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.topology = TopologyManager()
        self.topology.add_link(1, 2, 12, 21)
        self.latency = LatencyTracker(0.5, 10)
        self.service = LinkProbeService(self.latency)

    def test_send_uses_discovered_source_port(self) -> None:
        datapath = FakeDatapath(1)

        sent = self.service.send(
            topology=self.topology,
            datapaths={1: datapath},
            sent_at=10,
        )

        self.assertEqual(sent, 1)
        message = datapath.messages[0]
        self.assertEqual(message["kind"], "OFPPacketOut")
        self.assertEqual(message["actions"][0]["args"], (12,))

    def test_receive_validates_edge_and_records_latency(self) -> None:
        self.latency.record_echo(1, 9.998, 10)
        self.latency.record_echo(2, 9.998, 10)
        datapath = FakeDatapath(1)
        self.service.send(
            topology=self.topology,
            datapaths={1: datapath},
            sent_at=10,
        )

        result = self.service.receive(
            payload=datapath.messages[0]["data"],
            topology=self.topology,
            destination_dpid=2,
            destination_port=21,
            received_at=10.010,
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertTrue(result.accepted)
        self.assertIsNotNone(result.latency_ms)

    def test_non_probe_payload_is_not_consumed(self) -> None:
        result = self.service.receive(
            payload=b"ordinary-frame",
            topology=self.topology,
            destination_dpid=2,
            destination_port=21,
            received_at=10,
        )

        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
