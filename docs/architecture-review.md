# Senior Architecture Review

## Executive assessment

The project has a sound controller-independent ACO core and a useful
separation between topology, telemetry, routing, rerouting, and
OpenFlow message construction. The next risk is no longer the ACO
algorithm itself. It is operational correctness: state consistency,
data quality, safe changes, and explainability.

## Strengths

- The optimizer is independent from Ryu and is unit-testable.
- Randomness is seeded and the cost model is configurable.
- Missing or stale telemetry has explicit behavior.
- Active flows, cooldown, hysteresis, and fallback are represented.
- Flow rules are planned separately from their OpenFlow encoding.
- Offline baselines prevent unsupported performance claims.

## Priority findings

### P0 — Controller orchestration is too concentrated

`controller/main.py` owns protocol events, topology, probes,
telemetry, prediction, recommendations, routing, flow lifecycle, and
migration. It should become a thin adapter around a domain-level
control loop.

Recommended boundary:

```text
Ryu adapter → Event bus → Network state → Decision service
                                  ↓
                         Change executor
```

### P0 — No transaction acknowledgement

Make-before-break sends FlowMods but does not wait for OpenFlow Barrier
Replies. A failed or delayed switch can therefore leave a partially
installed path. Add route-generation IDs, barrier tracking, timeout,
and rollback.

### P0 — Shared state has no topology generation

Topology may change while ACO is computing or while rules are being
installed. Every decision should reference a topology generation. The
executor must reject a decision if that generation is no longer
current.

### P1 — Telemetry semantics need strengthening

- Link capacity is global rather than per port.
- OpenFlow drop counters are not a complete end-to-end loss signal.
- Missing latency currently leaves a neutral zero value.
- Probe timestamps are controller-clock measurements, not hardware
  timestamps.
- Samples are in memory and disappear on restart.

Add metric provenance, confidence, per-port capacity, and durable
time-series export.

### P1 — Host learning needs security controls

MAC learning from edge ports has no authentication, move rate limit,
or anti-spoof policy. Add trusted edge-port roles, move hold-down, and
optional IP/MAC bindings.

### P1 — Flow identity is too broad

MAC-pair flow identity cannot express QoS or distinguish applications.
Move toward a normalized five-tuple plus tenant and traffic class while
retaining a MAC-only compatibility mode.

### P1 — Prediction must remain advisory first

Forecasts should not directly trigger changes during their initial
deployment. Begin in Observe/Recommend mode, record forecast accuracy,
and permit automatic action only after confidence and false-positive
budgets are met.

### P2 — ACO scalability needs guardrails

For larger topologies, bound the candidate space with K-shortest paths,
cache stable paths, expose execution budgets, and stop early when the
best cost has converged.

### P2 — Southbound coupling

OpenFlow types still leak into orchestration. Introduce a southbound
interface so OpenFlow, P4Runtime, and future gNMI-backed devices share
the same decision model.

## Target architecture

```text
Telemetry providers
  OpenFlow | INT | eBPF | gNMI
             ↓
Network-state store + topology generation
             ↓
Prediction and anomaly detection
             ↓
Recommendation + what-if simulation
             ↓
Routing decision (ACO / baseline)
             ↓
Safety and policy verification
             ↓
Transactional change executor
  FlowMod → Barrier → verify → retire old path
             ↓
Incident timeline and audit log
```

## Implementation sequence

1. Telemetry history, explainable forecasts, confidence, and advisory
   recommendations.
2. Decision and recommendation API for the user interface.
3. Topology generation and transactional Barrier-based execution.
4. Persistent event/decision log and incident replay.
5. Five-tuple flow identity and intent-based policies.
6. P4Runtime/INT and gNMI adapters.
7. Temporal GNN after sufficient real data has been collected.

## Current prediction safety position

The initial predictor uses bounded linear trends rather than a black-box
model. It exposes sample count, confidence, horizon, predicted values,
threshold time, and contributing signals. Recommendations are advisory;
low-confidence forecasts are suppressed and predictive actions are not
automatically applied.

## Reliability hardening status

The following review findings are now implemented:

- topology-generation validation before rule installation;
- two-phase install/retire Barrier transactions;
- Barrier-verified rollback with timeout handling;
- generation-aware application cookies;
- persistent transaction-state journal;
- conservative managed-rule purge when a switch reconnects, allowing
  clean controller restart recovery;
- rule-level expiry accounting before removing an active flow;
- per-port configured capacity override ahead of OpenFlow-reported
  speed;
- freshness-derived telemetry confidence and explicit unknown-latency
  penalty;
- five-tuple-aware flow identity and demand estimation;
- recovery of pending learning outcomes after controller restart;
- Python 3.10/3.11 CI with compilation of every project package.

The primary remaining structural item is decomposition of the Ryu
application into a thinner protocol adapter and independently testable
control-loop services. Hardware/OVS failure-injection tests are also
required before treating the transaction executor as production-ready.

Route planning, pending-change ownership, transaction completion, and
registry commit have since moved into `RouteChangeService`. A live
failure-injection runner now exercises active-link failure,
active-switch failure, and controller disconnection in Mininet/OVS.
Telemetry history, prediction, recommendations, and learning-outcome
settlement now run in `ControlCycleService`. Initial installation,
host-move cleanup, and rule-expiry accounting now run in
`PacketFlowService`. The remaining decomposition target is the Ryu
protocol-event adapter and recommendation/proposal audit logging still
hosted by `main.py`.