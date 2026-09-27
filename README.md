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

Live telemetry and ACO-selected flow installation are the next
milestone. The controller currently floods traffic that does not have a
safe same-switch destination; it does **not** yet install routed paths.

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
stage intentionally does not install end-to-end ACO paths yet.

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

1. periodic port/flow statistics collection
2. conversion of telemetry into normalized link metrics
3. bidirectional ACO-selected OpenFlow rule installation
4. threshold, hysteresis, cooldown, and stale-telemetry handling
5. repeatable experiments with CSV results and baseline comparisons

## Status

Early prototype. Do not deploy in a production network.