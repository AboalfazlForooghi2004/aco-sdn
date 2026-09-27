from __future__ import annotations

from ryu.base import app_manager
from ryu.controller import ofp_event
from ryu.controller.handler import (
    CONFIG_DISPATCHER,
    MAIN_DISPATCHER,
    set_ev_cls,
)
from ryu.lib.packet import ethernet, ether_types, packet
from ryu.ofproto import ofproto_v1_3
from ryu.topology import event

from controller.topology import TopologyManager


class ACOSDNController(app_manager.RyuApp):
    """Discovery and host-learning foundation for the ACO controller."""

    OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.topology = TopologyManager()

    @set_ev_cls(
        ofp_event.EventOFPSwitchFeatures, CONFIG_DISPATCHER
    )
    def switch_features_handler(self, ev) -> None:
        datapath = ev.msg.datapath
        self.topology.add_switch(datapath.id)
        self._install_table_miss(datapath)
        self.logger.info("switch connected: dpid=%s", datapath.id)

    def _install_table_miss(self, datapath) -> None:
        parser = datapath.ofproto_parser
        ofproto = datapath.ofproto
        match = parser.OFPMatch()
        actions = [
            parser.OFPActionOutput(
                ofproto.OFPP_CONTROLLER,
                ofproto.OFPCML_NO_BUFFER,
            )
        ]
        instructions = [
            parser.OFPInstructionActions(
                ofproto.OFPIT_APPLY_ACTIONS,
                actions,
            )
        ]
        datapath.send_msg(
            parser.OFPFlowMod(
                datapath=datapath,
                priority=0,
                match=match,
                instructions=instructions,
            )
        )

    @set_ev_cls(event.EventSwitchEnter)
    def switch_enter_handler(self, ev) -> None:
        dpid = ev.switch.dp.id
        self.topology.add_switch(dpid)
        self.logger.info("topology switch added: dpid=%s", dpid)

    @set_ev_cls(event.EventSwitchLeave)
    def switch_leave_handler(self, ev) -> None:
        dpid = ev.switch.dp.id
        self.topology.remove_switch(dpid)
        self.logger.warning("topology switch removed: dpid=%s", dpid)

    @set_ev_cls(event.EventLinkAdd)
    def link_add_handler(self, ev) -> None:
        link = ev.link
        self.topology.add_link(
            link.src.dpid,
            link.dst.dpid,
            link.src.port_no,
            link.dst.port_no,
        )
        self.logger.info(
            "link added: %s:%s -> %s:%s",
            link.src.dpid,
            link.src.port_no,
            link.dst.dpid,
            link.dst.port_no,
        )

    @set_ev_cls(event.EventLinkDelete)
    def link_delete_handler(self, ev) -> None:
        link = ev.link
        self.topology.remove_link(link.src.dpid, link.dst.dpid)
        self.logger.warning(
            "link removed: %s -> %s",
            link.src.dpid,
            link.dst.dpid,
        )

    @set_ev_cls(ofp_event.EventOFPPacketIn, MAIN_DISPATCHER)
    def packet_in_handler(self, ev) -> None:
        msg = ev.msg
        datapath = msg.datapath
        in_port = msg.match["in_port"]
        parsed = packet.Packet(msg.data)
        frame = parsed.get_protocol(ethernet.ethernet)
        if frame is None or frame.ethertype == ether_types.ETH_TYPE_LLDP:
            return

        moved = self.topology.learn_host(
            frame.src,
            datapath.id,
            in_port,
        )
        if moved:
            self.logger.info(
                "host learned: mac=%s dpid=%s port=%s",
                frame.src,
                datapath.id,
                in_port,
            )

        destination = self.topology.host_location(frame.dst)
        out_port = (
            destination.port
            if destination is not None
            and destination.dpid == datapath.id
            else datapath.ofproto.OFPP_FLOOD
        )
        actions = [
            datapath.ofproto_parser.OFPActionOutput(out_port)
        ]
        data = None
        if msg.buffer_id == datapath.ofproto.OFP_NO_BUFFER:
            data = msg.data
        datapath.send_msg(
            datapath.ofproto_parser.OFPPacketOut(
                datapath=datapath,
                buffer_id=msg.buffer_id,
                in_port=in_port,
                actions=actions,
                data=data,
            )
        )