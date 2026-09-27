from __future__ import annotations

from dataclasses import dataclass

from controller.rerouting import ActiveFlow
from prediction.engine import EdgeForecast


@dataclass(frozen=True, slots=True)
class Recommendation:
    recommendation_id: str
    category: str
    title: str
    rationale: tuple[str, ...]
    action_type: str
    confidence: float
    urgency: str
    affected_flows: tuple[tuple[str, str], ...]
    valid_until: float
    auto_apply_allowed: bool


class RecommendationEngine:
    """Turn forecasts into ranked, conservative operator actions."""

    def __init__(
        self,
        minimum_confidence: float,
        validity_seconds: float,
    ) -> None:
        if not 0.0 <= minimum_confidence <= 1.0:
            raise ValueError(
                "minimum_confidence must be between 0 and 1"
            )
        if validity_seconds <= 0:
            raise ValueError(
                "validity_seconds must be positive"
            )
        self.minimum_confidence = minimum_confidence
        self.validity_seconds = validity_seconds

    def generate(
        self,
        forecasts: tuple[EdgeForecast, ...],
        flows: tuple[ActiveFlow, ...],
        now: float,
    ) -> tuple[Recommendation, ...]:
        recommendations = []
        for forecast in forecasts:
            if forecast.risk not in {"warning", "critical"}:
                continue
            if (
                forecast.available
                and forecast.confidence
                < self.minimum_confidence
            ):
                continue
            affected = tuple(
                flow.key
                for flow in flows
                if forecast.edge
                in set(zip(flow.path, flow.path[1:]))
            )
            source, target = forecast.edge
            immediate = (
                not forecast.available
                or "active_congestion" in forecast.signals
                or "active_packet_loss" in forecast.signals
            )
            recommendations.append(
                Recommendation(
                    recommendation_id=(
                        f"edge-{source}-{target}-"
                        f"{'immediate' if immediate else 'predictive'}"
                    ),
                    category=(
                        "immediate_action"
                        if immediate
                        else "preventive_action"
                    ),
                    title=(
                        f"Evaluate rerouting traffic away from "
                        f"{source} → {target}"
                    ),
                    rationale=forecast.signals,
                    action_type="simulate_reroute",
                    confidence=forecast.confidence,
                    urgency=(
                        "high" if immediate else "medium"
                    ),
                    affected_flows=affected,
                    valid_until=now + self.validity_seconds,
                    auto_apply_allowed=(
                        immediate
                        and forecast.confidence >= 0.9
                    ),
                )
            )
        urgency_order = {"high": 0, "medium": 1, "low": 2}
        recommendations.sort(
            key=lambda item: (
                urgency_order[item.urgency],
                -item.confidence,
                item.recommendation_id,
            )
        )
        return tuple(recommendations)