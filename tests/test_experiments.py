import csv
import tempfile
import unittest
from pathlib import Path

from experiments.runner import run_experiments


class ExperimentRunnerTests(unittest.TestCase):
    def test_runner_writes_three_algorithms_per_scenario(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "results.csv"

            results = run_experiments(output)

            self.assertEqual(len(results), 15)
            self.assertTrue(output.exists())
            with output.open(encoding="utf-8") as result_file:
                rows = list(csv.DictReader(result_file))
            self.assertEqual(len(rows), 15)

    def test_dynamic_algorithms_avoid_congested_upper_path(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            results = run_experiments(
                Path(directory) / "results.csv"
            )
        congestion = {
            result.algorithm: result
            for result in results
            if result.scenario == "congestion"
        }

        self.assertEqual(
            congestion["shortest_path"].path,
            "s1 -> s2 -> s4 -> s6",
        )
        self.assertEqual(
            congestion["minimum_dynamic_cost"].path,
            "s1 -> s3 -> s5 -> s6",
        )
        self.assertEqual(
            congestion["aco"].path,
            "s1 -> s3 -> s5 -> s6",
        )

    def test_all_algorithms_exclude_failed_link(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            results = run_experiments(
                Path(directory) / "results.csv"
            )
        failed = [
            result
            for result in results
            if result.scenario == "link_failure"
        ]

        self.assertTrue(
            all(
                result.path == "s1 -> s3 -> s5 -> s6"
                for result in failed
            )
        )


if __name__ == "__main__":
    unittest.main()