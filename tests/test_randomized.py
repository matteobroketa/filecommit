"""Deterministic model-based tests for repeated replacement behavior."""

from __future__ import annotations

import random
import tempfile
import unittest
from pathlib import Path

from filecommit import atomic_open, replace_bytes, replace_text


class RandomizedReplacementTests(unittest.TestCase):
    def test_randomized_success_and_rollback_sequence_matches_model(self) -> None:
        randomizer = random.Random(0xF11EC0)
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "state.txt"
            expected = "initial"
            replace_text(target, expected)

            for operation in range(100):
                candidate = f"operation={operation};value={randomizer.getrandbits(128):032x}"
                should_commit = randomizer.choice((True, True, False))
                if should_commit:
                    with atomic_open(target) as stream:
                        split = randomizer.randrange(len(candidate) + 1)
                        stream.write(candidate[:split])
                        stream.write(candidate[split:])
                    expected = candidate
                else:
                    with self.assertRaisesRegex(RuntimeError, "abort"):
                        with atomic_open(target) as stream:
                            stream.write(candidate)
                            raise RuntimeError("abort")
                self.assertEqual(target.read_text(encoding="utf-8"), expected)

    def test_random_binary_payloads_round_trip(self) -> None:
        randomizer = random.Random(0xB17E5)
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "payload.bin"
            for size in (0, 1, 31, 4096, 65537):
                payload = bytes(randomizer.getrandbits(8) for _ in range(size))
                replace_bytes(target, payload)
                self.assertEqual(target.read_bytes(), payload)


if __name__ == "__main__":
    unittest.main()
