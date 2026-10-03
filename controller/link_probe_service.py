from __future__ import annotations

from dataclasses import dataclass

from controller.latency import (
    LatencyTracker,
    decode_probe,
    encode_probe,
)
from controller.topology import TopologyManager


@dataclass(frozen=True, slots=True)
class ProbeObservation:
    source_dpid: int
    destination_dpid: int
    accepted: bool
    latency_ms: float | None


class LinkProbeService:
    """Own active-link probe transport and validation."""

    def __init__(self, latency: LatencyTracker) -> None:
        self.latency = latency

    def send(
        self,
        *,
        topology: TopologyManager,
        datapaths: dict[int, object],
        sent_at: float,
    ) -> int:
        sent = 0
        for (source_dpid, _), ports in topology.links.items():
            datapath = datapaths.get(source_dpid)
            if datapath is None:
                continue
            datapath.send_msg(
                datapath.ofproto_parser.OFPPacketOut(
                    datapath=datapath,
                    buffer_id=datapath.ofproto.OFP_NO_BUFFER,
                    in_port=datapath.ofproto.OFPP_CONTROLLER,
                    actions=[
                        datapath.ofproto_parser.OFPActionOutput(
                            ports.source_port
                        )
                    ],
                    data=encode_probe(
                        source_dpid,
                        ports.source_port,
                        sent_at,
                    ),
                )
            )
            sent += 1
        return sent

    def receive(
        self,
        *,
        payload: bytes,
        topology: TopologyManager,
        destination_dpid: int,
        destination_port: int,
        received_at: float,
    ) -> ProbeObservation | None:
        decoded = decode_probe(payload)
        if decoded is None:
            return None
        source_dpid, source_port, sent_at = decoded
        accepted = self.latency.validate_probe_edge(
            topology,
            source_dpid,
            source_port,
            destination_dpid,
            destination_port,
        )
        measured = None
        if accepted:
            measured = self.latency.record_probe(
                source_dpid,
                destination_dpid,
                sent_at,
                received_at,
            )
        return ProbeObservation(
            source_dpid=source_dpid,
            destination_dpid=destination_dpid,
            accepted=accepted,
            latency_ms=measured,
        )
