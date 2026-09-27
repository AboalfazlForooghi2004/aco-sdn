import unittest

from aco.models import LinkMetrics
from controller.latency import (
    LatencyTracker,
    decode_echo,
    decode_probe,
    encode_echo,
    encode_probe,
)
from controller.topology import TopologyManager


class LatencyTrackerTests(unittest.TestCase):
    def test_probe_and_echo_payloads_round_trip(self) -> None:
        echo = encode_echo(12.5)
        probe = encode_probe(123, 7, 14.25)

        self.assertEqual(decode_echo(echo), 12.5)
        self.assertEqual(decode_probe(probe), (123, 7, 14.25))
        self.assertIsNone(decode_probe(b"not-a-probe"))

    def test_probe_subtracts_control_plane_delay(self) -> None:
        tracker = LatencyTracker(
            ewma_alpha=0.25,
            max_age_seconds=5,
        )
        tracker.record_echo(1, sent_at=0, received_at=0.004)
        tracker.record_echo(2, sent_at=0, received_at=0.006)

        first = tracker.record_probe(
            1, 2, sent_at=1.0, received_at=1.007
        )
        second = tracker.record_probe(
            1, 2, sent_at=2.0, received_at=2.009
        )

        self.assertAlmostEqual(first or -1, 2.0, places=6)
        self.assertAlmostEqual(second or -1, 2.5, places=6)

    def test_fresh_latency_enriches_link_metrics(self) -> None:
        tracker = LatencyTracker(ewma_alpha=1.0, max_age_seconds=5)
        tracker.record_echo(1, 0, 0.002)
        tracker.record_echo(2, 0, 0.002)
        tracker.record_probe(1, 2, 10, 10.005)
        metrics = {
            (1, 2): LinkMetrics(utilization=0.3)
        }

        fresh = tracker.enrich(metrics, now=12)
        stale = tracker.enrich(metrics, now=16)

        self.assertAlmostEqual(
            fresh[(1, 2)].latency_ms, 3.0, places=6
        )
        self.assertEqual(stale[(1, 2)].latency_ms, 0.0)
        self.assertEqual(
            fresh[(1, 2)].utilization, 0.3
        )

    def test_probe_edge_must_match_discovered_ports(self) -> None:
        topology = TopologyManager()
        topology.add_link(1, 2, 12, 21)

        self.assertTrue(
            LatencyTracker.validate_probe_edge(
                topology, 1, 12, 2, 21
            )
        )
        self.assertFalse(
            LatencyTracker.validate_probe_edge(
                topology, 1, 99, 2, 21
            )
        )


if __name__ == "__main__":
    unittest.main()