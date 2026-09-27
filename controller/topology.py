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


@dataclass(frozen=True, slots=True)
class HostLearningPolicy:
    move_hold_down_seconds: float = 0.0
    trusted_edge_ports: frozenset[
        tuple[int, int]
    ] = frozenset()

    def __post_init__(self) -> None:
        if self.move_hold_down_seconds < 0:
            raise ValueError(
                "move_hold_down_seconds cannot be negative"
            )


class TopologyManager:
    """In-memory switches, directed links, and learned host locations."""

    def __init__(
        self,
        host_policy: HostLearningPolicy | None = None,
    ) -> None:
        self._switches: set[int] = set()
        self._links: dict[tuple[int, int], LinkPorts] = {}
        self._hosts: dict[str, HostLocation] = {}
        self._generation = 0
        self.host_policy = host_policy or HostLearningPolicy()

    @property
    def generation(self) -> int:
        return self._generation

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
        if dpid not in self._switches:
            self._generation += 1
        self._switches.add(dpid)

    def remove_switch(self, dpid: int) -> None:
        changed = (
            dpid in self._switches
            or any(dpid in edge for edge in self._links)
            or any(
                location.dpid == dpid
                for location in self._hosts.values()
            )
        )
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
        if changed:
            self._generation += 1

    def add_link(
        self,
        source_dpid: int,
        target_dpid: int,
        source_port: int,
        target_port: int,
    ) -> None:
        self.add_switch(source_dpid)
        self.add_switch(target_dpid)
        edge = (source_dpid, target_dpid)
        value = LinkPorts(
            source_port=source_port,
            target_port=target_port,
        )
        if self._links.get(edge) != value:
            self._links[edge] = value
            self._generation += 1

    def remove_link(self, source_dpid: int, target_dpid: int) -> None:
        if self._links.pop(
            (source_dpid, target_dpid), None
        ) is not None:
            self._generation += 1

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
        attachment = (dpid, port)
        if (
            self.host_policy.trusted_edge_ports
            and attachment
            not in self.host_policy.trusted_edge_ports
        ):
            return False
        if (
            previous is not None
            and (previous.dpid, previous.port) != attachment
            and timestamp - previous.last_seen
            < self.host_policy.move_hold_down_seconds
        ):
            return False
        self.add_switch(dpid)
        self._hosts[normalized_mac] = HostLocation(
            dpid=dpid,
            port=port,
            last_seen=timestamp,
        )
        changed = (
            previous is None
            or previous.dpid != dpid
            or previous.port != port
        )
        if changed:
            self._generation += 1
        return changed

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