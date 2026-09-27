import json
import unittest
from urllib.error import HTTPError
from urllib.request import urlopen

from aco.models import LinkMetrics
from controller.api import SnapshotApiServer
from controller.state import (
    OperatingMode,
    SnapshotStore,
    calculate_health_score,
)
from prediction.engine import EdgeForecast
from recommendation.engine import Recommendation


def forecast(
    risk: str = "normal",
) -> EdgeForecast:
    return EdgeForecast(
        edge=(1, 2),
        generated_at=10,
        horizon_seconds=60,
        sample_count=5,
        available=True,
        current_utilization=0.4,
        predicted_utilization=0.5,
        current_loss=0.0,
        predicted_loss=0.0,
        current_latency_ms=5,
        predicted_latency_ms=6,
        confidence=0.9,
        risk=risk,
        signals=(),
        seconds_to_utilization_threshold=None,
    )


class ControlApiTests(unittest.TestCase):
    def test_health_score_penalizes_failure_and_critical_risk(
        self,
    ) -> None:
        healthy = calculate_health_score(
            {
                (1, 2): LinkMetrics(
                    utilization=0.1, loss=0.0
                )
            },
            (forecast(),),
        )
        unhealthy = calculate_health_score(
            {(1, 2): LinkMetrics(available=False)},
            (forecast("critical"),),
        )

        self.assertGreater(healthy, unhealthy)
        self.assertGreaterEqual(healthy, 90)
        self.assertEqual(unhealthy, 0)

    def test_read_only_api_exposes_snapshot_sections(self) -> None:
        store = SnapshotStore(OperatingMode.RECOMMEND)
        store.publish(
            generated_at=10,
            mode=OperatingMode.RECOMMEND,
            switches=(2, 1),
            links={},
            metrics={
                (1, 2): LinkMetrics(
                    latency_ms=5,
                    utilization=0.4,
                )
            },
            forecasts=(forecast(),),
            recommendations=(
                Recommendation(
                    recommendation_id="r1",
                    category="preventive_action",
                    title="Test recommendation",
                    rationale=("predicted_congestion",),
                    action_type="simulate_reroute",
                    confidence=0.9,
                    urgency="medium",
                    affected_flows=(),
                    valid_until=100,
                    auto_apply_allowed=False,
                ),
            ),
            flows=(),
            proposals=(),
            events=(),
        )
        server = SnapshotApiServer(
            store, "127.0.0.1", 0
        )
        server.start()
        host, port = server.address
        base = f"http://{host}:{port}/api/v1"
        try:
            with urlopen(
                f"{base}/health", timeout=2
            ) as response:
                health = json.load(response)
            with urlopen(
                f"{base}/forecasts", timeout=2
            ) as response:
                forecasts = json.load(response)
            with urlopen(
                f"{base}/recommendations", timeout=2
            ) as response:
                recommendations = json.load(response)
            with urlopen(
                f"{base}/events", timeout=2
            ) as response:
                events = json.load(response)
            self.assertEqual(health["mode"], "recommend")
            self.assertGreater(health["health_score"], 0)
            self.assertEqual(forecasts[0]["edge"], [1, 2])
            self.assertEqual(
                recommendations[0][
                    "recommendation_id"
                ],
                "r1",
            )
            self.assertEqual(events, [])
            with self.assertRaises(HTTPError) as error:
                urlopen(f"{base}/missing", timeout=2)
            self.assertEqual(error.exception.code, 404)
        finally:
            server.shutdown()


if __name__ == "__main__":
    unittest.main()