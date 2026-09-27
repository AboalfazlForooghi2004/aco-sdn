from __future__ import annotations

import heapq
import math
import random
from dataclasses import dataclass

from .cost import CostWeights, link_cost, path_cost
from .models import NetworkGraph


@dataclass(frozen=True, slots=True)
class ACOConfig:
    strategy: str = "mmas"
    alpha: float = 1.0
    beta: float = 2.0
    evaporation: float = 0.4
    ants: int = 20
    iterations: int = 50
    pheromone_initial: float = 1.0
    pheromone_min: float = 0.05
    pheromone_max: float = 5.0
    deposit_q: float = 1.0
    stagnation_iterations: int = 8
    max_restarts: int = 1
    seed: int = 42

    def __post_init__(self) -> None:
        if self.strategy not in {"ant_system", "mmas"}:
            raise ValueError("strategy must be ant_system or mmas")
        if self.alpha < 0 or self.beta < 0:
            raise ValueError("alpha and beta cannot be negative")
        if not 0.0 < self.evaporation < 1.0:
            raise ValueError("evaporation must be between 0 and 1")
        if self.ants <= 0 or self.iterations <= 0:
            raise ValueError("ants and iterations must be positive")
        if self.pheromone_min <= 0:
            raise ValueError("pheromone_min must be positive")
        if self.pheromone_max < self.pheromone_min:
            raise ValueError(
                "pheromone_max must be greater than pheromone_min"
            )
        if not (
            self.pheromone_min
            <= self.pheromone_initial
            <= self.pheromone_max
        ):
            raise ValueError(
                "pheromone_initial must be inside configured bounds"
            )
        if self.stagnation_iterations <= 0:
            raise ValueError("stagnation_iterations must be positive")
        if self.max_restarts < 0:
            raise ValueError("max_restarts cannot be negative")


@dataclass(frozen=True, slots=True)
class PathResult:
    path: tuple[str, ...]
    cost: float
    used_fallback: bool = False
    strategy: str = "mmas"
    iterations_run: int = 0
    restarts: int = 0
    convergence_reason: str = "completed"


class AntColonyOptimizer:
    """Loop-free AS/MMAS path search with minimum-cost fallback.

    MMAS deposits only on the best-so-far path, clamps pheromone values,
    and performs bounded restarts when the search stagnates. This prevents
    one early random path from permanently dominating the search.
    """

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

        # Reset the generator for reproducible independent decisions.
        self._random.seed(self.config.seed)
        pheromone = self._initial_pheromone(graph)
        best_path: list[str] | None = None
        best_cost = float("inf")
        stagnant_iterations = 0
        restarts = 0
        iterations_run = 0
        convergence_reason = "iteration_budget"

        for iteration in range(1, self.config.iterations + 1):
            iterations_run = iteration
            successful: list[tuple[list[str], float]] = []
            improved = False
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
                    improved = True

            self._update_pheromone(
                pheromone,
                successful,
                best_path,
                best_cost,
            )

            stagnant_iterations = (
                0 if improved else stagnant_iterations + 1
            )
            if (
                self.config.strategy == "mmas"
                and stagnant_iterations
                >= self.config.stagnation_iterations
            ):
                if restarts < self.config.max_restarts:
                    pheromone = self._initial_pheromone(graph)
                    restarts += 1
                    stagnant_iterations = 0
                else:
                    convergence_reason = "stagnation"
                    break

        if best_path is not None:
            return PathResult(
                tuple(best_path),
                best_cost,
                strategy=self.config.strategy,
                iterations_run=iterations_run,
                restarts=restarts,
                convergence_reason=convergence_reason,
            )

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
            strategy=self.config.strategy,
            iterations_run=iterations_run,
            restarts=restarts,
            convergence_reason="fallback",
        )

    def _initial_pheromone(
        self, graph: NetworkGraph
    ) -> dict[tuple[str, str], float]:
        return {
            edge: self.config.pheromone_initial for edge in graph.edges
        }

    def _update_pheromone(
        self,
        pheromone: dict[tuple[str, str], float],
        successful: list[tuple[list[str], float]],
        best_path: list[str] | None,
        best_cost: float,
    ) -> None:
        factor = 1.0 - self.config.evaporation
        for edge in pheromone:
            pheromone[edge] *= factor

        deposits = successful
        if self.config.strategy == "mmas":
            deposits = (
                [(best_path, best_cost)]
                if best_path is not None
                else []
            )

        for candidate, candidate_cost in deposits:
            deposit = self.config.deposit_q / max(
                candidate_cost, 1e-9
            )
            for edge in zip(candidate, candidate[1:]):
                pheromone[edge] += deposit

        lower = (
            self.config.pheromone_min
            if self.config.strategy == "mmas"
            else 1e-12
        )
        upper = (
            self.config.pheromone_max
            if self.config.strategy == "mmas"
            else float("inf")
        )
        for edge, value in pheromone.items():
            pheromone[edge] = min(upper, max(lower, value))

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