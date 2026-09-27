#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from dataclasses import asdict

from learning.quality import inspect_dataset


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Inspect routing-learning dataset quality"
    )
    parser.add_argument(
        "dataset",
        nargs="?",
        default="data/routing-learning.jsonl",
    )
    arguments = parser.parse_args()
    print(
        json.dumps(
            asdict(inspect_dataset(arguments.dataset)),
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())