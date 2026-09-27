#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from dataclasses import asdict

from learning.environment import OfflineRoutingEnv, load_episodes
from learning.shadow import (
    AlwaysKeepPolicy,
    GreedySafePolicy,
    ShadowEvaluator,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Evaluate read-only routing policies on a recorded dataset"
        )
    )
    parser.add_argument(
        "dataset",
        nargs="?",
        default="data/routing-learning.jsonl",
    )
    arguments = parser.parse_args()
    episodes = load_episodes(arguments.dataset)
    if not episodes:
        parser.error("dataset contains no decision episodes")

    evaluator = ShadowEvaluator()
    policies = {
        "always_keep": AlwaysKeepPolicy(),
        "greedy_safe": GreedySafePolicy(),
    }
    results = {
        name: asdict(
            evaluator.evaluate(
                OfflineRoutingEnv(episodes),
                policy,
            )
        )
        for name, policy in policies.items()
    }
    print(
        json.dumps(
            {
                "dataset": arguments.dataset,
                "policies": results,
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())