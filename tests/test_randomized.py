"""Deterministic model-based tests for repeated replacement behavior."""

from __future__ import annotations

import unittest

from tools.model_validation import DEFAULT_SEEDS, run


class RandomizedReplacementTests(unittest.TestCase):
    def test_required_model_seed_set_matches_reference_model(self) -> None:
        run(DEFAULT_SEEDS, cases=100)


if __name__ == "__main__":
    unittest.main()
