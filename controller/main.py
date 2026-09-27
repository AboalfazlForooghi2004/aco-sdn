from __future__ import annotations

import time

from ryu.base import app_manager
from ryu.controller import ofp_event
from ryu.controller.handler import (
    CONFIG_DISPATCHER,
    DEAD_DISPATCHER,
    MAIN_DISPATCHER,
    set_ev_cls,
)
from ryu.lib import hub
from ryu.lib.packet import ethernet, ether_types, packet
from ryu.ofproto import ofproto_v1_3
from ryu.topology import event

from controller.flow_manager import (
    FlowManager,
    build_bidirectional_plan,
)
from controller.rerouting import (
    ActiveFlow,
    FlowRegistry,
    RerouteManager,
)
from controller.routing import RoutingService
from controller.settings import (
    load_optimizer,
    load_reroute_policy,
    load_telemetry_settings,
)
from controller.telemetry import PortCounters, TelemetryCollector
from controller.topology import TopologyManager


class ACOSDNController(app_manager.RyuApp):
    """Discovery and host-learning foundation for the ACO controller."""

    OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.topology = TopologyManager()
        self.datapaths = {}
        self.telemetry_settings = load_telemetry_settings()
        self.telemetry = TelemetryCollector(
            link_capacity_bps=(
                self.telemetry_settings.link_capacity_bps
            ),
            max_age_seconds=(
                self.telemetry_settings.max_age_seconds
            ),
        )
        self.routing = RoutingService(load_optimizer())
        self.flow_manager = FlowManager()
        self.flow_registry = FlowRegistry()
        self.reroute_manager = RerouteManager(
            self.routing,
            load_reroute_policy(),
        )
        self.monitor_thread = hub.spawn(self._monitor)

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

    @set_ev_cls(
        ofp_event.EventOFPStateChange,
        [MAIN_DISPATCHER, DEAD_DISPATCHER],
    )
    def state_change_handler(self, ev) -> None:
        datapath = ev.datapath
        if ev.state == MAIN_DISPATCHER:
            self.datapaths[datapath.id] = datapath
        elif ev.state == DEAD_DISPATCHER:
            self.datapaths.pop(datapath.id, None)

    def _monitor(self) -> None:
        while True:
            for datapath in list(self.datapaths.values()):
                self._request_port_stats(datapath)
            hub.sleep(
                self.telemetry_settings.poll_interval_seconds
            )
            self._evaluate_reroutes()

    def _evaluate_reroutes(self) -> None:
        metrics = self.telemetry.link_metrics(self.topology)
        now = time.monotonic()
        for flow in self.flow_registry.flows:
            migration = self.reroute_manager.evaluate(
                flow,
                self.topology,
                metrics,
                now,
            )
            if migration is None:
                continue
            try:
                new_rules = build_bidirectional_plan(
                    topology=self.topology,
                    path=migration.decision.path,
                    source_mac=flow.source_mac,
                    destination_mac=flow.destination_mac,
                    source_host_port=flow.source_host_port,
                    destination_host_port=(
                        flow.destination_host_port
                    ),
                )
                old_rules = build_bidirectional_plan(
                    topology=self.topology,
                    path=flow.path,
                    source_mac=flow.source_mac,
                    destination_mac=flow.destination_mac,
                    source_host_port=flow.source_host_port,
                    destination_host_port=(
                        flow.destination_host_port
                    ),
                )
                # Make-before-break: add/modify the new path first.
                self.flow_manager.install(
                    self.datapaths,
                    new_rules,
                )
                self.flow_manager.delete(
                    self.datapaths,
                    old_rules,
                    exclude_dpids=frozenset(
                        migration.decision.path
                    ),
                )
                self.flow_registry.replace(
                    flow,
                    migration.decision,
                    changed_at=now,
                )
                self.logger.info(
                    "flow rerouted: %s -> %s old=%s new=%s "
                    "old_cost=%.4f new_cost=%.4f forced=%s",
                    flow.source_mac,
                    flow.destination_mac,
                    flow.path,
                    migration.decision.path,
                    migration.current_cost,
                    migration.decision.cost,
                    migration.forced,
                )
            except (KeyError, ValueError) as exc:
                self.logger.warning(
                    "flow reroute failed: %s", exc
                )

    @staticmethod
    def _request_port_stats(datapath) -> None:
        parser = datapath.ofproto_parser
        request = parser.OFPPortStatsRequest(
            datapath,
            0,
            datapath.ofproto.OFPP_ANY,
        )
        datapath.send_msg(request)

    @set_ev_cls(
        ofp_event.EventOFPPortStatsReply, MAIN_DISPATCHER
    )
    def port_stats_reply_handler(self, ev) -> None:
        datapath = ev.msg.datapath
        observed_at = time.monotonic()
        updated = 0
        for stat in ev.msg.body:
            if stat.port_no == datapath.ofproto.OFPP_LOCAL:
                continue
            result = self.telemetry.update(
                datapath.id,
                stat.port_no,
                PortCounters(
                    rx_bytes=stat.rx_bytes,
                    tx_bytes=stat.tx_bytes,
                    rx_packets=stat.rx_packets,
                    tx_packets=stat.tx_packets,
                    rx_dropped=stat.rx_dropped,
                    tx_dropped=stat.tx_dropped,
                    observed_at=observed_at,
                ),
            )
            if result is not None:
                updated += 1
        self.logger.debug(
            "port telemetry updated: dpid=%s ports=%s",
            datapath.id,
            updated,
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

        if not self.topology.is_link_port(datapath.id, in_port):
            previous_location = self.topology.host_location(
                frame.src
            )
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
            if moved and previous_location is not None:
                self._cleanup_host_flows(frame.src)

        source = self.topology.host_location(frame.src)
        if (
            source is None
            or source.dpid != datapath.id
            or source.port != in_port
        ):
            self._send_packet_out(
                msg,
                in_port,
                datapath.ofproto.OFPP_FLOOD,
            )
            return

        destination = self.topology.host_location(frame.dst)
        if destination is None:
            self._send_packet_out(
                msg,
                in_port,
                datapath.ofproto.OFPP_FLOOD,
            )
            return

        try:
            metrics = self.telemetry.link_metrics(self.topology)
            decision = self.routing.select_path(
                self.topology,
                metrics,
                datapath.id,
                destination.dpid,
            )
            rules = build_bidirectional_plan(
                topology=self.topology,
                path=decision.path,
                source_mac=frame.src,
                destination_mac=frame.dst,
                source_host_port=in_port,
                destination_host_port=destination.port,
            )
            self.flow_manager.install(self.datapaths, rules)
            self.flow_registry.register_initial(
                ActiveFlow(
                    source_mac=frame.src.lower(),
                    destination_mac=frame.dst.lower(),
                    source_dpid=datapath.id,
                    destination_dpid=destination.dpid,
                    source_host_port=in_port,
                    destination_host_port=destination.port,
                    path=decision.path,
                    installed_cost=decision.cost,
                    last_reroute_at=time.monotonic(),
                )
            )
            first_forward_rule = next(
                rule
                for rule in rules
                if rule.dpid == datapath.id
                and rule.source_mac == frame.src.lower()
                and rule.destination_mac == frame.dst.lower()
            )
            self.logger.info(
                "route installed: %s -> %s path=%s "
                "cost=%.4f fallback=%s",
                frame.src,
                frame.dst,
                decision.path,
                decision.cost,
                decision.used_fallback,
            )
            self._send_packet_out(
                msg,
                in_port,
                first_forward_rule.output_port,
            )
        except (KeyError, ValueError, StopIteration) as exc:
            self.logger.warning(
                "route installation failed; flooding packet: %s",
                exc,
            )
            self._send_packet_out(
                msg,
                in_port,
                datapath.ofproto.OFPP_FLOOD,
            )

    def _cleanup_host_flows(self, mac: str) -> None:
        for flow in self.flow_registry.flows_for_host(mac):
            try:
                rules = build_bidirectional_plan(
                    topology=self.topology,
                    path=flow.path,
                    source_mac=flow.source_mac,
                    destination_mac=flow.destination_mac,
                    source_host_port=flow.source_host_port,
                    destination_host_port=(
                        flow.destination_host_port
                    ),
                )
                self.flow_manager.delete(self.datapaths, rules)
            except KeyError as exc:
                self.logger.debug(
                    "partial host-move cleanup: %s", exc
                )
            self.flow_registry.remove(flow)
            self.logger.info(
                "flow removed after host move: %s -> %s",
                flow.source_mac,
                flow.destination_mac,
            )

    @set_ev_cls(
        ofp_event.EventOFPFlowRemoved, MAIN_DISPATCHER
    )
    def flow_removed_handler(self, ev) -> None:
        msg = ev.msg
        if msg.reason not in {
            msg.datapath.ofproto.OFPRR_IDLE_TIMEOUT,
            msg.datapath.ofproto.OFPRR_HARD_TIMEOUT,
        }:
            return
        source_mac = msg.match.get("eth_src")
        destination_mac = msg.match.get("eth_dst")
        if source_mac is None or destination_mac is None:
            return
        removed = self.flow_registry.remove_by_macs(
            source_mac,
            destination_mac,
        )
        if removed is not None:
            self.logger.info(
                "inactive flow expired: %s -> %s",
                removed.source_mac,
                removed.destination_mac,
            )

    @staticmethod
    def _send_packet_out(msg, in_port: int, out_port: int) -> None:
        datapath = msg.datapath
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