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
from controller.events import EventTimeline
from controller.flow_demand import (
    FlowCounters,
    FlowDemandEstimator,
)
from controller.flow_identity import FlowSelector
from controller.flow_manager import (
    FlowManager,
    build_bidirectional_plan,
)
from controller.latency import (
    LatencyTracker,
    decode_echo,
    decode_probe,
    encode_echo,
    encode_probe,
)
from controller.rerouting import (
    ActiveFlow,
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
from controller.topology import TopologyManager
from controller.transactions import (
    RouteTransactionManager,
    StaleTopologyError,
    TransactionResult,
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
        self.route_transactions = RouteTransactionManager(
            self.flow_manager,
            transaction_settings.timeout_seconds,
        )
        self._pending_migrations: dict[str, tuple] = {}
        self._pending_flow_transactions: dict[
            tuple, str
        ] = {}
        self.flow_registry = FlowRegistry()
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
        self.event_timeline.append(
            occurred_at=time.time(),
            category="controller",
            severity="info",
            title="Controller started",
            details={
                "mode": self.control_settings.mode.value
            },
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
        self.current_forecasts = ()
        self.current_recommendations = ()
        self._logged_recommendation_ids: set[str] = set()
        self._logged_proposal_ids: set[str] = set()
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
        self._install_table_miss(datapath)
        self._request_port_desc(datapath)
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
                self._request_flow_stats(datapath)
                self._request_echo(datapath)
            self._send_link_probes()
            hub.sleep(
                self.telemetry_settings.poll_interval_seconds
            )
            self._run_control_cycle()

    def _current_metrics(self, now: float | None = None):
        current_time = (
            time.monotonic() if now is None else now
        )
        port_metrics = self.telemetry.link_metrics(
            self.topology,
            now=current_time,
        )
        return self.latency.enrich(
            port_metrics, now=current_time
        )

    def _run_control_cycle(self) -> None:
        now = time.monotonic()
        wall_now = time.time()
        for result in self.route_transactions.expire(
            now=now,
            current_generation=self.topology.generation,
            datapaths=self.datapaths,
        ):
            self._finalize_route_transaction(result, now)
        metrics = self._current_metrics(now)
        self.telemetry_history.append(
            observed_at=wall_now,
            topology_generation=self.topology.generation,
            metrics=metrics,
        )
        self.learning_dataset.settle_due(
            observed_at=wall_now,
            flows=self.flow_registry.flows,
            topology=self.topology,
            metrics=metrics,
            weights=self.routing.optimizer.weights,
        )
        self.prediction.observe(metrics, now)
        self.current_forecasts = (
            self.prediction.forecast_all(now)
        )
        self.current_recommendations = (
            self.recommendation_engine.generate(
                self.current_forecasts,
                self.flow_registry.flows,
                now,
            )
        )
        current_ids = {
            item.recommendation_id
            for item in self.current_recommendations
        }
        for recommendation in self.current_recommendations:
            if (
                recommendation.recommendation_id
                in self._logged_recommendation_ids
            ):
                continue
            self.logger.warning(
                "network recommendation: id=%s title=%s "
                "urgency=%s confidence=%.2f affected=%s "
                "signals=%s",
                recommendation.recommendation_id,
                recommendation.title,
                recommendation.urgency,
                recommendation.confidence,
                len(recommendation.affected_flows),
                recommendation.rationale,
            )
            self.event_timeline.append(
                occurred_at=time.time(),
                category="recommendation",
                severity=(
                    "warning"
                    if recommendation.urgency == "high"
                    else "info"
                ),
                title=recommendation.title,
                details={
                    "recommendation_id": (
                        recommendation.recommendation_id
                    ),
                    "confidence": (
                        recommendation.confidence
                    ),
                    "urgency": recommendation.urgency,
                    "signals": list(
                        recommendation.rationale
                    ),
                    "affected_flows": [
                        list(item)
                        for item in (
                            recommendation.affected_flows
                        )
                    ],
                },
            )
        self._logged_recommendation_ids = current_ids
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
            if flow.key in self._pending_flow_transactions:
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
                        demand := self.flow_demand.get(
                            flow.source_mac,
                            flow.destination_mac,
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
            if (
                proposal.proposal_id
                not in self._logged_proposal_ids
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
                self.event_timeline.append(
                    occurred_at=time.time(),
                    category="migration",
                    severity=(
                        "warning"
                        if proposal.forced
                        else "info"
                    ),
                    title="Route migration proposed",
                    details={
                        "proposal_id": proposal.proposal_id,
                        "source_mac": proposal.source_mac,
                        "destination_mac": (
                            proposal.destination_mac
                        ),
                        "old_path": list(proposal.old_path),
                        "new_path": list(proposal.new_path),
                        "old_cost": proposal.old_cost,
                        "new_cost": proposal.new_cost,
                        "forced": proposal.forced,
                        "simulation": proposal.simulation,
                    },
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
                self.logger.error(
                    "reroute blocked by what-if simulation: "
                    "id=%s violations=%s",
                    proposal.proposal_id,
                    simulation.proposed.violations,
                )
                self.event_timeline.append(
                    occurred_at=time.time(),
                    category="migration",
                    severity="error",
                    title="Route migration blocked by simulation",
                    details={
                        "proposal_id": proposal.proposal_id,
                        "violations": list(
                            simulation.proposed.violations
                        ),
                        "warnings": list(
                            simulation.warnings
                        ),
                    },
                )
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
                    selector=flow.selector,
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
                    selector=flow.selector,
                )
                transaction_id = (
                    f"{proposal.proposal_id}-"
                    f"{time.monotonic_ns()}"
                )
                self.route_transactions.begin(
                    transaction_id=transaction_id,
                    topology_generation=(
                        migration.decision.topology_generation
                    ),
                    current_generation=(
                        self.topology.generation
                    ),
                    datapaths=self.datapaths,
                    old_rules=old_rules,
                    new_rules=new_rules,
                    now=now,
                )
                self._pending_migrations[transaction_id] = (
                    flow,
                    migration,
                    proposal,
                )
                self._pending_flow_transactions[
                    flow.key
                ] = transaction_id
                self.logger.info(
                    "route transaction started: id=%s "
                    "%s -> %s old=%s new=%s generation=%s",
                    transaction_id,
                    flow.source_mac,
                    flow.destination_mac,
                    flow.path,
                    migration.decision.path,
                    migration.decision.topology_generation,
                )
                self.event_timeline.append(
                    occurred_at=time.time(),
                    category="migration",
                    severity="info",
                    title="Route migration transaction started",
                    details={
                        "transaction_id": transaction_id,
                        "proposal_id": proposal.proposal_id,
                        "old_path": list(flow.path),
                        "new_path": list(
                            migration.decision.path
                        ),
                        "topology_generation": (
                            migration.decision
                            .topology_generation
                        ),
                    },
                )
            except (
                KeyError,
                ValueError,
                StaleTopologyError,
            ) as exc:
                self.logger.warning(
                    "flow reroute failed: %s", exc
                )
                self.event_timeline.append(
                    occurred_at=time.time(),
                    category="migration",
                    severity="error",
                    title="Route migration failed",
                    details={
                        "proposal_id": proposal.proposal_id,
                        "error": str(exc),
                    },
                )
        self._logged_proposal_ids = {
            item.proposal_id for item in proposals
        }
        return tuple(proposals)

    @set_ev_cls(
        ofp_event.EventOFPBarrierReply, MAIN_DISPATCHER
    )
    def barrier_reply_handler(self, ev) -> None:
        result = self.route_transactions.acknowledge(
            dpid=ev.msg.datapath.id,
            xid=ev.msg.xid,
            current_generation=self.topology.generation,
            datapaths=self.datapaths,
        )
        if result is not None:
            self._finalize_route_transaction(
                result, time.monotonic()
            )

    def _finalize_route_transaction(
        self,
        result: TransactionResult,
        now: float,
    ) -> None:
        context = self._pending_migrations.pop(
            result.transaction_id, None
        )
        if context is None:
            return
        flow, migration, proposal = context
        self._pending_flow_transactions.pop(flow.key, None)
        if result.status == "committed":
            self.flow_registry.replace(
                flow,
                migration.decision,
                changed_at=now,
            )
            self.logger.info(
                "route transaction committed: id=%s "
                "old=%s new=%s",
                result.transaction_id,
                flow.path,
                migration.decision.path,
            )
            severity = "info"
            title = "Route migration committed"
        else:
            self.logger.error(
                "route transaction rolled back: id=%s "
                "reason=%s",
                result.transaction_id,
                result.reason,
            )
            severity = "error"
            title = "Route migration rolled back"
        self.event_timeline.append(
            occurred_at=time.time(),
            category="migration",
            severity=severity,
            title=title,
            details={
                "transaction_id": result.transaction_id,
                "proposal_id": proposal.proposal_id,
                "status": result.status,
                "reason": result.reason,
                "old_path": list(flow.path),
                "new_path": list(
                    migration.decision.path
                ),
            },
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

    @staticmethod
    def _request_port_desc(datapath) -> None:
        datapath.send_msg(
            datapath.ofproto_parser.OFPPortDescStatsRequest(
                datapath, 0
            )
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

    @staticmethod
    def _request_flow_stats(datapath) -> None:
        datapath.send_msg(
            datapath.ofproto_parser.OFPFlowStatsRequest(
                datapath
            )
        )

    @staticmethod
    def _request_echo(datapath) -> None:
        datapath.send_msg(
            datapath.ofproto_parser.OFPEchoRequest(
                datapath,
                data=encode_echo(time.monotonic()),
            )
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
            for flow in self.flow_registry.flows:
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
                self.flow_demand.update(
                    source_mac,
                    destination_mac,
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
            metrics = self._current_metrics()
            selector = self._flow_selector(parsed, frame)
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
                selector=selector,
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
                    selector=selector,
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
                    selector=flow.selector,
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
        match = {
            name: msg.match.get(name)
            for name in (
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
            )
            if msg.match.get(name) is not None
        }
        removed = self.flow_registry.remove_by_match(
            match
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