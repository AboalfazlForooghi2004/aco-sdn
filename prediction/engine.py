from __future__ import annotations

import math
from collections import defaultdict, deque
from dataclasses import dataclass

from aco.models import LinkMetrics

Edge = tuple[int, int]


@dataclass(frozen=True, slots=True)
class PredictionConfig:
    history_window_seconds: float = 300.0
    max_samples_per_link: int = 180
    horizon_seconds: float = 60.0
    min_samples: int = 5
    min_history_seconds: float = 20.0
    utilization_threshold: float = 0.8
    loss_threshold: float = 0.05
    latency_growth_ratio: float = 0.3

    def __post_init__(self) -> None:
        if self.history_window_seconds <= 0:
            raise ValueError(
                "history_window_seconds must be positive"
            )
        if self.max_samples_per_link < 2:
            raise ValueError(
                "max_samples_per_link must be at least 2"
            )
        if self.horizon_seconds <= 0:
            raise ValueError("horizon_seconds must be positive")
        if self.min_samples < 2:
            raise ValueError("min_samples must be at least 2")
        if self.min_history_seconds <= 0:
            raise ValueError(
                "min_history_seconds must be positive"
            )


@dataclass(frozen=True, slots=True)
class MetricSample:
    observed_at: float
    metrics: LinkMetrics


@dataclass(frozen=True, slots=True)
class EdgeForecast:
    edge: Edge
    generated_at: float
    horizon_seconds: float
    sample_count: int
    available: bool
    current_utilization: float
    predicted_utilization: float
    current_loss: float
    predicted_loss: float
    current_latency_ms: float
    predicted_latency_ms: float
    confidence: float
    risk: str
    signals: tuple[str, ...]
    seconds_to_utilization_threshold: float | None


@dataclass(frozen=True, slots=True)
class _Trend:
    current: float
    predicted: float
    slope_per_second: float
    fit_quality: float


def _linear_trend(
    samples: list[tuple[float, float]],
    horizon_seconds: float,
) -> _Trend:
    origin = samples[0][0]
    x = [timestamp - origin for timestamp, _ in samples]
    y = [value for _, value in samples]
    x_mean = sum(x) / len(x)
    y_mean = sum(y) / len(y)
    denominator = sum(
        (value - x_mean) ** 2 for value in x
    )
    slope = (
        sum(
            (x_value - x_mean) * (y_value - y_mean)
            for x_value, y_value in zip(x, y)
        )
        / denominator
        if denominator > 0
        else 0.0
    )
    intercept = y_mean - slope * x_mean
    fitted = [
        intercept + slope * x_value for x_value in x
    ]
    residual = sum(
        (actual - estimate) ** 2
        for actual, estimate in zip(y, fitted)
    )
    total = sum((actual - y_mean) ** 2 for actual in y)
    fit_quality = (
        1.0 if total <= 1e-12 else max(0.0, 1.0 - residual / total)
    )
    future_x = x[-1] + horizon_seconds
    return _Trend(
        current=y[-1],
        predicted=intercept + slope * future_x,
        slope_per_second=slope,
        fit_quality=fit_quality,
    )


