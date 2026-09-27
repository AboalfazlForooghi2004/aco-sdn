from __future__ import annotations

import argparse

from experiments.runner import run_experiments


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run deterministic offline ACO-SDN scenarios"
    )
    parser.add_argument(
        "--output",
        default="results/offline_comparison.csv",
        help="CSV output path",
    )
    args = parser.parse_args()
    results = run_experiments(args.output)
    print(f"wrote {len(results)} rows to {args.output}")


if __name__ == "__main__":
    main()