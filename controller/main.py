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
from ryu.lib.packet import (
    ethernet,
    ether_types,
    ipv4,
    packet,
    tcp,
    udp,
)
from ryu.ofproto import ofproto_v1_3
from ryu.topology import event

from controller.api import SnapshotApiServer
from controller.audit_service import ControllerAuditService
from controller.change_service import (
    RouteChangeOutcome,
    RouteChangeService,
)
from controller.control_cycle import ControlCycleService
from controller.events import EventTimeline
from controller.flow_demand import (
    FlowCounters,
    FlowDemandEstimator,
)
from controller.flow_identity import FlowSelector
from controller.flow_manager import (
    FlowManager,
)
from controller.latency import (
    LatencyTracker,
    decode_echo,
    decode_probe,
    encode_echo,
    encode_probe,
)
from controller.openflow_protocol import OpenFlowProtocol
from controller.packet_flow import PacketFlowService
from controller.rerouting import (
    FlowRegistry,
    RerouteManager,
)
from controller.routing import RoutingService
from controller.simulation import WhatIfSimulator
from controller.settings import (
    load_control_settings,
    load_event_settings,
    load_host_learning_policy,
    load_learning_settings,
    load_optimizer,
    load_prediction_config,
    load_recommendation_settings,
    load_reroute_policy,
    load_simulation_settings,
    load_telemetry_settings,
    load_transaction_settings,
)
from controller.state import (
    MigrationProposal,
    OperatingMode,
    SnapshotStore,
)
from controller.telemetry import PortCounters, TelemetryCollector
from controller.telemetry_history import TelemetryHistory
from controller.transaction_journal import TransactionJournal
from controller.topology import TopologyManager
from controller.transactions import (
    RouteTransactionManager,
    StaleTopologyError,
)
from prediction.engine import PredictionEngine
from recommendation.engine import RecommendationEngine
from learning.dataset import LearningDataset


