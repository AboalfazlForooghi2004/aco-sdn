from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

from aco.cost import CostWeights
from aco.optimizer import ACOConfig, AntColonyOptimizer
from controller.rerouting import ReroutePolicy


@dataclass(frozen=True, slots=True)
class TelemetrySettings:
    poll_interval_seconds: float
    link_capacity_bps: float
    max_age_seconds: float


def _config_path(path: str | Path | None) -> Path:
    return (
        Path(path)
        if path is not None
        else Path(__file__).resolve().parents[1]
        / "config"
        / "config.yaml"
    )


def _load_document(path: str | Path | None) -> dict:
    with _config_path(path).open(encoding="utf-8") as config_file:
        return yaml.safe_load(config_file)


def load_telemetry_settings(
    path: str | Path | None = None,
) -> TelemetrySettings:
    document = _load_document(path)
    telemetry = document["telemetry"]
    return TelemetrySettings(
        poll_interval_seconds=float(
            telemetry["poll_interval_seconds"]
        ),
        link_capacity_bps=float(telemetry["link_capacity_bps"]),
        max_age_seconds=float(telemetry["max_age_seconds"]),
    )


def load_optimizer(
    path: str | Path | None = None,
) -> AntColonyOptimizer:
    document = _load_document(path)
    aco = document["aco"]
    cost = document["cost"]
    return AntColonyOptimizer(
        ACOConfig(
            alpha=float(aco["alpha"]),
            beta=float(aco["beta"]),
            evaporation=float(aco["evaporation"]),
            ants=int(aco["ants"]),
            iterations=int(aco["iterations"]),
            pheromone_initial=float(aco["pheromone_initial"]),
            deposit_q=float(aco["deposit_q"]),
            seed=int(aco["seed"]),
        ),
        CostWeights(
            latency=float(cost["latency"]),
            utilization=float(cost["utilization"]),
            loss=float(cost["loss"]),
            hop=float(cost["hop"]),
            latency_reference_ms=float(
                cost["latency_reference_ms"]
            ),
        ),
    )


def load_reroute_policy(
    path: str | Path | None = None,
) -> ReroutePolicy:
    rerouting = _load_document(path)["rerouting"]
    return ReroutePolicy(
        utilization_threshold=float(
            rerouting["utilization_threshold"]
        ),
        utilization_hysteresis=float(
            rerouting["utilization_hysteresis"]
        ),
        loss_threshold=float(rerouting["loss_threshold"]),
        loss_hysteresis=float(
            rerouting["loss_hysteresis"]
        ),
        minimum_improvement=float(
            rerouting["minimum_improvement"]
        ),
        cooldown_seconds=float(rerouting["cooldown_seconds"]),
    )