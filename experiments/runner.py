from __future__ import annotations

import csv
import math
import time
from dataclasses import asdict, dataclass, replace
from pathlib import Path

from aco.cost import CostWeights, path_cost
from aco.models import NetworkGraph
from aco.optimizer import ACOConfig, AntColonyOptimizer
from experiments.baselines import (
    minimum_cost_path,
    shortest_hop_path,
)
from experiments.scenarios import Scenario, build_scenarios


@dataclass(frozen=True, slots=True)
class ExperimentResult:
    scenario: str
    algorithm: str
    path: str
    cost: float
    latency_ms: float
    average_utilization: float
    packet_loss: float
    execution_ms: float
    seed: int


def _path_metrics(
    graph: NetworkGraph,
    path: list[str],
) -> tuple[float, float, float]:
    links = [
        graph.metrics(left, right)
        for left, right in zip(path, path[1:])
    ]
    latency = sum(link.latency_ms for link in links)
    utilization = (
        sum(link.utilization for link in links) / len(links)
        if links
        else 0.0
    )
    delivery_probability = math.prod(
        1.0 - link.loss for link in links
    )
    return latency, utilization, 1.0 - delivery_probability


def evaluate_scenario(
    scenario: Scenario,
    weights: CostWeights,
    aco_config: ACOConfig,
) -> tuple[ExperimentResult, ...]:
    algorithms = {
        "shortest_path": lambda: shortest_hop_path(
            scenario.graph,
            scenario.source,
            scenario.destination,
        ),
        "minimum_dynamic_cost": lambda: minimum_cost_path(
            scenario.graph,
            scenario.source,
            scenario.destination,
            weights,
        ),
        "aco_ant_system": lambda: list(
            AntColonyOptimizer(
                replace(aco_config, strategy="ant_system"), weights
            ).optimize(
                scenario.graph,
                scenario.source,
                scenario.destination,
            ).path
        ),
        "aco_mmas": lambda: list(
            AntColonyOptimizer(
                replace(aco_config, strategy="mmas"), weights
            ).optimize(
                scenario.graph,
                scenario.source,
                scenario.destination,
            ).path
        ),
    }
    results = []
    for name, select in algorithms.items():
        started = time.perf_counter()
        selected_path = select()
        execution_ms = (
            time.perf_counter() - started
        ) * 1000.0
        latency, utilization, loss = _path_metrics(
            scenario.graph,
            selected_path,
        )
        results.append(
            ExperimentResult(
                scenario=scenario.name,
                algorithm=name,
                path=" -> ".join(selected_path),
                cost=path_cost(
                    scenario.graph,
                    selected_path,
                    weights,
                ),
                latency_ms=latency,
                average_utilization=utilization,
                packet_loss=loss,
                execution_ms=execution_ms,
                seed=aco_config.seed,
            )
        )
    return tuple(results)


def run_experiments(
    output_path: str | Path,
    scenarios: tuple[Scenario, ...] | None = None,
    weights: CostWeights | None = None,
    aco_config: ACOConfig | None = None,
) -> tuple[ExperimentResult, ...]:
    selected_scenarios = scenarios or build_scenarios()
    selected_weights = weights or CostWeights()
    selected_config = aco_config or ACOConfig()
    results = tuple(
        result
        for scenario in selected_scenarios
        for result in evaluate_scenario(
            scenario,
            selected_weights,
            selected_config,
        )
    )
    destination = Path(output_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open(
        "w", newline="", encoding="utf-8"
    ) as output:
        writer = csv.DictWriter(
            output,
            fieldnames=list(asdict(results[0]).keys()),
        )
        writer.writeheader()
        writer.writerows(asdict(result) for result in results)
    return results