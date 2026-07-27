"""Tests for benchmark statistics without asserting timing values."""

from __future__ import annotations

import unittest

from tools.benchmark_filecommit import _percentile, run


class BenchmarkTests(unittest.TestCase):
    def test_nearest_rank_percentiles(self) -> None:
        self.assertEqual(_percentile([1.0, 2.0, 3.0, 4.0], 90), 4.0)
        self.assertEqual(_percentile([1.0, 2.0, 3.0, 4.0], 50), 2.0)

    def test_small_run_contains_required_operation_classes(self) -> None:
        report = run(samples=1, payload_sizes=(0,))
        operations = {result["operation"] for result in report["results"]}
        self.assertTrue(
            {"direct_write", "same_target_contention", "different_target_contention"} <= operations
        )
        self.assertEqual(report["parameters"]["samples"], 1)


if __name__ == "__main__":
    unittest.main()