class PredictionEngine:
    """Forecast near-term link health with explainable linear trends."""

    def __init__(self, config: PredictionConfig) -> None:
        self.config = config
        self._history: dict[Edge, deque[MetricSample]] = (
            defaultdict(
                lambda: deque(
                    maxlen=config.max_samples_per_link
                )
            )
        )

    def observe(
        self,
        metrics: dict[Edge, LinkMetrics],
        observed_at: float,
    ) -> None:
        cutoff = (
            observed_at - self.config.history_window_seconds
        )
        for edge, value in metrics.items():
            history = self._history[edge]
            history.append(
                MetricSample(observed_at, value)
            )
            while history and history[0].observed_at < cutoff:
                history.popleft()

    def forecast_all(
        self, now: float
    ) -> tuple[EdgeForecast, ...]:
        return tuple(
            forecast
            for edge in sorted(self._history)
            if (forecast := self.forecast(edge, now))
            is not None
        )

    def forecast(
        self,
        edge: Edge,
        now: float,
    ) -> EdgeForecast | None:
        history = list(self._history.get(edge, ()))
        if not history:
            return None
        latest = history[-1]
        if not latest.metrics.available:
            return EdgeForecast(
                edge=edge,
                generated_at=now,
                horizon_seconds=self.config.horizon_seconds,
                sample_count=len(history),
                available=False,
                current_utilization=latest.metrics.utilization,
                predicted_utilization=latest.metrics.utilization,
                current_loss=latest.metrics.loss,
                predicted_loss=latest.metrics.loss,
                current_latency_ms=latest.metrics.latency_ms,
                predicted_latency_ms=latest.metrics.latency_ms,
                confidence=1.0,
                risk="critical",
                signals=("link_unavailable",),
                seconds_to_utilization_threshold=None,
            )

        usable = [
            sample
            for sample in history
            if sample.metrics.available
        ]
        if len(usable) < 2:
            return None
        utilization = _linear_trend(
            [
                (
                    sample.observed_at,
                    sample.metrics.utilization,
                )
                for sample in usable
            ],
            self.config.horizon_seconds,
        )
        loss = _linear_trend(
            [
                (sample.observed_at, sample.metrics.loss)
                for sample in usable
            ],
            self.config.horizon_seconds,
        )
        latency = _linear_trend(
            [
                (
                    sample.observed_at,
                    sample.metrics.latency_ms,
                )
                for sample in usable
            ],
            self.config.horizon_seconds,
        )
        span = usable[-1].observed_at - usable[0].observed_at
        sample_factor = min(
            1.0, len(usable) / self.config.min_samples
        )
        coverage_factor = min(
            1.0, span / self.config.min_history_seconds
        )
        fit_quality = (
            utilization.fit_quality
            + loss.fit_quality
            + latency.fit_quality
        ) / 3.0
        confidence = max(
            0.0,
            min(
                1.0,
                sample_factor
                * coverage_factor
                * (0.5 + 0.5 * fit_quality),
            ),
        )
        predicted_utilization = min(
            1.0, max(0.0, utilization.predicted)
        )
        predicted_loss = min(1.0, max(0.0, loss.predicted))
        predicted_latency = max(0.0, latency.predicted)
        signals: list[str] = []
        risk = "normal"

        if (
            utilization.current
            >= self.config.utilization_threshold
        ):
            signals.append("active_congestion")
            risk = "critical"
        elif (
            predicted_utilization
            >= self.config.utilization_threshold
        ):
            signals.append("predicted_congestion")
            risk = "warning"
        elif (
            predicted_utilization
            >= self.config.utilization_threshold * 0.9
        ):
            signals.append("utilization_near_threshold")
            risk = "watch"

        if loss.current >= self.config.loss_threshold:
            signals.append("active_packet_loss")
            risk = "critical"
        elif predicted_loss >= self.config.loss_threshold:
            signals.append("predicted_packet_loss")
            if risk != "critical":
                risk = "warning"

        latency_growth = (
            (predicted_latency - latency.current)
            / max(latency.current, 1e-9)
        )
        if (
            latency.current > 0
            and latency_growth
            >= self.config.latency_growth_ratio
        ):
            signals.append("predicted_latency_growth")
            if risk == "normal":
                risk = "warning"

        seconds_to_threshold = None
        if (
            utilization.slope_per_second > 0
            and utilization.current
            < self.config.utilization_threshold
        ):
            seconds_to_threshold = max(
                0.0,
                (
                    self.config.utilization_threshold
                    - utilization.current
                )
                / utilization.slope_per_second,
            )
            if not math.isfinite(seconds_to_threshold):
                seconds_to_threshold = None

        return EdgeForecast(
            edge=edge,
            generated_at=now,
            horizon_seconds=self.config.horizon_seconds,
            sample_count=len(usable),
            available=True,
            current_utilization=utilization.current,
            predicted_utilization=predicted_utilization,
            current_loss=loss.current,
            predicted_loss=predicted_loss,
            current_latency_ms=latency.current,
            predicted_latency_ms=predicted_latency,
            confidence=confidence,
            risk=risk,
            signals=tuple(signals),
            seconds_to_utilization_threshold=(
                seconds_to_threshold
            ),
        )