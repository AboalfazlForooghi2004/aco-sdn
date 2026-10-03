from __future__ import annotations

from typing import Iterable


class OpenFlowProtocol:
    """Build common OpenFlow 1.3 requests without owning Ryu events."""

    @staticmethod
    def install_table_miss(datapath) -> None:
        parser = datapath.ofproto_parser
        ofproto = datapath.ofproto
        actions = [
            parser.OFPActionOutput(
                ofproto.OFPP_CONTROLLER,
                ofproto.OFPCML_NO_BUFFER,
            )
        ]
        datapath.send_msg(
            parser.OFPFlowMod(
                datapath=datapath,
                priority=0,
                match=parser.OFPMatch(),
                instructions=[
                    parser.OFPInstructionActions(
                        ofproto.OFPIT_APPLY_ACTIONS,
                        actions,
                    )
                ],
            )
        )

    @staticmethod
    def request_port_stats(datapath) -> None:
        datapath.send_msg(
            datapath.ofproto_parser.OFPPortStatsRequest(
                datapath,
                0,
                datapath.ofproto.OFPP_ANY,
            )
        )

    @staticmethod
    def request_port_desc(datapath) -> None:
        datapath.send_msg(
            datapath.ofproto_parser.OFPPortDescStatsRequest(
                datapath, 0
            )
        )

    @staticmethod
    def request_flow_stats(datapath) -> None:
        datapath.send_msg(
            datapath.ofproto_parser.OFPFlowStatsRequest(
                datapath
            )
        )

    @staticmethod
    def request_echo(datapath, payload: bytes) -> None:
        datapath.send_msg(
            datapath.ofproto_parser.OFPEchoRequest(
                datapath, data=payload
            )
        )

    @staticmethod
    def send_packet_out(
        msg, in_port: int, out_port: int
    ) -> None:
        datapath = msg.datapath
        data = (
            msg.data
            if msg.buffer_id
            == datapath.ofproto.OFP_NO_BUFFER
            else None
        )
        datapath.send_msg(
            datapath.ofproto_parser.OFPPacketOut(
                datapath=datapath,
                buffer_id=msg.buffer_id,
                in_port=in_port,
                actions=[
                    datapath.ofproto_parser.OFPActionOutput(
                        out_port
                    )
                ],
                data=data,
            )
        )

    @staticmethod
    def extract_match(
        match,
        fields: Iterable[str] = (
            "eth_src",
            "eth_dst",
            "eth_type",
            "ipv4_src",
            "ipv4_dst",
            "ip_proto",
            "tcp_src",
            "tcp_dst",
            "udp_src",
            "udp_dst",
        ),
    ) -> dict[str, object]:
        return {
            name: match.get(name)
            for name in fields
            if match.get(name) is not None
        }