# ACO-SDN Traffic Optimizer

An experimental SDN traffic-engineering controller that uses Ant
Colony Optimization (ACO) to select paths from live link telemetry and
installs them through OpenFlow 1.3.

## Current milestone

The first two milestones deliver a controller-independent routing core
and the initial Ryu discovery adapter:

- directed graph with validated latency, utilization, loss, and
  availability metrics;
- normalized multi-criteria cost function;
- seeded, loop-free ACO optimizer with pheromone evaporation/deposit;
- deterministic minimum-cost fallback;
- six-switch multipath Mininet topology;
- unit tests for congestion-aware selection and failed-link exclusion.
- Ryu switch/link discovery and OpenFlow 1.3 table-miss setup;
- host learning with attachment-point move detection and cleanup.

Port telemetry is collected periodically and converted into
utilization/loss metrics. The controller now selects an ACO path and
installs bidirectional OpenFlow 1.3 rules for learned hosts. If fresh
telemetry cannot produce a path, it uses a deterministic minimum-hop
fallback. Active flows are monitored and migrated when configured
utilization/loss thresholds are crossed.

## Layout

```text
aco/          Algorithm, graph model, and cost function
config/       Reproducible ACO, cost, and rerouting settings
controller/   Ryu/OpenFlow adapter (next milestone)
topology/     Mininet lab topology
experiments/  Reproducible scenario runners
tests/        Unit tests
```

## Run core tests

```bash
python -m unittest discover -s tests -v
```

## Run the Mininet topology

On an Ubuntu host with Mininet and Open vSwitch installed:

```bash
sudo python topology/mininet_topology.py
```

The topology expects an OpenFlow controller on `127.0.0.1:6653`.

## Run discovery controller

On the Ubuntu lab host, install dependencies and start Ryu with link
observation enabled:

```bash
python -m pip install -r requirements-lab.txt
ryu-manager --observe-links controller/main.py
```

In another terminal:

```bash
sudo python topology/mininet_topology.py
```

Ryu logs switch/link events and learned host attachment points. This
stage also polls OpenFlow port counters, calculates byte rates,
utilization and transmit-drop loss, and rejects stale or reset counter
series. Once both hosts are learned, it installs end-to-end rules for
the selected path. MAC addresses observed on inter-switch ports are not
treated as directly attached hosts.

Dynamic migration uses the thresholds in `config/config.yaml`.
Non-failure changes must pass both the cooldown and minimum-improvement
checks. Link unavailability bypasses cooldown. Replacement rules are
installed before old-only path rules are deleted; rules on shared
switches are modified in place.

## Cost model

```text
cost = w_latency * normalized_latency
     + w_utilization * utilization
     + w_loss * loss
     + w_hop
```

Weights and normalization references are kept in
`config/config.yaml`. Metrics use ratios in `0..1`; latency is in
milliseconds.

## Roadmap

1. latency measurement and flow-stat correlation
2. explicit hysteresis state around rerouting thresholds
3. flow-removed lifecycle and host-move cleanup
4. repeatable experiments with CSV results and baseline comparisons

## Status

Early prototype. Do not deploy in a production network.