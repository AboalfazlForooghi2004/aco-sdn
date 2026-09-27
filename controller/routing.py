from __future__ import annotations

from collections import deque
from dataclasses import dataclass

from aco.cost import path_cost
from aco.models import LinkMetrics
from aco.optimizer import AntColonyOptimizer
from controller.topology import TopologyManager


@dataclass(frozen=True, slots=True)
class RoutingDecision:
    path: tuple[int, ...]
    cost: float
    used_fallback: bool


class RoutingService:
    """Select an ACO path or deterministic minimum-hop fallback."""

    def __init__(self, optimizer: AntColonyOptimizer) -> None:
        self.optimizer = optimizer

    def select_path(
        self,
        topology: TopologyManager,
        metrics: dict[tuple[int, int], LinkMetrics],
        source_dpid: int,
        destination_dpid: int,
        allow_topology_fallback: bool = True,
    ) -> RoutingDecision:
        if source_dpid == destination_dpid:
            return RoutingDecision(
                path=(source_dpid,),
                cost=0.0,
                used_fallback=False,
            )

        graph = topology.build_graph(metrics)
        try:
            result = self.optimizer.optimize(
                graph,
                str(source_dpid),
                str(destination_dpid),
            )
            return RoutingDecision(
                path=tuple(int(node) for node in result.path),
                cost=result.cost,
                used_fallback=result.used_fallback,
            )
        except ValueError:
            if not allow_topology_fallback:
                raise
            fallback = self._minimum_hop_path(
                topology,
                source_dpid,
                destination_dpid,
            )
            if fallback is None:
                raise ValueError(
                    "no topology path from "
                    f"{source_dpid} to {destination_dpid}"
                )
            fallback_graph = topology.build_graph()
            as_strings = [str(node) for node in fallback]
            return RoutingDecision(
                path=tuple(fallback),
                cost=path_cost(
                    fallback_graph,
                    as_strings,
                    self.optimizer.weights,
                ),
                used_fallback=True,
            )

    @staticmethod
    def _minimum_hop_path(
        topology: TopologyManager,
        source_dpid: int,
        destination_dpid: int,
    ) -> list[int] | None:
        adjacency: dict[int, list[int]] = {}
        for source, target in topology.links:
            adjacency.setdefault(source, []).append(target)
        for neighbors in adjacency.values():
            neighbors.sort()

        queue: deque[list[int]] = deque([[source_dpid]])
        visited = {source_dpid}
        while queue:
            path = queue.popleft()
            current = path[-1]
            if current == destination_dpid:
                return path
            for neighbor in adjacency.get(current, []):
                if neighbor in visited:
                    continue
                visited.add(neighbor)
                queue.append(path + [neighbor])
        return None