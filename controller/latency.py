from __future__ import annotations

import struct
from dataclasses import dataclass, replace

from aco.models import LinkMetrics
from controller.topology import TopologyManager

PROBE_ETHERTYPE = 0x88B5
PROBE_DESTINATION = bytes.fromhex("0200000000fe")
PROBE_SOURCE = bytes.fromhex("0200000000fd")
PROBE_MAGIC = b"ACOP"
PROBE_VERSION = 1
_ETHERNET_HEADER = struct.Struct("!6s6sH")
_PROBE_PAYLOAD = struct.Struct("!4sBQIQ")
_ECHO_PAYLOAD = struct.Struct("!d")


@dataclass(frozen=True, slots=True)
class LatencySample:
    latency_ms: float
    observed_at: float


def encode_echo(sent_at: float) -> bytes:
    return _ECHO_PAYLOAD.pack(sent_at)


def decode_echo(data: bytes) -> float | None:
    if len(data) != _ECHO_PAYLOAD.size:
        return None
    return _ECHO_PAYLOAD.unpack(data)[0]


def encode_probe(
    source_dpid: int,
    source_port: int,
    sent_at: float,
) -> bytes:
    sent_ns = int(sent_at * 1_000_000_000)
    return _ETHERNET_HEADER.pack(
        PROBE_DESTINATION,
        PROBE_SOURCE,
        PROBE_ETHERTYPE,
    ) + _PROBE_PAYLOAD.pack(
        PROBE_MAGIC,
        PROBE_VERSION,
        source_dpid,
        source_port,
        sent_ns,
    )


def decode_probe(data: bytes) -> tuple[int, int, float] | None:
    required = _ETHERNET_HEADER.size + _PROBE_PAYLOAD.size
    if len(data) < required:
        return None
    _, _, ethertype = _ETHERNET_HEADER.unpack_from(data)
    if ethertype != PROBE_ETHERTYPE:
        return None
    magic, version, dpid, port, sent_ns = (
        _PROBE_PAYLOAD.unpack_from(
            data, _ETHERNET_HEADER.size
        )
    )
    if magic != PROBE_MAGIC or version != PROBE_VERSION:
        return None
    return dpid, port, sent_ns / 1_000_000_000


class LatencyTracker:
    """Estimate directed link latency using probes and switch RTTs."""

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
        self._switch_rtt: dict[int, LatencySample] = {}
        self._links: dict[tuple[int, int], LatencySample] = {}

    def record_echo(
        self,
        dpid: int,
        sent_at: float,
        received_at: float,
    ) -> float | None:
        rtt_ms = (received_at - sent_at) * 1000.0
        if rtt_ms < 0:
            return None
        self._switch_rtt[dpid] = LatencySample(
            latency_ms=rtt_ms,
            observed_at=received_at,
        )
        return rtt_ms

    def record_probe(
        self,
        source_dpid: int,
        destination_dpid: int,
        sent_at: float,
        received_at: float,
    ) -> float | None:
        source_rtt = self._switch_rtt.get(source_dpid)
        destination_rtt = self._switch_rtt.get(
            destination_dpid
        )
        if source_rtt is None or destination_rtt is None:
            return None
        raw_ms = (received_at - sent_at) * 1000.0
        control_plane_ms = (
            source_rtt.latency_ms
            + destination_rtt.latency_ms
        ) / 2.0
        adjusted_ms = max(raw_ms - control_plane_ms, 0.0)
        edge = (source_dpid, destination_dpid)
        previous = self._links.get(edge)
        smoothed_ms = (
            adjusted_ms
            if previous is None
            else self.ewma_alpha * adjusted_ms
            + (1.0 - self.ewma_alpha)
            * previous.latency_ms
        )
        self._links[edge] = LatencySample(
            latency_ms=smoothed_ms,
            observed_at=received_at,
        )
        return smoothed_ms

    def link_latency(
        self,
        edge: tuple[int, int],
        now: float,
    ) -> float | None:
        sample = self._links.get(edge)
        if sample is None:
            return None
        if now - sample.observed_at > self.max_age_seconds:
            return None
        return sample.latency_ms

    def enrich(
        self,
        metrics: dict[tuple[int, int], LinkMetrics],
        now: float,
    ) -> dict[tuple[int, int], LinkMetrics]:
        enriched = {}
        for edge, link in metrics.items():
            latency_ms = self.link_latency(edge, now)
            enriched[edge] = (
                link
                if latency_ms is None
                else replace(
                    link,
                    latency_ms=latency_ms,
                    confidence=min(link.confidence, 0.7),
                    provenance=(
                        f"{link.provenance}+active_probe"
                    ),
                )
            )
        return enriched

    @staticmethod
    def validate_probe_edge(
        topology: TopologyManager,
        source_dpid: int,
        source_port: int,
        destination_dpid: int,
        destination_port: int,
    ) -> bool:
        ports = topology.links.get(
            (source_dpid, destination_dpid)
        )
        return (
            ports is not None
            and ports.source_port == source_port
            and ports.target_port == destination_port
        )