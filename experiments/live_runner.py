from __future__ import annotations

import argparse
import csv
import json
import os
import re
import time
from dataclasses import asdict, dataclass
from pathlib import Path

from topology.lab import build_network, switch_link


@dataclass(frozen=True, slots=True)
class LiveResult:
    scenario: str
    ping_average_ms: float | None
    packet_loss_percent: float | None
    throughput_mbps: float | None
    measured_at: str


PING_LOSS = re.compile(r"([0-9.]+)% packet loss")
PING_RTT = re.compile(
    r"(?:rtt|round-trip) min/avg/max/(?:mdev|stddev) = "
    r"[0-9.]+/([0-9.]+)/"
)


def parse_ping(
    output: str,
) -> tuple[float | None, float | None]:
    loss_match = PING_LOSS.search(output)
    rtt_match = PING_RTT.search(output)
    average = (
        float(rtt_match.group(1)) if rtt_match else None
    )
    loss = (
        float(loss_match.group(1)) if loss_match else None
    )
    return average, loss


def parse_iperf3(output: str) -> float | None:
    try:
        document = json.loads(output)
        bits_per_second = document["end"]["sum_received"][
            "bits_per_second"
        ]
    except (json.JSONDecodeError, KeyError, TypeError):
        return None
    return float(bits_per_second) / 1_000_000


def _configure_link(
    net,
    left: str,
    right: str,
    *,
    bandwidth_mbps: int = 100,
    delay: str = "2ms",
    loss_percent: float = 0,
) -> None:
    link = switch_link(net, left, right)
    for interface in (link.intf1, link.intf2):
        interface.config(
            bw=bandwidth_mbps,
            delay=delay,
            loss=loss_percent,
        )


def _reset_upper_link(net) -> None:
    net.configLinkStatus("s2", "s4", "up")
    _configure_link(net, "s2", "s4")


def _apply_scenario(net, name: str) -> None:
    _reset_upper_link(net)
    net["h3"].cmd("pkill -f 'iperf3.*5202' || true")
    net["h4"].cmd("pkill -f 'iperf3.*5202' || true")
    if name == "normal":
        return
    if name == "congestion":
        net["h4"].cmd("iperf3 -s -D -p 5202")
        net["h3"].cmd(
            "iperf3 -c 10.0.0.4 -p 5202 -u -b 95M "
            "-t 60 >/tmp/aco-sdn-background.log 2>&1 &"
        )
        return
    if name == "latency":
        _configure_link(net, "s2", "s4", delay="50ms")
        return
    if name == "packet_loss":
        _configure_link(
            net, "s2", "s4", loss_percent=8
        )
        return
    if name == "link_failure":
        net.configLinkStatus("s2", "s4", "down")
        return
    raise ValueError(f"unknown scenario: {name}")


def _measure(net, scenario: str) -> LiveResult:
    h1, h2 = net["h1"], net["h2"]
    h2.cmd("pkill -f 'iperf3.*5201' || true")
    h2.cmd("iperf3 -s -D -p 5201")
    ping_output = h1.cmd(
        "ping -c 10 -i 0.2 -W 2 10.0.0.2"
    )
    iperf_output = h1.cmd(
        "iperf3 -c 10.0.0.2 -p 5201 -t 5 -J"
    )
    average, loss = parse_ping(ping_output)
    return LiveResult(
        scenario=scenario,
        ping_average_ms=average,
        packet_loss_percent=loss,
        throughput_mbps=parse_iperf3(iperf_output),
        measured_at=time.strftime(
            "%Y-%m-%dT%H:%M:%SZ", time.gmtime()
        ),
    )


def run_live(
    output_path: str | Path,
    controller_ip: str,
    controller_port: int,
    settle_seconds: float,
) -> tuple[LiveResult, ...]:
    if os.geteuid() != 0:
        raise PermissionError(
            "live Mininet experiments must run as root"
        )
    net = build_network(controller_ip, controller_port)
    results = []
    try:
        net.start()
        net.pingAll(timeout="1")
        for scenario in (
            "normal",
            "congestion",
            "latency",
            "packet_loss",
            "link_failure",
        ):
            _apply_scenario(net, scenario)
            time.sleep(settle_seconds)
            results.append(_measure(net, scenario))
    finally:
        for host in getattr(net, "hosts", []):
            host.cmd("pkill iperf3 || true")
        net.stop()

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
    return tuple(results)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run live Mininet ACO-SDN scenarios"
    )
    parser.add_argument(
        "--output",
        default="results/live_comparison.csv",
    )
    parser.add_argument("--controller-ip", default="127.0.0.1")
    parser.add_argument(
        "--controller-port", type=int, default=6653
    )
    parser.add_argument(
        "--settle-seconds", type=float, default=12
    )
    args = parser.parse_args()
    results = run_live(
        args.output,
        args.controller_ip,
        args.controller_port,
        args.settle_seconds,
    )
    print(f"wrote {len(results)} rows to {args.output}")


if __name__ == "__main__":
    main()