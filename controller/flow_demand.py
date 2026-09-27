from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class FlowCounters:
    byte_count: int
    packet_count: int
    observed_at: float


@dataclass(frozen=True, slots=True)
class FlowDemand:
    bits_per_second: float
    packets_per_second: float
    observed_at: float


class FlowDemandEstimator:
    """Estimate directional flow demand from ingress FlowStats."""

    def __init__(
        self,
        ewma_alpha: float,
        max_age_seconds: float,
    ) -> None:
        if not 0.0 < ewma_alpha <= 1.0:
            raise ValueError("ewma_alpha must be in (0, 1]")
        if max_age_seconds <= 0:
            raise ValueError("max_age_seconds must be positive")
        self.ewma_alpha = ewma_alpha
        self.max_age_seconds = max_age_seconds
        self._previous: dict[
            tuple[str, str], FlowCounters
        ] = {}
        self._demand: dict[tuple[str, str], FlowDemand] = {}

    @staticmethod
    def _key(
        source_mac: str,
        destination_mac: str,
    ) -> tuple[str, str]:
        return source_mac.lower(), destination_mac.lower()

    def update(
        self,
        source_mac: str,
        destination_mac: str,
        counters: FlowCounters,
    ) -> FlowDemand | None:
        key = self._key(source_mac, destination_mac)
        previous = self._previous.get(key)
        self._previous[key] = counters
        if previous is None:
            return None
        elapsed = counters.observed_at - previous.observed_at
        byte_delta = counters.byte_count - previous.byte_count
        packet_delta = (
            counters.packet_count - previous.packet_count
        )
        if elapsed <= 0 or byte_delta < 0 or packet_delta < 0:
            self._demand.pop(key, None)
            return None
        measured_bps = byte_delta * 8.0 / elapsed
        measured_pps = packet_delta / elapsed
        old = self._demand.get(key)
        demand = FlowDemand(
            bits_per_second=(
                measured_bps
                if old is None
                else self.ewma_alpha * measured_bps
                + (1.0 - self.ewma_alpha)
                * old.bits_per_second
            ),
            packets_per_second=(
                measured_pps
                if old is None
                else self.ewma_alpha * measured_pps
                + (1.0 - self.ewma_alpha)
                * old.packets_per_second
            ),
            observed_at=counters.observed_at,
        )
        self._demand[key] = demand
        return demand

    def get(
        self,
        source_mac: str,
        destination_mac: str,
        now: float,
    ) -> FlowDemand | None:
        demand = self._demand.get(
            self._key(source_mac, destination_mac)
        )
        if demand is None:
            return None
        if now - demand.observed_at > self.max_age_seconds:
            return None
        return demand