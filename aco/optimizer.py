from __future__ import annotations

import heapq
import math
import random
from dataclasses import dataclass

from .cost import CostWeights, link_cost, path_cost
from .models import NetworkGraph


@dataclass(frozen=True, slots=True)
class ACOConfig:
    alpha: float = 1.0
    beta: float = 2.0
    evaporation: float = 0.4
    ants: int = 20
    iterations: int = 50
    pheromone_initial: float = 1.0
    deposit_q: float = 1.0
    seed: int = 42

    def __post_init__(self) -> None:
        if self.alpha < 0 or self.beta < 0:
            raise ValueError("alpha and beta cannot be negative")
        if not 0.0 < self.evaporation < 1.0:
            raise ValueError("evaporation must be between 0 and 1")
        if self.ants <= 0 or self.iterations <= 0:
            raise ValueError("ants and iterations must be positive")


@dataclass(frozen=True, slots=True)
class PathResult:
    path: tuple[str, ...]
    cost: float
    used_fallback: bool = False


class AntColonyOptimizer:
    """Loop-free ACO path search with minimum-cost fallback."""

    def __init__(
        self, config: ACOConfig, weights: CostWeights | None = None
    ) -> None:
        self.config = config
        self.weights = weights or CostWeights()
        self._random = random.Random(config.seed)

    def optimize(
        self, graph: NetworkGraph, source: str, destination: str
    ) -> PathResult:
        if source not in graph.nodes or destination not in graph.nodes:
            raise ValueError("source and destination must exist in the graph")

        pheromone = {
            edge: self.config.pheromone_initial for edge in graph.edges
        }
        best_path: list[str] | None = None
        best_cost = float("inf")

        for _ in range(self.config.iterations):
            successful: list[tuple[list[str], float]] = []
            for _ in range(self.config.ants):
                candidate = self._construct_path(
                    graph, source, destination, pheromone
                )
                if candidate is None:
                    continue
                candidate_cost = path_cost(
                    graph, candidate, self.weights
                )
                successful.append((candidate, candidate_cost))
                if candidate_cost < best_cost:
                    best_path, best_cost = candidate, candidate_cost

            factor = 1.0 - self.config.evaporation
            for edge in pheromone:
                pheromone[edge] = max(
                    1e-12, pheromone[edge] * factor
                )
            for candidate, candidate_cost in successful:
                deposit = self.config.deposit_q / max(
                    candidate_cost, 1e-9
                )
                for edge in zip(candidate, candidate[1:]):
                    pheromone[edge] += deposit

        if best_path is not None:
            return PathResult(tuple(best_path), best_cost)

        fallback = self._shortest_path(
            graph, source, destination
        )
        if fallback is None:
            raise ValueError(
                f"no available path from {source} to {destination}"
            )
        return PathResult(
            tuple(fallback),
            path_cost(graph, fallback, self.weights),
            used_fallback=True,
        )

    def _construct_path(
        self,
        graph: NetworkGraph,
        source: str,
        destination: str,
        pheromone: dict[tuple[str, str], float],
    ) -> list[str] | None:
        path = [source]
        visited = {source}
        current = source

        while current != destination and len(path) <= len(graph.nodes):
            candidates = [
                node
                for node in graph.neighbors(current)
                if node not in visited
                and math.isfinite(
                    link_cost(
                        graph.metrics(current, node),
                        self.weights,
                    )
                )
            ]
            if not candidates:
                return None

            scores = []
            for node in candidates:
                cost = link_cost(
                    graph.metrics(current, node), self.weights
                )
                heuristic = 1.0 / max(cost, 1e-9)
                score = (
                    pheromone[(current, node)] ** self.config.alpha
                    * heuristic**self.config.beta
                )
                scores.append(score)

            current = self._weighted_choice(candidates, scores)
            path.append(current)
            visited.add(current)

        return path if current == destination else None

    def _weighted_choice(
        self, candidates: list[str], scores: list[float]
    ) -> str:
        total = sum(scores)
        if total <= 0:
            return candidates[0]
        marker = self._random.random() * total
        cumulative = 0.0
        for candidate, score in zip(candidates, scores):
            cumulative += score
            if marker <= cumulative:
                return candidate
        return candidates[-1]

    def _shortest_path(
        self,
        graph: NetworkGraph,
        source: str,
        destination: str,
    ) -> list[str] | None:
        queue: list[tuple[float, str, list[str]]] = [
            (0.0, source, [source])
        ]
        best = {source: 0.0}
        while queue:
            cost_so_far, node, path = heapq.heappop(queue)
            if node == destination:
                return path
            if cost_so_far > best.get(node, float("inf")):
                continue
            for neighbor in graph.neighbors(node):
                edge_cost = link_cost(
                    graph.metrics(node, neighbor), self.weights
                )
                if not math.isfinite(edge_cost):
                    continue
                new_cost = cost_so_far + edge_cost
                if new_cost < best.get(neighbor, float("inf")):
                    best[neighbor] = new_cost
                    heapq.heappush(
                        queue,
                        (new_cost, neighbor, path + [neighbor]),
                    )
        return None