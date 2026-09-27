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
- seeded, loop-free ACO optimizer with selectable Ant System or
  Max-Min Ant System (MMAS);
- bounded MMAS pheromones, best-so-far reinforcement, stagnation
  detection, and deterministic restart/early-stop diagnostics;
- deterministic minimum-cost fallback;
- six-switch multipath Mininet topology;
- unit tests for congestion-aware selection and failed-link exclusion.
- Ryu switch/link discovery and OpenFlow 1.3 table-miss setup;
- host learning with attachment-point move detection and cleanup.
- topology-generation validation and two-phase OpenFlow Barrier
  transactions with timeout rollback.
- per-port capacity discovery from OpenFlow Port Description,
  metric provenance/confidence, and host-move hold-down controls.
- normalized bidirectional L2/L3/L4 flow identity with TCP/UDP
  five-tuple OpenFlow matches and MAC-only compatibility.
- interval-limited durable telemetry history with topology generation,
  metric provenance, confidence, and bounded compaction.

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
learning/     Versioned decision dataset and safe shadow environment
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

Congestion state uses separate entry and exit levels. A link that
crosses a threshold remains latched until utilization and loss fall
below their configured hysteresis clear levels. Installed rules request
OpenFlow removal notifications. Idle/hard timeout events remove the
corresponding active-flow record, while controller-initiated deletion
events are ignored. When a host moves, all tracked paths involving its
MAC address are explicitly removed before a new route is learned.

## Active link-latency telemetry

On each telemetry cycle, the controller sends:

1. an OpenFlow Echo request to estimate the controller RTT for every
   connected switch;
2. a small experimental-EtherType probe over each discovered directed
   link.

The raw probe duration contains controller-to-switch and
switch-to-controller delay. Half of each endpoint's measured Echo RTT
is subtracted before the result is stored. Link samples use a
configurable EWMA and expire with the same telemetry maximum age.
Received probes are accepted only when their source/destination ports
match an existing discovered link. Fresh values populate
`LinkMetrics.latency_ms` and therefore participate in ACO cost.

This is a lab estimator rather than hardware timestamping; results can
include scheduling, OpenFlow channel, and Packet-In processing noise.

## Offline reproducible experiments

The offline runner compares four algorithms on the same six-switch
graph:

- minimum-hop shortest path;
- deterministic minimum dynamic cost;
- classic seeded Ant System;
- seeded Max-Min Ant System (MMAS).

Included scenarios are normal operation, congestion, increased
latency, packet loss, and link failure. Run:

```bash
python -m experiments.run \
  --output results/offline_comparison.csv
```

The CSV contains the selected path, normalized path cost, accumulated
latency, average utilization, end-to-end packet-loss estimate,
algorithm execution time, and random seed. These are synthetic
repeatable inputs for validating decision logic; they are not Mininet
performance measurements.

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

1. Mininet congestion and link-failure scenario automation
2. integration validation on Ubuntu/Mininet/OVS
3. measured CSV export from live controller telemetry
4. charts generated from measured experiment CSV files

## Status

Early prototype. Do not deploy in a production network.

## Provisioning and live validation

For the current single-host Ubuntu lab, Ansible is a better fit than
Terraform. The included playbook installs Mininet, Open vSwitch,
iperf3, Python dependencies, clones the repository, and executes the
test suite:

```bash
cp deploy/ansible/inventory.example.ini \
  deploy/ansible/inventory.ini
ansible-playbook \
  -i deploy/ansible/inventory.ini \
  deploy/ansible/playbook.yml
```

Terraform becomes useful when the project needs to create cloud VMs,
security groups, virtual networks, or a repeatable multi-host lab.

Useful commands:

```bash
make test
make offline
make preflight
make controller
make topology
make live
make clean
```

The live topology uses `h1` and `h2` for measurement. `h3` and `h4`
generate independent background traffic over the upper path. The live
runner applies congestion, delay, loss, and link failure, then writes
ping loss/latency and iperf3 throughput to
`results/live_comparison.csv`.

GitHub CI runs the full unit suite, a simulated OpenFlow integration
test, and deterministic offline scenarios. The integration simulation
validates ACO rerouting, make-before-break ordering, FlowMod creation,
and strict deletion without requiring root or OVS. Run
`make preflight` on the Ubuntu host before live tests.

