import unittest

from aco.models import LinkMetrics
from controller.rerouting import ActiveFlow
from prediction.engine import PredictionConfig, PredictionEngine
from recommendation.engine import RecommendationEngine


class PredictionRecommendationTests(unittest.TestCase):
    def config(self) -> PredictionConfig:
        return PredictionConfig(
            history_window_seconds=120,
            max_samples_per_link=20,
            horizon_seconds=30,
            min_samples=5,
            min_history_seconds=40,
            utilization_threshold=0.8,
            loss_threshold=0.05,
            latency_growth_ratio=0.3,
        )

    def test_rising_utilization_predicts_congestion(self) -> None:
        engine = PredictionEngine(self.config())
        for index, utilization in enumerate(
            (0.40, 0.48, 0.56, 0.64, 0.72)
        ):
            engine.observe(
                {
                    (1, 2): LinkMetrics(
                        latency_ms=5,
                        utilization=utilization,
                        loss=0.001,
                    )
                },
                observed_at=index * 10,
            )

        forecast = engine.forecast((1, 2), now=40)

        self.assertIsNotNone(forecast)
        assert forecast is not None
        self.assertEqual(forecast.risk, "warning")
        self.assertIn(
            "predicted_congestion", forecast.signals
        )
        self.assertGreaterEqual(forecast.confidence, 0.99)
        self.assertGreaterEqual(
            forecast.predicted_utilization, 0.8
        )
        self.assertAlmostEqual(
            forecast.seconds_to_utilization_threshold
            or 0.0,
            10.0,
            places=5,
        )

    def test_stable_healthy_link_stays_normal(self) -> None:
        engine = PredictionEngine(self.config())
        for index in range(5):
            engine.observe(
                {
                    (1, 2): LinkMetrics(
                        latency_ms=5,
                        utilization=0.25,
                        loss=0.001,
                    )
                },
                observed_at=index * 10,
            )

        forecast = engine.forecast((1, 2), now=40)

        self.assertIsNotNone(forecast)
        assert forecast is not None
        self.assertEqual(forecast.risk, "normal")
        self.assertEqual(forecast.signals, ())

    def test_unavailable_link_is_immediate_risk(self) -> None:
        engine = PredictionEngine(self.config())
        engine.observe(
            {(2, 4): LinkMetrics(available=False)},
            observed_at=10,
        )

        forecast = engine.forecast((2, 4), now=10)

        self.assertIsNotNone(forecast)
        assert forecast is not None
        self.assertEqual(forecast.risk, "critical")
        self.assertEqual(
            forecast.signals, ("link_unavailable",)
        )

    def test_recommendation_names_affected_flow(self) -> None:
        prediction = PredictionEngine(self.config())
        for index, utilization in enumerate(
            (0.40, 0.48, 0.56, 0.64, 0.72)
        ):
            prediction.observe(
                {
                    (1, 2): LinkMetrics(
                        utilization=utilization
                    )
                },
                observed_at=index * 10,
            )
        forecast = prediction.forecast((1, 2), now=40)
        assert forecast is not None
        flow = ActiveFlow(
            source_mac="00:00:00:00:00:01",
            destination_mac="00:00:00:00:00:02",
            source_dpid=1,
            destination_dpid=4,
            source_host_port=1,
            destination_host_port=9,
            path=(1, 2, 4),
            installed_cost=0.5,
            last_reroute_at=0,
        )
        engine = RecommendationEngine(
            minimum_confidence=0.6,
            validity_seconds=90,
        )

        recommendations = engine.generate(
            (forecast,), (flow,), now=40
        )

        self.assertEqual(len(recommendations), 1)
        item = recommendations[0]
        self.assertEqual(item.category, "preventive_action")
        self.assertEqual(item.action_type, "simulate_reroute")
        self.assertEqual(item.affected_flows, (flow.key,))
        self.assertFalse(item.auto_apply_allowed)
        self.assertEqual(item.valid_until, 130)

    def test_low_confidence_prediction_is_not_recommended(self) -> None:
        engine = PredictionEngine(self.config())
        engine.observe(
            {(1, 2): LinkMetrics(utilization=0.60)},
            observed_at=0,
        )
        engine.observe(
            {(1, 2): LinkMetrics(utilization=0.75)},
            observed_at=1,
        )
        forecast = engine.forecast((1, 2), now=1)
        assert forecast is not None
        recommendations = RecommendationEngine(
            minimum_confidence=0.6,
            validity_seconds=90,
        ).generate((forecast,), (), now=1)

        self.assertLess(forecast.confidence, 0.6)
        self.assertEqual(recommendations, ())


if __name__ == "__main__":
    unittest.main()