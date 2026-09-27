from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class GraphObservation:
    node_ids: tuple[int, ...]
    node_features: tuple[tuple[float, ...], ...]
    edge_index: tuple[tuple[int, int], ...]
    edge_features: tuple[tuple[float, ...], ...]
    global_features: tuple[float, ...]


class GraphObservationEncoder:
    """Convert a routing observation into dependency-free GNN inputs."""

    def __init__(
        self, latency_reference_ms: float = 100.0
    ) -> None:
        if latency_reference_ms <= 0:
            raise ValueError(
                "latency_reference_ms must be positive"
            )
        self.latency_reference_ms = latency_reference_ms

    def encode(
        self, observation: dict[str, Any]
    ) -> GraphObservation:
        links = tuple(observation["link_features"])
        node_ids = tuple(
            sorted(
                {
                    int(link["source"])
                    for link in links
                }
                | {
                    int(link["target"])
                    for link in links
                }
                | {
                    int(observation["source_dpid"]),
                    int(observation["destination_dpid"]),
                }
            )
        )
        index = {
            node_id: position
            for position, node_id in enumerate(node_ids)
        }
        denominator = max(len(node_ids) - 1, 1)
        in_degree = {node_id: 0 for node_id in node_ids}
        out_degree = {node_id: 0 for node_id in node_ids}
        for link in links:
            out_degree[int(link["source"])] += 1
            in_degree[int(link["target"])] += 1

        source = int(observation["source_dpid"])
        destination = int(observation["destination_dpid"])
        node_features = tuple(
            (
                in_degree[node_id] / denominator,
                out_degree[node_id] / denominator,
                float(node_id == source),
                float(node_id == destination),
            )
            for node_id in node_ids
        )

        current_edges = self._path_edges(
            observation["current_path"]
        )
        candidate_edges = self._path_edges(
            observation["candidate_path"]
        )
        sorted_links = tuple(
            sorted(
                links,
                key=lambda item: (
                    int(item["source"]),
                    int(item["target"]),
                ),
            )
        )
        edge_index = tuple(
            (
                index[int(link["source"])],
                index[int(link["target"])],
            )
            for link in sorted_links
        )
        edge_features = tuple(
            (
                min(
                    float(link["latency_ms"])
                    / self.latency_reference_ms,
                    1.0,
                ),
                float(link["utilization"]),
                float(link["loss"]),
                float(bool(link["available"])),
                float(
                    (
                        int(link["source"]),
                        int(link["target"]),
                    )
                    in current_edges
                ),
                float(
                    (
                        int(link["source"]),
                        int(link["target"]),
                    )
                    in candidate_edges
                ),
            )
            for link in sorted_links
        )
        current_cost = observation.get("current_cost")
        candidate_cost = observation.get("candidate_cost")
        global_features = (
            float(current_cost or 0.0),
            float(candidate_cost or 0.0),
            float(bool(observation["simulation_safe"])),
            float(len(observation["current_path"]) - 1),
            float(len(observation["candidate_path"]) - 1),
        )
        return GraphObservation(
            node_ids=node_ids,
            node_features=node_features,
            edge_index=edge_index,
            edge_features=edge_features,
            global_features=global_features,
        )

    @staticmethod
    def _path_edges(
        path: list[int] | tuple[int, ...],
    ) -> frozenset[tuple[int, int]]:
        return frozenset(
            (int(left), int(right))
            for left, right in zip(path, path[1:])
        )