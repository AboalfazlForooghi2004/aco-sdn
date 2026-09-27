import unittest

from controller.flow_demand import (
    FlowCounters,
    FlowDemandEstimator,
)


class FlowDemandEstimatorTests(unittest.TestCase):
    def test_counter_delta_produces_directional_demand(self) -> None:
        estimator = FlowDemandEstimator(
            ewma_alpha=0.5,
            max_age_seconds=5,
        )
        first = FlowCounters(
            byte_count=100,
            packet_count=10,
            observed_at=10,
        )
        second = FlowCounters(
            byte_count=1_100,
            packet_count=30,
            observed_at=11,
        )

        self.assertIsNone(
            estimator.update("AA", "BB", first)
        )
        demand = estimator.update("AA", "BB", second)

        self.assertIsNotNone(demand)
        assert demand is not None
        self.assertEqual(demand.bits_per_second, 8_000)
        self.assertEqual(demand.packets_per_second, 20)
        self.assertEqual(
            estimator.get("aa", "bb", now=12),
            demand,
        )

    def test_ewma_smooths_new_measurement(self) -> None:
        estimator = FlowDemandEstimator(0.5, 5)
        estimator.update(
            "aa", "bb", FlowCounters(0, 0, 0)
        )
        first = estimator.update(
            "aa", "bb", FlowCounters(1_000, 10, 1)
        )
        second = estimator.update(
            "aa", "bb", FlowCounters(4_000, 30, 2)
        )

        assert first is not None and second is not None
        self.assertEqual(first.bits_per_second, 8_000)
        self.assertEqual(second.bits_per_second, 16_000)

    def test_reset_and_staleness_invalidate_demand(self) -> None:
        estimator = FlowDemandEstimator(1.0, 2)
        estimator.update(
            "aa", "bb", FlowCounters(1_000, 10, 10)
        )
        estimator.update(
            "aa", "bb", FlowCounters(2_000, 20, 11)
        )
        self.assertIsNone(
            estimator.get("aa", "bb", now=14)
        )
        self.assertIsNone(
            estimator.update(
                "aa",
                "bb",
                FlowCounters(100, 1, 15),
            )
        )


if __name__ == "__main__":
    unittest.main()