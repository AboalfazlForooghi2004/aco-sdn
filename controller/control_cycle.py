from __future__ import annotations

from dataclasses import dataclass

from aco.models import LinkMetrics
from controller.latency import LatencyTracker
from controller.rerouting import FlowRegistry
from controller.telemetry import TelemetryCollector
from controller.telemetry_history import TelemetryHistory
from controller.topology import TopologyManager
from learning.dataset import LearningDataset
from prediction.engine import EdgeForecast, PredictionEngine
from recommendation.engine import (
    Recommendation,
    RecommendationEngine,
)


@dataclass(frozen=True, slots=True)
class CycleAnalysis:
    metrics: dict[tuple[int, int], LinkMetrics]
    forecasts: tuple[EdgeForecast, ...]
    recommendations: tuple[Recommendation, ...]
    settled_learning_outcomes: int


class ControlCycleService:
    """Build one immutable intelligence view from current network state."""

    def __init__(
        self,
        *,
        telemetry: TelemetryCollector,
        latency: LatencyTracker,
        telemetry_history: TelemetryHistory,
        prediction: PredictionEngine,
        recommendation: RecommendationEngine,
        learning_dataset: LearningDataset,
        flow_registry: FlowRegistry,
        optimizer_weights,
    ) -> None:
        self.telemetry = telemetry
        self.latency = latency
        self.telemetry_history = telemetry_history
        self.prediction = prediction
        self.recommendation = recommendation
        self.learning_dataset = learning_dataset
        self.flow_registry = flow_registry
        self.optimizer_weights = optimizer_weights

    def current_metrics(
        self,
        topology: TopologyManager,
        now: float,
    ) -> dict[tuple[int, int], LinkMetrics]:
        return self.latency.enrich(
            self.telemetry.link_metrics(
                topology,
                now=now,
            ),
            now=now,
        )

    def analyze(
        self,
        *,
        topology: TopologyManager,
        monotonic_now: float,
        wall_now: float,
    ) -> CycleAnalysis:
        metrics = self.current_metrics(
            topology, monotonic_now
        )
        self.telemetry_history.append(
            observed_at=wall_now,
            topology_generation=topology.generation,
            metrics=metrics,
        )
        settled = self.learning_dataset.settle_due(
            observed_at=wall_now,
            flows=self.flow_registry.flows,
            topology=topology,
            metrics=metrics,
            weights=self.optimizer_weights,
        )
        self.prediction.observe(metrics, monotonic_now)
        forecasts = self.prediction.forecast_all(
            monotonic_now
        )
        recommendations = self.recommendation.generate(
            forecasts,
            self.flow_registry.flows,
            monotonic_now,
        )
        return CycleAnalysis(
            metrics=metrics,
            forecasts=forecasts,
            recommendations=recommendations,
            settled_learning_outcomes=settled,
        )