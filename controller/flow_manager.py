from __future__ import annotations

from dataclasses import dataclass

from controller.topology import TopologyManager


@dataclass(frozen=True, slots=True)
class PlannedRule:
    dpid: int
    source_mac: str
    destination_mac: str
    output_port: int


def build_bidirectional_plan(
    topology: TopologyManager,
    path: tuple[int, ...],
    source_mac: str,
    destination_mac: str,
    source_host_port: int,
    destination_host_port: int,
) -> tuple[PlannedRule, ...]:
    if not path:
        raise ValueError("path cannot be empty")

    rules: list[PlannedRule] = []
    for index, dpid in enumerate(path):
        forward_port = (
            destination_host_port
            if index == len(path) - 1
            else topology.output_port(dpid, path[index + 1])
        )
        reverse_port = (
            source_host_port
            if index == 0
            else topology.output_port(dpid, path[index - 1])
        )
        rules.append(
            PlannedRule(
                dpid=dpid,
                source_mac=source_mac.lower(),
                destination_mac=destination_mac.lower(),
                output_port=forward_port,
            )
        )
        rules.append(
            PlannedRule(
                dpid=dpid,
                source_mac=destination_mac.lower(),
                destination_mac=source_mac.lower(),
                output_port=reverse_port,
            )
        )
    return tuple(rules)


class FlowManager:
    """Translate pure rule plans into OpenFlow 1.3 FlowMod messages."""

    def __init__(
        self,
        priority: int = 100,
        idle_timeout: int = 30,
        cookie: int = 0xAC05D,
    ) -> None:
        self.priority = priority
        self.idle_timeout = idle_timeout
        self.cookie = cookie

    def install(
        self,
        datapaths: dict[int, object],
        rules: tuple[PlannedRule, ...],
    ) -> None:
        missing = {
            rule.dpid
            for rule in rules
            if rule.dpid not in datapaths
        }
        if missing:
            raise ValueError(
                f"missing datapaths for switches: {sorted(missing)}"
            )

        for rule in rules:
            datapath = datapaths[rule.dpid]
            parser = datapath.ofproto_parser
            match = parser.OFPMatch(
                eth_src=rule.source_mac,
                eth_dst=rule.destination_mac,
            )
            actions = [
                parser.OFPActionOutput(rule.output_port)
            ]
            instructions = [
                parser.OFPInstructionActions(
                    datapath.ofproto.OFPIT_APPLY_ACTIONS,
                    actions,
                )
            ]
            datapath.send_msg(
                parser.OFPFlowMod(
                    datapath=datapath,
                    cookie=self.cookie,
                    priority=self.priority,
                    idle_timeout=self.idle_timeout,
                    match=match,
                    instructions=instructions,
                )
            )

    def delete(
        self,
        datapaths: dict[int, object],
        rules: tuple[PlannedRule, ...],
        exclude_dpids: frozenset[int] = frozenset(),
    ) -> None:
        """Strictly delete old-path rules outside the replacement path."""
        for rule in rules:
            if (
                rule.dpid in exclude_dpids
                or rule.dpid not in datapaths
            ):
                continue
            datapath = datapaths[rule.dpid]
            parser = datapath.ofproto_parser
            match = parser.OFPMatch(
                eth_src=rule.source_mac,
                eth_dst=rule.destination_mac,
            )
            datapath.send_msg(
                parser.OFPFlowMod(
                    datapath=datapath,
                    cookie=self.cookie,
                    cookie_mask=0xFFFFFFFFFFFFFFFF,
                    command=datapath.ofproto.OFPFC_DELETE_STRICT,
                    priority=self.priority,
                    out_port=datapath.ofproto.OFPP_ANY,
                    out_group=datapath.ofproto.OFPG_ANY,
                    match=match,
                )
            )