class ACOSDNController(app_manager.RyuApp):
    """Discovery and host-learning foundation for the ACO controller."""

    OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.topology = TopologyManager(
            load_host_learning_policy()
        )
        self.datapaths = {}
        self.telemetry_settings = load_telemetry_settings()
        self.telemetry = TelemetryCollector(
            link_capacity_bps=(
                self.telemetry_settings.link_capacity_bps
            ),
            max_age_seconds=(
                self.telemetry_settings.max_age_seconds
            ),
            capacity_overrides_bps=(
                self.telemetry_settings
                .port_capacity_overrides_bps
            ),
        )
        self.telemetry_history = TelemetryHistory(
            self.telemetry_settings.history_path,
            self.telemetry_settings.history_interval_seconds,
            self.telemetry_settings.history_max_records,
        )
        self.flow_demand = FlowDemandEstimator(
            ewma_alpha=(
                self.telemetry_settings.flow_demand_ewma_alpha
            ),
            max_age_seconds=(
                self.telemetry_settings.max_age_seconds
            ),
        )
        self.latency = LatencyTracker(
            ewma_alpha=(
                self.telemetry_settings.latency_ewma_alpha
            ),
            max_age_seconds=(
                self.telemetry_settings.max_age_seconds
            ),
        )
        self.routing = RoutingService(load_optimizer())
        simulation_settings = load_simulation_settings()
        self.simulator = WhatIfSimulator(
            self.routing.optimizer.weights,
            utilization_safety_limit=(
                simulation_settings.utilization_safety_limit
            ),
        )
        self.flow_manager = FlowManager()
        transaction_settings = load_transaction_settings()
        self.transaction_journal = TransactionJournal(
            transaction_settings.journal_path
        )
        self._recovered_unresolved_transactions = (
            self.transaction_journal.unresolved()
        )
        self.route_transactions = RouteTransactionManager(
            self.flow_manager,
            transaction_settings.timeout_seconds,
            journal=self.transaction_journal,
        )
        self.flow_registry = FlowRegistry()
        self.packet_flow = PacketFlowService(
            self.flow_manager,
            self.flow_registry,
        )
        self.change_service = RouteChangeService(
            self.flow_manager,
            self.route_transactions,
            self.flow_registry,
        )
        self.control_settings = load_control_settings()
        learning_settings = load_learning_settings()
        self.learning_dataset = LearningDataset(
            learning_settings.dataset_path,
            learning_settings.outcome_horizon_seconds,
            enabled=learning_settings.enabled,
        )
        event_settings = load_event_settings()
        self.event_timeline = EventTimeline(
            event_settings.path,
            event_settings.max_events,
        )
        self.audit = ControllerAuditService(
            self.event_timeline, self.logger
        )
        self.audit.controller_started(
            occurred_at=time.time(),
            mode=self.control_settings.mode.value,
        )
        self.audit.recovered_transactions(
            occurred_at=time.time(),
            transaction_ids=(
                self._recovered_unresolved_transactions
            ),
        )
        self.snapshot_store = SnapshotStore(
            self.control_settings.mode
        )
        self.api_server = None
        if self.control_settings.api_enabled:
            self.api_server = SnapshotApiServer(
                self.snapshot_store,
                self.control_settings.api_host,
                self.control_settings.api_port,
            )
            self.api_server.start()
        self.reroute_manager = RerouteManager(
            self.routing,
            load_reroute_policy(),
        )
        self.prediction = PredictionEngine(
            load_prediction_config()
        )
        recommendation_settings = (
            load_recommendation_settings()
        )
        self.recommendation_engine = RecommendationEngine(
            minimum_confidence=(
                recommendation_settings.minimum_confidence
            ),
            validity_seconds=(
                recommendation_settings.validity_seconds
            ),
        )
        self.control_cycle = ControlCycleService(
            telemetry=self.telemetry,
            latency=self.latency,
            telemetry_history=self.telemetry_history,
            prediction=self.prediction,
            recommendation=self.recommendation_engine,
            learning_dataset=self.learning_dataset,
            flow_registry=self.flow_registry,
            optimizer_weights=self.routing.optimizer.weights,
        )
        self.current_forecasts = ()
        self.current_recommendations = ()
        self.openflow = OpenFlowProtocol()
        self.monitor_thread = hub.spawn(self._monitor)
        if self.api_server is not None:
            host, port = self.api_server.address
            self.logger.info(
                "read-only API started: http://%s:%s "
                "mode=%s",
                host,
                port,
                self.control_settings.mode.value,
            )

    def close(self) -> None:
        if self.api_server is not None:
            self.api_server.shutdown()
        super().close()

    @set_ev_cls(
        ofp_event.EventOFPSwitchFeatures, CONFIG_DISPATCHER
    )
    def switch_features_handler(self, ev) -> None:
        datapath = ev.msg.datapath
        self.topology.add_switch(datapath.id)
        self.flow_manager.purge_managed(datapath)
        self.flow_manager.request_barrier(datapath)
        self.openflow.install_table_miss(datapath)
        self.openflow.request_port_desc(datapath)
        self.logger.info("switch connected: dpid=%s", datapath.id)

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
                self.openflow.request_port_stats(datapath)
                self.openflow.request_flow_stats(datapath)
                self.openflow.request_echo(
                    datapath,
                    encode_echo(time.monotonic()),
                )
            self._send_link_probes()
            hub.sleep(
                self.telemetry_settings.poll_interval_seconds
            )
            self._run_control_cycle()

    def _current_metrics(self, now: float | None = None):
        current_time = (
            time.monotonic() if now is None else now
        )
        return self.control_cycle.current_metrics(
            self.topology, current_time
        )

    def _run_control_cycle(self) -> None:
        now = time.monotonic()
        wall_now = time.time()
        for outcome in self.change_service.expire(
            now=now,
            topology=self.topology,
            datapaths=self.datapaths,
        ):
            self._record_route_change_outcome(outcome)
        analysis = self.control_cycle.analyze(
            topology=self.topology,
            monotonic_now=now,
            wall_now=wall_now,
        )
        metrics = analysis.metrics
        self.current_forecasts = analysis.forecasts
        self.current_recommendations = (
            analysis.recommendations
        )
        self.audit.recommendations(
            self.current_recommendations,
            occurred_at=wall_now,
        )
        proposals = self._evaluate_reroutes(metrics, now)
        self.snapshot_store.publish(
            generated_at=wall_now,
            mode=self.control_settings.mode,
            switches=tuple(self.topology.switches),
            links=self.topology.links,
            metrics=metrics,
            forecasts=self.current_forecasts,
            recommendations=self.current_recommendations,
            flows=self.flow_registry.flows,
            proposals=proposals,
            events=self.event_timeline.recent(),
        )

    def _evaluate_reroutes(
        self,
        metrics,
        now: float,
    ) -> tuple[MigrationProposal, ...]:
        if self.control_settings.mode == OperatingMode.OBSERVE:
            return ()
        proposals = []
        for flow in self.flow_registry.flows:
            if self.change_service.is_pending(flow):
                continue
            migration = self.reroute_manager.evaluate(
                flow,
                self.topology,
                metrics,
                now,
            )
            if migration is None:
                continue
            simulation = self.simulator.compare(
                topology=self.topology,
                metrics=metrics,
                current_path=flow.path,
                proposed_path=migration.decision.path,
                flow_demand_bps=(
                    demand.bits_per_second
                    if (
                        demand := self.flow_demand.get_key(
                            flow.key,
                            now,
                        )
                    )
                    is not None
                    else None
                ),
                link_capacity_bps=(
                    self.telemetry_settings.link_capacity_bps
                ),
                link_capacities_bps=(
                    self.telemetry.link_capacities(
                        self.topology
                    )
                ),
            )
            proposal = MigrationProposal.from_plan(
                migration,
                now,
                simulation=simulation.to_dict(),
            )
            proposals.append(proposal)
            if self.audit.proposal(
                proposal, occurred_at=time.time()
            ):
                learning_observed_at = time.time()
                self.learning_dataset.record_decision(
                    decision_id=(
                        f"{proposal.proposal_id}-"
                        f"{int(learning_observed_at * 1000)}"
                    ),
                    observed_at=learning_observed_at,
                    flow=flow,
                    candidate_path=proposal.new_path,
                    current_cost=proposal.old_cost,
                    candidate_cost=proposal.new_cost,
                    algorithm=(
                        self.routing.optimizer.config.strategy
                    ),
                    mode=self.control_settings.mode.value,
                    simulation_safe=simulation.safe_to_apply,
                    metrics=metrics,
                )
            if (
                self.control_settings.mode
                == OperatingMode.RECOMMEND
            ):
                self.logger.warning(
                    "reroute proposal: id=%s old=%s new=%s "
                    "old_cost=%.4f new_cost=%.4f forced=%s",
                    proposal.proposal_id,
                    proposal.old_path,
                    proposal.new_path,
                    migration.current_cost,
                    proposal.new_cost,
                    proposal.forced,
                )
                continue
            if not simulation.safe_to_apply:
                self.audit.migration_blocked(
                    proposal,
                    violations=simulation.proposed.violations,
                    warnings=simulation.warnings,
                    occurred_at=time.time(),
                )
                continue
            try:
                transaction_id = self.change_service.start(
                    flow=flow,
                    migration=migration,
                    proposal=proposal,
                    topology=self.topology,
                    datapaths=self.datapaths,
                    now=now,
                )
                self.audit.transaction_started(
                    transaction_id=transaction_id,
                    proposal=proposal,
                    old_path=flow.path,
                    new_path=migration.decision.path,
                    topology_generation=(
                        migration.decision.topology_generation
                    ),
                    occurred_at=time.time(),
                )
            except (
                KeyError,
                ValueError,
                StaleTopologyError,
            ) as exc:
                self.audit.migration_failed(
                    proposal,
                    exc,
                    occurred_at=time.time(),
                )
        self.audit.retain_proposals(proposals)
        return tuple(proposals)

    @set_ev_cls(
        ofp_event.EventOFPBarrierReply, MAIN_DISPATCHER
    )
    def barrier_reply_handler(self, ev) -> None:
        outcome = self.change_service.acknowledge(
            dpid=ev.msg.datapath.id,
            xid=ev.msg.xid,
            topology=self.topology,
            datapaths=self.datapaths,
            now=time.monotonic(),
        )
        if outcome is not None:
            self._record_route_change_outcome(outcome)

    def _record_route_change_outcome(
        self,
        outcome: RouteChangeOutcome,
    ) -> None:
        self.audit.route_change_outcome(
            outcome,
            occurred_at=time.time(),
        )

    @set_ev_cls(
        ofp_event.EventOFPPortDescStatsReply, MAIN_DISPATCHER
    )
    def port_desc_reply_handler(self, ev) -> None:
        datapath = ev.msg.datapath
        updated = 0
        for port in ev.msg.body:
            if port.port_no == datapath.ofproto.OFPP_LOCAL:
                continue
            speed_kbps = (
                port.curr_speed
                if port.curr_speed > 0
                else port.max_speed
            )
            if self.telemetry.update_capacity(
                datapath.id,
                port.port_no,
                float(speed_kbps) * 1000.0,
            ):
                updated += 1
        self.logger.info(
            "port capacities discovered: dpid=%s ports=%s",
            datapath.id,
            updated,
        )

    def _send_link_probes(self) -> None:
        sent_at = time.monotonic()
        for (source_dpid, _), ports in (
            self.topology.links.items()
        ):
            datapath = self.datapaths.get(source_dpid)
            if datapath is None:
                continue
            actions = [
                datapath.ofproto_parser.OFPActionOutput(
                    ports.source_port
                )
            ]
            datapath.send_msg(
                datapath.ofproto_parser.OFPPacketOut(
                    datapath=datapath,
                    buffer_id=(
                        datapath.ofproto.OFP_NO_BUFFER
                    ),
                    in_port=(
                        datapath.ofproto.OFPP_CONTROLLER
                    ),
                    actions=actions,
                    data=encode_probe(
                        source_dpid,
                        ports.source_port,
                        sent_at,
                    ),
                )
            )

    @set_ev_cls(
        ofp_event.EventOFPEchoReply, MAIN_DISPATCHER
    )
    def echo_reply_handler(self, ev) -> None:
        sent_at = decode_echo(ev.msg.data)
        if sent_at is None:
            return
        self.latency.record_echo(
            ev.msg.datapath.id,
            sent_at,
            time.monotonic(),
        )

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

    @set_ev_cls(
        ofp_event.EventOFPFlowStatsReply, MAIN_DISPATCHER
    )
    def flow_stats_reply_handler(self, ev) -> None:
        datapath = ev.msg.datapath
        observed_at = time.monotonic()
        for stat in ev.msg.body:
            if stat.priority != self.flow_manager.priority:
                continue
            source_mac = stat.match.get("eth_src")
            destination_mac = stat.match.get("eth_dst")
            if source_mac is None or destination_mac is None:
                continue
            source_mac = str(source_mac).lower()
            destination_mac = str(destination_mac).lower()
            decoded_cookie = self.flow_manager.decode_cookie(
                int(getattr(stat, "cookie", 0))
            )
            for flow in self.flow_registry.flows:
                if (
                    decoded_cookie is not None
                    and decoded_cookie[0]
                    != flow.route_generation
                ):
                    continue
                selector_matches = (
                    flow.selector is None
                    or flow.selector.matches(stat.match)
                    or flow.selector.reverse().matches(
                        stat.match
                    )
                )
                forward_ingress = (
                    datapath.id == flow.source_dpid
                    and source_mac == flow.source_mac
                    and destination_mac
                    == flow.destination_mac
                )
                reverse_ingress = (
                    datapath.id == flow.destination_dpid
                    and source_mac == flow.destination_mac
                    and destination_mac == flow.source_mac
                )
                if (
                    not selector_matches
                    or not (
                        forward_ingress or reverse_ingress
                    )
                ):
                    continue
                self.flow_demand.update_key(
                    flow.key,
                    FlowCounters(
                        byte_count=stat.byte_count,
                        packet_count=stat.packet_count,
                        observed_at=observed_at,
                    ),
                )
                break

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
        probe = decode_probe(msg.data)
        if probe is not None:
            source_dpid, source_port, sent_at = probe
            if self.latency.validate_probe_edge(
                self.topology,
                source_dpid,
                source_port,
                datapath.id,
                in_port,
            ):
                measured = self.latency.record_probe(
                    source_dpid,
                    datapath.id,
                    sent_at,
                    time.monotonic(),
                )
                if measured is not None:
                    self.logger.debug(
                        "link latency: %s -> %s %.3f ms",
                        source_dpid,
                        datapath.id,
                        measured,
                    )
            return
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
            self.openflow.send_packet_out(
                msg,
                in_port,
                datapath.ofproto.OFPP_FLOOD,
            )
            return

        destination = self.topology.host_location(frame.dst)
        if destination is None:
            self.openflow.send_packet_out(
                msg,
                in_port,
                datapath.ofproto.OFPP_FLOOD,
            )
            return

        try:
            metrics = self._current_metrics()
            selector = self._flow_selector(parsed, frame)
            decision = self.routing.select_path(
                self.topology,
                metrics,
                datapath.id,
                destination.dpid,
            )
            installation = self.packet_flow.install_initial(
                topology=self.topology,
                datapaths=self.datapaths,
                decision=decision,
                selector=selector,
                source_dpid=datapath.id,
                destination_dpid=destination.dpid,
                source_host_port=in_port,
                destination_host_port=destination.port,
                installed_at=time.monotonic(),
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
            self.openflow.send_packet_out(
                msg,
                in_port,
                installation.first_output_port,
            )
        except (KeyError, ValueError, StopIteration) as exc:
            self.logger.warning(
                "route installation failed; flooding packet: %s",
                exc,
            )
            self.openflow.send_packet_out(
                msg,
                in_port,
                datapath.ofproto.OFPP_FLOOD,
            )

    def _cleanup_host_flows(self, mac: str) -> None:
        cleanup = self.packet_flow.cleanup_host(
            topology=self.topology,
            datapaths=self.datapaths,
            mac=mac,
        )
        for error in cleanup.errors:
            self.logger.debug(
                "partial host-move cleanup: %s", error
            )
        for flow in cleanup.removed:
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
        match = self.openflow.extract_match(msg.match)
        removed = self.packet_flow.rule_removed(
            match=match,
            cookie=msg.cookie,
            dpid=msg.datapath.id,
        )
        if removed is not None:
            self.logger.info(
                "inactive flow expired: %s -> %s",
                removed.source_mac,
                removed.destination_mac,
            )

    @staticmethod
    def _flow_selector(
        parsed: packet.Packet,
        frame: ethernet.ethernet,
    ) -> FlowSelector:
        network = parsed.get_protocol(ipv4.ipv4)
        tcp_segment = parsed.get_protocol(tcp.tcp)
        udp_datagram = parsed.get_protocol(udp.udp)
        transport = tcp_segment or udp_datagram
        return FlowSelector(
            source_mac=frame.src,
            destination_mac=frame.dst,
            eth_type=frame.ethertype,
            ipv4_source=(
                network.src if network is not None else None
            ),
            ipv4_destination=(
                network.dst if network is not None else None
            ),
            ip_protocol=(
                network.proto if network is not None else None
            ),
            source_port=(
                transport.src_port
                if transport is not None
                else None
            ),
            destination_port=(
                transport.dst_port
                if transport is not None
                else None
            ),
        )