## Offline learning safety boundary

The controller records versioned routing decisions and delayed outcomes in
an append-only JSONL dataset. `learning/` provides a one-step,
Gymnasium-style offline environment and shadow evaluator that cannot access
OpenFlow datapaths. Run
`python scripts/evaluate_shadow.py data/routing-learning.jsonl` to compare
the built-in keep-path and greedy-safe shadow policies. See
`docs/offline-learning.md`.

Before training, run
`python scripts/inspect_dataset.py data/routing-learning.jsonl`. A stable
graph encoder produces GNN-ready node, edge, and global features, while a
promotion gate blocks policies with insufficient coverage, unsafe actions,
or no reward improvement.

## GitHub Codespaces

The repository includes a privileged Ubuntu-based dev container with
Mininet, Open vSwitch, iproute2, iperf3, Ansible, Python 3.10, and Ryu
dependencies. Codespace creation runs the unit suite, offline
experiments, starts OVS when supported, and prints the live-lab
preflight result. The actual Mininet run still depends on the
capabilities exposed by the Codespaces host.

## Predictive health and recommendations

The controller now keeps a bounded in-memory history for every
directed link and produces explainable near-term forecasts for
utilization, loss, and latency. Each forecast includes:

- prediction horizon and sample count;
- current and predicted values;
- model-fit and history-based confidence;
- time to the utilization threshold when calculable;
- explicit signals such as predicted congestion, packet loss, latency
  growth, or link unavailability.

The first model is intentionally a bounded linear trend rather than a
black box. Low-confidence forecasts are suppressed by the
recommendation layer. Accepted risks produce advisory actions naming
the affected link and tracked flows, urgency, validity window, and
whether policy could eventually permit automatic application. Forecasts
do not directly change the network in this phase.

The senior technical review and target architecture are documented in
`docs/architecture-review.md`.

## Operating modes and read-only API

`config/config.yaml` defines the safe operating mode:

```yaml
control:
  mode: recommend
```

- `observe`: collect, forecast, and explain without route proposals.
- `recommend`: calculate and expose migration proposals without
  applying them.
- `autopilot`: execute threshold-based migrations as before.

The default is `recommend`. A dependency-free read-only API listens on
`127.0.0.1:8080` by default:

```text
GET /api/v1/health
GET /api/v1/topology
GET /api/v1/metrics
GET /api/v1/forecasts
GET /api/v1/recommendations
GET /api/v1/flows
GET /api/v1/migrations
GET /api/v1/events
GET /api/v1/snapshot
```

The immutable snapshot includes a `0..100` network health score and
lets a future dashboard read controller state without importing Ryu or
touching mutable protocol objects. No endpoint can modify the network.

## Incident and decision timeline

Controller lifecycle, recommendations, migration proposals, successful
migrations, and failures are appended to a bounded JSONL event log.
Events survive controller restarts and are also exposed through the
read-only snapshot API. Every event has an ID, wall-clock timestamp,
category, severity, title, and structured details suitable for replay
or automated incident reports.

## What-if route simulation

Every migration proposal now carries a non-mutating comparison of the
current and proposed paths:

- topology validity and loop detection;
- link existence and availability;
- hop count and normalized path cost;
- accumulated latency and end-to-end loss estimate;
- average and maximum observed utilization;
- before/after deltas and cost-improvement ratio.

Autopilot blocks a migration when the proposed path fails simulation.
When no fresh FlowStats estimate exists, the result explicitly warns
that flow bandwidth was not modeled. Every result also identifies its
telemetry as a point-in-time observation, preventing the UI from
presenting estimated benefits as guaranteed outcomes.

## Flow-demand-aware simulation

The controller polls OpenFlow FlowStats and estimates directional flow
bit/packet rates from ingress-switch counter deltas. An EWMA smooths
short spikes, counter resets invalidate the estimate, and stale demand
is not used.

When a fresh estimate exists, What-if simulation projects the flow's
load onto links that are new to the proposed route. Autopilot blocks a
candidate whose projected maximum utilization exceeds the configured
safety limit. The current MVP assumes a global link capacity and says
so explicitly in the simulation warnings; per-port capacity is a future
improvement.