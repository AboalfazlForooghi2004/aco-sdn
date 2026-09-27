# Offline learning and shadow evaluation

The learning pipeline is deliberately isolated from OpenFlow execution.
It cannot install, modify, or delete a forwarding rule.

## Dataset

When a migration proposal is created, the controller appends a versioned
`decision` record to `data/routing-learning.jsonl`. The record includes:

- flow endpoints and current/candidate paths;
- current and candidate costs;
- selected optimizer and controller mode;
- What-if safety result;
- a sorted snapshot of all link features.

After the configured outcome horizon, an `outcome` record is appended with:

- whether the flow still exists;
- whether the candidate was applied;
- active path and availability;
- cost, latency, utilization, and loss;
- a bounded failure penalty or negative path-cost reward.

Decision and outcome records join by `decision_id`. Invalid JSONL lines are
ignored by the offline loader rather than breaking an experiment.

## Shadow environment

`OfflineRoutingEnv` exposes a Gymnasium-style `reset`/`step` API as a one-step
contextual routing environment:

- action `0`: keep the current path;
- action `1`: choose the candidate path.

Unsafe candidate actions receive a fixed penalty. The environment never
references a datapath or `FlowManager`.

`ShadowEvaluator` compares policies across the recorded episodes and reports:

- average reward;
- candidate selection rate;
- unsafe action rate.

Run the built-in read-only baselines with:

```bash
python scripts/evaluate_shadow.py data/routing-learning.jsonl
```

Inspect coverage, corrupt records, duplicates, orphan outcomes, unsafe
candidates, and topology diversity with:

```bash
python scripts/inspect_dataset.py data/routing-learning.jsonl
```

`GraphObservationEncoder` converts variable-size topology snapshots into
stable node features, directed edge indices, normalized edge features, and
global path features. It uses plain Python tuples so the core remains small;
training code can convert the result to NumPy, PyTorch, or PyTorch Geometric
tensors.

`PromotionGate` prevents a shadow policy from progressing when the dataset is
too small, outcome coverage is incomplete, unsafe actions are observed, or
reward does not beat the baseline.

This is the safety boundary for future GNN/PPO work: train and evaluate
offline first, then run in live shadow mode, and only later consider
recommendation or constrained execution.