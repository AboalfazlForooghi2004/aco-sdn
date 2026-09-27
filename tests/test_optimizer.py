import unittest

from aco.cost import CostWeights, path_cost
from aco.models import LinkMetrics, NetworkGraph
from aco.optimizer import ACOConfig, AntColonyOptimizer


class OptimizerTests(unittest.TestCase):
    def build_graph(self) -> NetworkGraph:
        graph = NetworkGraph()
        congested = LinkMetrics(
            latency_ms=40, utilization=0.95, loss=0.08
        )
        healthy = LinkMetrics(
            latency_ms=5, utilization=0.15, loss=0.0
        )
        graph.add_bidirectional_link("s1", "s2", congested)
        graph.add_bidirectional_link("s2", "s4", congested)
        graph.add_bidirectional_link("s1", "s3", healthy)
        graph.add_bidirectional_link("s3", "s4", healthy)
        return graph

    def test_aco_prefers_lower_dynamic_cost(self) -> None:
        graph = self.build_graph()
        optimizer = AntColonyOptimizer(
            ACOConfig(ants=30, iterations=25, seed=7)
        )
        result = optimizer.optimize(graph, "s1", "s4")
        self.assertEqual(result.path, ("s1", "s3", "s4"))
        self.assertFalse(result.used_fallback)

    def test_path_cost_reflects_congestion(self) -> None:
        graph = self.build_graph()
        weights = CostWeights()
        self.assertLess(
            path_cost(graph, ["s1", "s3", "s4"], weights),
            path_cost(graph, ["s1", "s2", "s4"], weights),
        )

    def test_unavailable_links_are_excluded(self) -> None:
        graph = self.build_graph()
        graph.add_link("s1", "s3", LinkMetrics(available=False))
        optimizer = AntColonyOptimizer(
            ACOConfig(ants=10, iterations=10, seed=1)
        )
        result = optimizer.optimize(graph, "s1", "s4")
        self.assertEqual(result.path, ("s1", "s2", "s4"))

    def test_mmas_reports_stagnation_and_bounded_restart(self) -> None:
        graph = self.build_graph()
        optimizer = AntColonyOptimizer(
            ACOConfig(
                strategy="mmas",
                ants=20,
                iterations=50,
                stagnation_iterations=3,
                max_restarts=1,
                seed=7,
            )
        )

        result = optimizer.optimize(graph, "s1", "s4")

        self.assertEqual(result.path, ("s1", "s3", "s4"))
        self.assertEqual(result.strategy, "mmas")
        self.assertEqual(result.restarts, 1)
        self.assertEqual(result.convergence_reason, "stagnation")
        self.assertLess(result.iterations_run, 50)

    def test_invalid_strategy_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            ACOConfig(strategy="unknown")


if __name__ == "__main__":
    unittest.main()