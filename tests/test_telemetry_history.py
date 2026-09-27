import json
import tempfile
import unittest
from pathlib import Path

from aco.models import LinkMetrics
from controller.telemetry_history import TelemetryHistory


class TelemetryHistoryTests(unittest.TestCase):
    def test_interval_limits_durable_snapshots(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "telemetry.jsonl"
            history = TelemetryHistory(path, 5, 100)
            metrics = {
                (1, 2): LinkMetrics(
                    latency_ms=5,
                    utilization=0.4,
                    observed_at=10,
                    confidence=0.8,
                    provenance="openflow_port_stats",
                )
            }

            self.assertTrue(
                history.append(
                    observed_at=100,
                    topology_generation=7,
                    metrics=metrics,
                )
            )
            self.assertFalse(
                history.append(
                    observed_at=102,
                    topology_generation=7,
                    metrics=metrics,
                )
            )
            self.assertTrue(
                history.append(
                    observed_at=105,
                    topology_generation=8,
                    metrics=metrics,
                )
            )

            records = [
                json.loads(line)
                for line in path.read_text().splitlines()
            ]
            self.assertEqual(len(records), 2)
            self.assertEqual(
                records[-1]["topology_generation"], 8
            )
            self.assertEqual(
                records[-1]["links"][0]["confidence"], 0.8
            )


if __name__ == "__main__":
    unittest.main()