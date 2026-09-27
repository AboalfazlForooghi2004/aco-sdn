# Routing algorithm strategy

## Production default

The controller defaults to **Max-Min Ant System (MMAS)** rather than the
original Ant System.

MMAS keeps the useful online and explainable behavior of ACO while adding:

- lower and upper pheromone bounds;
- best-so-far-only reinforcement;
- stagnation detection;
- a bounded deterministic restart;
- early-stop and convergence diagnostics.

These controls reduce premature convergence and cap wasted controller CPU
time. The original Ant System remains selectable for reproducible baseline
experiments.

## Why GNN/DRL is not the default yet

Recent traffic-engineering research increasingly combines graph neural
networks with deep reinforcement learning. These methods are promising for
large or previously unseen topologies, but a safe controller deployment also
needs a representative training corpus, an offline network simulator,
out-of-distribution detection, shadow evaluation, and a deterministic safety
fallback.

Adding an untrained policy directly to the forwarding loop would be a
regression in safety and reproducibility. The recommended evolution is:

1. collect versioned topology, demand, action, and outcome datasets;
2. build a Gym-compatible offline environment;
3. train a GNN policy with PPO or another constrained RL method;
4. run it in shadow mode against MMAS and minimum-cost routing;
5. enable recommendations only after measured safety gates pass;
6. retain MMAS as the fallback policy.

## Configuration

```yaml
aco:
  strategy: mmas
  pheromone_min: 0.05
  pheromone_max: 5.0
  stagnation_iterations: 8
  max_restarts: 1
```

Use `strategy: ant_system` only for baseline comparison or compatibility
testing.