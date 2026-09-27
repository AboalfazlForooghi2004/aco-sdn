from __future__ import annotations

import time
from dataclasses import dataclass

from aco.models import LinkMetrics
from controller.topology import TopologyManager


@dataclass(frozen=True, slots=True)
class PortCounters:
    """Raw monotonically increasing OpenFlow port counters."""

    rx_bytes: int
    tx_bytes: int
    rx_packets: int
    tx_packets: int
    rx_dropped: int
    tx_dropped: int
    observed_at: float


@dataclass(frozen=True, slots=True)
class PortTelemetry:
    """Rates and quality indicators derived from two counter samples."""

    rx_bps: float
    tx_bps: float
    utilization: float
    loss: float
    observed_at: float


class TelemetryCollector:
    """Convert OpenFlow counters into fresh normalized link metrics."""

    def __init__(
        self,
        link_capacity_bps: float,
        max_age_seconds: float,
    ) -> None:
        if link_capacity_bps <= 0:
            raise ValueError("link_capacity_bps must be positive")
        if max_age_seconds <= 0:
            raise ValueError("max_age_seconds must be positive")
        self.link_capacity_bps = link_capacity_bps
        self.max_age_seconds = max_age_seconds
        self._previous: dict[tuple[int, int], PortCounters] = {}
        self._telemetry: dict[tuple[int, int], PortTelemetry] = {}

    def update(
        self,
        dpid: int,
        port: int,
        counters: PortCounters,
    ) -> PortTelemetry | None:
        """Store a sample and return a rate after a valid second sample."""
        key = (dpid, port)
        previous = self._previous.get(key)
        self._previous[key] = counters
        if previous is None:
            return None

        elapsed = counters.observed_at - previous.observed_at
        current_values = (
            counters.rx_bytes,
            counters.tx_bytes,
            counters.rx_packets,
            counters.tx_packets,
            counters.rx_dropped,
            counters.tx_dropped,
        )
        previous_values = (
            previous.rx_bytes,
            previous.tx_bytes,
            previous.rx_packets,
            previous.tx_packets,
            previous.rx_dropped,
            previous.tx_dropped,
        )
        if elapsed <= 0 or any(
            current < old
            for current, old in zip(
                current_values, previous_values
            )
        ):
            self._telemetry.pop(key, None)
            return None

        rx_bytes = counters.rx_bytes - previous.rx_bytes
        tx_bytes = counters.tx_bytes - previous.tx_bytes
        tx_packets = counters.tx_packets - previous.tx_packets
        tx_dropped = counters.tx_dropped - previous.tx_dropped
        rx_bps = rx_bytes * 8.0 / elapsed
        tx_bps = tx_bytes * 8.0 / elapsed
        utilization = min(
            max(rx_bps, tx_bps) / self.link_capacity_bps,
            1.0,
        )
        attempted_packets = tx_packets + tx_dropped
        loss = (
            tx_dropped / attempted_packets
            if attempted_packets > 0
            else 0.0
        )
        result = PortTelemetry(
            rx_bps=rx_bps,
            tx_bps=tx_bps,
            utilization=utilization,
            loss=min(loss, 1.0),
            observed_at=counters.observed_at,
        )
        self._telemetry[key] = result
        return result

    def port_telemetry(
        self,
        dpid: int,
        port: int,
        now: float | None = None,
    ) -> PortTelemetry | None:
        result = self._telemetry.get((dpid, port))
        if result is None:
            return None
        current_time = time.monotonic() if now is None else now
        if current_time - result.observed_at > self.max_age_seconds:
            return None
        return result

    def link_metrics(
        self,
        topology: TopologyManager,
        now: float | None = None,
    ) -> dict[tuple[int, int], LinkMetrics]:
        """Map every directed topology link to source-port metrics."""
        current_time = time.monotonic() if now is None else now
        result: dict[tuple[int, int], LinkMetrics] = {}
        for edge, ports in topology.links.items():
            telemetry = self.port_telemetry(
                edge[0],
                ports.source_port,
                now=current_time,
            )
            if telemetry is None:
                result[edge] = LinkMetrics(available=False)
            else:
                result[edge] = LinkMetrics(
                    utilization=telemetry.utilization,
                    loss=telemetry.loss,
                    available=True,
                )
        return result