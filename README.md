# ACO-SDN Traffic Optimizer

An experimental SDN traffic-engineering controller that uses Ant
Colony Optimization (ACO) to select paths from live link telemetry and
installs them through OpenFlow 1.3.

## Current milestone

The first milestone delivers a controller-independent routing core:

- directed graph with validated latency, utilization, loss, and
  availability metrics;
- normalized multi-criteria cost function;
- seeded, loop-free ACO optimizer with pheromone evaporation/deposit;
- deterministic minimum-cost fallback;
- six-switch multipath Mininet topology;
- unit tests for congestion-aware selection and failed-link exclusion.

Ryu integration and live OpenFlow telemetry are the next milestone;
this repository does **not** yet control OVS flows.

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

1. Ryu topology discovery and host learning
2. periodic port/flow statistics collection
3. bidirectional OpenFlow rule installation
4. threshold, hysteresis, cooldown, and stale-telemetry handling
5. repeatable experiments with CSV results and baseline comparisons

## Status

Early prototype. Do not deploy in a production network.