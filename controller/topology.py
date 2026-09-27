from __future__ import annotations

import time
from dataclasses import dataclass

from aco.models import LinkMetrics, NetworkGraph


@dataclass(frozen=True, slots=True)
class LinkPorts:
    """OpenFlow ports for a directed inter-switch link."""

    source_port: int
    target_port: int


@dataclass(frozen=True, slots=True)
class HostLocation:
    """Most recently observed attachment point for a host."""

    dpid: int
    port: int
    last_seen: float


class TopologyManager:
    """In-memory switches, directed links, and learned host locations."""

    def __init__(self) -> None:
        self._switches: set[int] = set()
        self._links: dict[tuple[int, int], LinkPorts] = {}
        self._hosts: dict[str, HostLocation] = {}

    @property
    def switches(self) -> frozenset[int]:
        return frozenset(self._switches)

    @property
    def links(self) -> dict[tuple[int, int], LinkPorts]:
        return dict(self._links)

    @property
    def hosts(self) -> dict[str, HostLocation]:
        return dict(self._hosts)

    def add_switch(self, dpid: int) -> None:
        self._switches.add(dpid)

    def remove_switch(self, dpid: int) -> None:
        self._switches.discard(dpid)
        self._links = {
            edge: ports
            for edge, ports in self._links.items()
            if dpid not in edge
        }
        self._hosts = {
            mac: location
            for mac, location in self._hosts.items()
            if location.dpid != dpid
        }

    def add_link(
        self,
        source_dpid: int,
        target_dpid: int,
        source_port: int,
        target_port: int,
    ) -> None:
        self.add_switch(source_dpid)
        self.add_switch(target_dpid)
        self._links[(source_dpid, target_dpid)] = LinkPorts(
            source_port=source_port,
            target_port=target_port,
        )

    def remove_link(self, source_dpid: int, target_dpid: int) -> None:
        self._links.pop((source_dpid, target_dpid), None)

    def learn_host(
        self,
        mac: str,
        dpid: int,
        port: int,
        observed_at: float | None = None,
    ) -> bool:
        """Learn or move a host; return True when its attachment changed."""
        normalized_mac = mac.lower()
        timestamp = (
            time.monotonic() if observed_at is None else observed_at
        )
        previous = self._hosts.get(normalized_mac)
        self.add_switch(dpid)
        self._hosts[normalized_mac] = HostLocation(
            dpid=dpid,
            port=port,
            last_seen=timestamp,
        )
        return (
            previous is None
            or previous.dpid != dpid
            or previous.port != port
        )

    def host_location(self, mac: str) -> HostLocation | None:
        return self._hosts.get(mac.lower())

    def output_port(self, source_dpid: int, target_dpid: int) -> int:
        return self._links[(source_dpid, target_dpid)].source_port

    def is_link_port(self, dpid: int, port: int) -> bool:
        return any(
            source == dpid and ports.source_port == port
            for (source, _), ports in self._links.items()
        )

    def build_graph(
        self,
        metrics: dict[tuple[int, int], LinkMetrics] | None = None,
    ) -> NetworkGraph:
        """Build the optimizer graph from current directed links."""
        graph = NetworkGraph()
        for dpid in self._switches:
            graph.add_node(str(dpid))
        supplied_metrics = metrics or {}
        for edge in self._links:
            source, target = edge
            graph.add_link(
                str(source),
                str(target),
                supplied_metrics.get(edge, LinkMetrics()),
            )
        return graph