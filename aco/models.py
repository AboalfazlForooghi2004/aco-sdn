from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Iterable


@dataclass(frozen=True, slots=True)
class LinkMetrics:
    """Current measurements for a directed link."""

    latency_ms: float = 0.0
    utilization: float = 0.0
    loss: float = 0.0
    available: bool = True

    def __post_init__(self) -> None:
        if self.latency_ms < 0:
            raise ValueError("latency_ms cannot be negative")
        if not 0.0 <= self.utilization <= 1.0:
            raise ValueError("utilization must be between 0 and 1")
        if not 0.0 <= self.loss <= 1.0:
            raise ValueError("loss must be between 0 and 1")


class NetworkGraph:
    """Small directed graph whose edges carry live link metrics."""

    def __init__(self) -> None:
        self._adjacency: Dict[str, Dict[str, LinkMetrics]] = {}

    def add_link(self, source: str, target: str, metrics: LinkMetrics) -> None:
        self._adjacency.setdefault(source, {})[target] = metrics
        self._adjacency.setdefault(target, {})

    def add_node(self, node: str) -> None:
        self._adjacency.setdefault(node, {})

    def add_bidirectional_link(
        self, left: str, right: str, metrics: LinkMetrics
    ) -> None:
        self.add_link(left, right, metrics)
        self.add_link(right, left, metrics)

    def neighbors(self, node: str) -> Iterable[str]:
        return self._adjacency.get(node, {}).keys()

    def metrics(self, source: str, target: str) -> LinkMetrics:
        return self._adjacency[source][target]

    @property
    def nodes(self) -> tuple[str, ...]:
        return tuple(self._adjacency)

    @property
    def edges(self) -> tuple[tuple[str, str], ...]:
        return tuple(
            (source, target)
            for source, links in self._adjacency.items()
            for target in links
        )