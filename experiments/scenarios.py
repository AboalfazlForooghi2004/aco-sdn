from __future__ import annotations

from dataclasses import dataclass

from aco.models import LinkMetrics, NetworkGraph


@dataclass(frozen=True, slots=True)
class Scenario:
    name: str
    graph: NetworkGraph
    source: str = "s1"
    destination: str = "s6"


UPPER = (("s1", "s2"), ("s2", "s4"), ("s4", "s6"))
LOWER = (("s1", "s3"), ("s3", "s5"), ("s5", "s6"))


def _build(
    upper: LinkMetrics,
    lower: LinkMetrics,
    failed_edge: tuple[str, str] | None = None,
) -> NetworkGraph:
    graph = NetworkGraph()
    for left, right in UPPER:
        metrics = (
            LinkMetrics(available=False)
            if failed_edge in {(left, right), (right, left)}
            else upper
        )
        graph.add_bidirectional_link(left, right, metrics)
    for left, right in LOWER:
        metrics = (
            LinkMetrics(available=False)
            if failed_edge in {(left, right), (right, left)}
            else lower
        )
        graph.add_bidirectional_link(left, right, metrics)
    return graph


def build_scenarios() -> tuple[Scenario, ...]:
    normal = LinkMetrics(
        latency_ms=5,
        utilization=0.2,
        loss=0.001,
    )
    healthy = LinkMetrics(
        latency_ms=5,
        utilization=0.15,
        loss=0.001,
    )
    return (
        Scenario("normal", _build(normal, normal)),
        Scenario(
            "congestion",
            _build(
                LinkMetrics(
                    latency_ms=5,
                    utilization=0.9,
                    loss=0.001,
                ),
                healthy,
            ),
        ),
        Scenario(
            "latency",
            _build(
                LinkMetrics(
                    latency_ms=60,
                    utilization=0.2,
                    loss=0.001,
                ),
                healthy,
            ),
        ),
        Scenario(
            "packet_loss",
            _build(
                LinkMetrics(
                    latency_ms=5,
                    utilization=0.2,
                    loss=0.08,
                ),
                healthy,
            ),
        ),
        Scenario(
            "link_failure",
            _build(normal, healthy, failed_edge=("s2", "s4")),
        ),
    )