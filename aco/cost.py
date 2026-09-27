from __future__ import annotations

from dataclasses import dataclass

from .models import LinkMetrics, NetworkGraph


@dataclass(frozen=True, slots=True)
class CostWeights:
    latency: float = 0.30
    utilization: float = 0.30
    loss: float = 0.25
    hop: float = 0.15
    latency_reference_ms: float = 100.0

    def __post_init__(self) -> None:
        total = self.latency + self.utilization + self.loss + self.hop
        if abs(total - 1.0) > 1e-9:
            raise ValueError("cost weights must sum to 1.0")
        if self.latency_reference_ms <= 0:
            raise ValueError("latency_reference_ms must be positive")


def link_cost(metrics: LinkMetrics, weights: CostWeights) -> float:
    """Return a normalized positive cost."""
    if not metrics.available:
        return float("inf")
    normalized_latency = min(
        metrics.latency_ms / weights.latency_reference_ms, 1.0
    )
    return max(
        1e-9,
        weights.latency * normalized_latency
        + weights.utilization * metrics.utilization
        + weights.loss * metrics.loss
        + weights.hop,
    )


def path_cost(
    graph: NetworkGraph, path: list[str], weights: CostWeights
) -> float:
    return sum(
        link_cost(graph.metrics(left, right), weights)
        for left, right in zip(path, path[1:])
    )