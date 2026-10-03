import unittest

from controller.openflow_protocol import OpenFlowProtocol


class FakeOfproto:
    OFPP_CONTROLLER = 0xFFFFFFFD
    OFPCML_NO_BUFFER = 0xFFFF
    OFPIT_APPLY_ACTIONS = 4
    OFPP_ANY = 0xFFFFFFFF
    OFP_NO_BUFFER = 0xFFFFFFFF


class FakeParser:
    def __getattr__(self, name):
        def build(*args, **kwargs):
            return {
                "kind": name,
                "args": args,
                **kwargs,
            }

        return build


class FakeDatapath:
    def __init__(self) -> None:
        self.ofproto = FakeOfproto()
        self.ofproto_parser = FakeParser()
        self.messages = []

    def send_msg(self, message) -> None:
        self.messages.append(message)


class FakeMessage:
    def __init__(self, datapath) -> None:
        self.datapath = datapath
        self.buffer_id = datapath.ofproto.OFP_NO_BUFFER
        self.data = b"frame"


class OpenFlowProtocolTests(unittest.TestCase):
    def test_common_requests_are_built(self) -> None:
        datapath = FakeDatapath()

        OpenFlowProtocol.install_table_miss(datapath)
        OpenFlowProtocol.request_port_stats(datapath)
        OpenFlowProtocol.request_port_desc(datapath)
        OpenFlowProtocol.request_flow_stats(datapath)
        OpenFlowProtocol.request_echo(datapath, b"echo")

        self.assertEqual(len(datapath.messages), 5)
        self.assertEqual(
            datapath.messages[0]["kind"], "OFPFlowMod"
        )
        self.assertEqual(
            datapath.messages[-1]["data"], b"echo"
        )

    def test_packet_out_carries_unbuffered_data(self) -> None:
        datapath = FakeDatapath()

        OpenFlowProtocol.send_packet_out(
            FakeMessage(datapath), 1, 2
        )

        self.assertEqual(
            datapath.messages[-1]["data"], b"frame"
        )

    def test_extract_match_keeps_supported_fields(self) -> None:
        result = OpenFlowProtocol.extract_match(
            {
                "eth_src": "aa",
                "eth_dst": "bb",
                "tcp_src": 1000,
                "metadata": 7,
            }
        )

        self.assertEqual(
            result,
            {
                "eth_src": "aa",
                "eth_dst": "bb",
                "tcp_src": 1000,
            },
        )


if __name__ == "__main__":
    unittest.main()