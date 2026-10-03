from __future__ import annotations

import argparse
import csv
import os
import re
import time
from dataclasses import asdict, dataclass
from pathlib import Path

from experiments.live_runner import parse_ping
from topology.lab import build_network, switch_link

OUTPUT_PORT = re.compile(r"actions=output:(\d+)")


@dataclass(frozen=True, slots=True)
class FailureResult:
    scenario: str
    fault_target: str
    recovered: bool
    recovery_seconds: float | None
    packet_loss_percent: float | None
    active_branch: str | None


def parse_output_ports(flow_dump: str) -> tuple[int, ...]:
    return tuple(
        sorted(
            {
                int(match.group(1))
                for match in OUTPUT_PORT.finditer(flow_dump)
            }
        )
    )


def _is_reachable(host, destination: str) -> bool:
    _, loss = parse_ping(
        host.cmd(f"ping -c 1 -W 1 {destination}")
    )
    return loss == 0.0


def _wait_for_recovery(
    host,
    destination: str,
    timeout_seconds: float,
    poll_seconds: float = 0.2,
) -> tuple[bool, float | None]:
    started = time.monotonic()
    while time.monotonic() - started < timeout_seconds:
        if _is_reachable(host, destination):
            return True, time.monotonic() - started
        time.sleep(poll_seconds)
    return False, None


def _s1_branch(net) -> str | None:
    dump = net["s1"].cmd(
        "ovs-ofctl -O OpenFlow13 dump-flows s1"
    )
    ports = parse_output_ports(dump)
    for branch in ("s2", "s3"):
        link = switch_link(net, "s1", branch)
        interface = (
            link.intf1
            if link.intf1.node.name == "s1"
            else link.intf2
        )
        if int(interface.node.ports[interface]) in ports:
            return branch
    return None


def _measure_after_fault(
    net,
    scenario: str,
    target: str,
    timeout_seconds: float,
) -> FailureResult:
    host = net["h1"]
    recovered, recovery_seconds = _wait_for_recovery(
        host, "10.0.0.2", timeout_seconds
    )
    output = host.cmd(
        "ping -c 10 -i 0.2 -W 1 10.0.0.2"
    )
    _, loss = parse_ping(output)
    return FailureResult(
        scenario=scenario,
        fault_target=target,
        recovered=recovered,
        recovery_seconds=recovery_seconds,
        packet_loss_percent=loss,
        active_branch=_s1_branch(net),
    )


def run_failure_injection(
    output_path: str | Path,
    controller_ip: str,
    controller_port: int,
    settle_seconds: float,
    timeout_seconds: float,
) -> tuple[FailureResult, ...]:
    if os.geteuid() != 0:
        raise PermissionError(
            "failure injection must run as root"
        )
    net = build_network(controller_ip, controller_port)
    results = []
    try:
        net.start()
        net.pingAll(timeout="1")
        time.sleep(settle_seconds)

        branch = _s1_branch(net) or "s2"
        failed_link = (
            ("s2", "s4")
            if branch == "s2"
            else ("s3", "s5")
        )
        net.configLinkStatus(*failed_link, "down")
        results.append(
            _measure_after_fault(
                net,
                "active_link_failure",
                "-".join(failed_link),
                timeout_seconds,
            )
        )
        net.configLinkStatus(*failed_link, "up")
        time.sleep(settle_seconds)

        branch = _s1_branch(net) or "s2"
        switch = net[branch]
        switch.stop(deleteIntfs=False)
        results.append(
            _measure_after_fault(
                net,
                "active_switch_failure",
                branch,
                timeout_seconds,
            )
        )
        switch.start(net.controllers)
        time.sleep(settle_seconds)

        for item in net.switches:
            item.cmd("ovs-vsctl del-controller", item.name)
        time.sleep(2)
        results.append(
            _measure_after_fault(
                net,
                "controller_disconnect",
                "all-switches",
                timeout_seconds,
            )
        )
        for item in net.switches:
            item.cmd(
                "ovs-vsctl set-controller",
                item.name,
                f"tcp:{controller_ip}:{controller_port}",
            )
    finally:
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
        description=(
            "Inject active link, switch, and controller faults"
        )
    )
    parser.add_argument(
        "--output",
        default="results/failure_injection.csv",
    )
    parser.add_argument("--controller-ip", default="127.0.0.1")
    parser.add_argument(
        "--controller-port", type=int, default=6653
    )
    parser.add_argument(
        "--settle-seconds", type=float, default=8
    )
    parser.add_argument(
        "--timeout-seconds", type=float, default=15
    )
    arguments = parser.parse_args()
    results = run_failure_injection(
        arguments.output,
        arguments.controller_ip,
        arguments.controller_port,
        arguments.settle_seconds,
        arguments.timeout_seconds,
    )
    failed = [item for item in results if not item.recovered]
    print(
        f"wrote {len(results)} scenarios; "
        f"unrecovered={len(failed)}"
    )
    raise SystemExit(1 if failed else 0)


if __name__ == "__main__":
    main()