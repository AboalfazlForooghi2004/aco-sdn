from __future__ import annotations

import heapq
import math
from collections import deque

from aco.cost import CostWeights, link_cost
from aco.models import NetworkGraph


def shortest_hop_path(
    graph: NetworkGraph,
    source: str,
    destination: str,
) -> list[str]:
    """Return a deterministic BFS path using only available links."""
    queue: deque[list[str]] = deque([[source]])
    visited = {source}
    while queue:
        path = queue.popleft()
        current = path[-1]
        if current == destination:
            return path
        for neighbor in sorted(graph.neighbors(current)):
            if neighbor in visited:
                continue
            if not graph.metrics(current, neighbor).available:
                continue
            visited.add(neighbor)
            queue.append(path + [neighbor])
    raise ValueError(f"no path from {source} to {destination}")


def minimum_cost_path(
    graph: NetworkGraph,
    source: str,
    destination: str,
    weights: CostWeights,
) -> list[str]:
    """Return the current deterministic minimum-cost path."""
    queue: list[tuple[float, str, list[str]]] = [
        (0.0, source, [source])
    ]
    best = {source: 0.0}
    while queue:
        cost_so_far, current, path = heapq.heappop(queue)
        if current == destination:
            return path
        if cost_so_far > best.get(current, float("inf")):
            continue
        for neighbor in sorted(graph.neighbors(current)):
            edge_cost = link_cost(
                graph.metrics(current, neighbor),
                weights,
            )
            if not math.isfinite(edge_cost):
                continue
            new_cost = cost_so_far + edge_cost
            if new_cost < best.get(neighbor, float("inf")):
                best[neighbor] = new_cost
                heapq.heappush(
                    queue,
                    (
                        new_cost,
                        neighbor,
                        path + [neighbor],
                    ),
                )
    raise ValueError(f"no path from {source} to {destination}")