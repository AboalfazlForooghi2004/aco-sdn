import unittest

from controller.telemetry import PortCounters, TelemetryCollector
from controller.topology import TopologyManager


def counters(
    observed_at: float,
    *,
    rx_bytes: int = 0,
    tx_bytes: int = 0,
    rx_packets: int = 0,
    tx_packets: int = 0,
    rx_dropped: int = 0,
    tx_dropped: int = 0,
) -> PortCounters:
    return PortCounters(
        rx_bytes=rx_bytes,
        tx_bytes=tx_bytes,
        rx_packets=rx_packets,
        tx_packets=tx_packets,
        rx_dropped=rx_dropped,
        tx_dropped=tx_dropped,
        observed_at=observed_at,
    )


class TelemetryCollectorTests(unittest.TestCase):
    def test_two_samples_produce_rates_utilization_and_loss(self) -> None:
        collector = TelemetryCollector(
            link_capacity_bps=10_000,
            max_age_seconds=5,
        )
        self.assertIsNone(
            collector.update(
                1,
                2,
                counters(10, tx_bytes=100, tx_packets=10),
            )
        )

        result = collector.update(
            1,
            2,
            counters(
                11,
                tx_bytes=1_100,
                tx_packets=19,
                tx_dropped=1,
            ),
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.tx_bps, 8_000)
        self.assertEqual(result.utilization, 0.8)
        self.assertEqual(result.loss, 0.1)

    def test_counter_reset_discards_rate(self) -> None:
        collector = TelemetryCollector(10_000, 5)
        collector.update(1, 2, counters(10, tx_bytes=1_000))

        result = collector.update(
            1, 2, counters(11, tx_bytes=50)
        )

        self.assertIsNone(result)
        self.assertIsNone(
            collector.port_telemetry(1, 2, now=11)
        )

    def test_stale_telemetry_marks_link_unavailable(self) -> None:
        collector = TelemetryCollector(10_000, 5)
        topology = TopologyManager()
        topology.add_link(1, 2, 7, 8)
        collector.update(1, 7, counters(10, tx_bytes=0))
        collector.update(1, 7, counters(11, tx_bytes=100))

        fresh = collector.link_metrics(topology, now=12)
        stale = collector.link_metrics(topology, now=17)

        self.assertTrue(fresh[(1, 2)].available)
        self.assertFalse(stale[(1, 2)].available)

    def test_missing_source_port_sample_marks_link_unavailable(self) -> None:
        collector = TelemetryCollector(10_000, 5)
        topology = TopologyManager()
        topology.add_link(1, 2, 7, 8)

        metrics = collector.link_metrics(topology, now=1)

        self.assertFalse(metrics[(1, 2)].available)
        self.assertEqual(metrics[(1, 2)].confidence, 0.0)

    def test_discovered_port_capacity_overrides_default(self) -> None:
        collector = TelemetryCollector(100_000_000, 5)
        self.assertTrue(
            collector.update_capacity(
                1, 7, 10_000_000, "openflow_port_desc"
            )
        )
        collector.update(1, 7, counters(10, tx_bytes=0))
        result = collector.update(
            1,
            7,
            counters(11, tx_bytes=1_000_000),
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.capacity_bps, 10_000_000)
        self.assertEqual(result.utilization, 0.8)
        self.assertEqual(
            result.capacity_source, "openflow_port_desc"
        )

    def test_link_metrics_include_provenance(self) -> None:
        collector = TelemetryCollector(10_000, 5)
        topology = TopologyManager()
        topology.add_link(1, 2, 7, 8)
        collector.update(1, 7, counters(10))
        collector.update(1, 7, counters(11, tx_bytes=100))

        metric = collector.link_metrics(
            topology, now=12
        )[(1, 2)]

        self.assertEqual(metric.confidence, 0.8)
        self.assertEqual(metric.observed_at, 11)
        self.assertIn("openflow_port_stats", metric.provenance)


if __name__ == "__main__":
    unittest.main()