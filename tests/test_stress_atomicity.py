"""Focused checks for the spawn-safe atomicity stress tool."""

from __future__ import annotations

import unittest

from tools.stress_atomicity import run


class StressAtomicityToolTests(unittest.TestCase):
    def test_same_target_process_stress(self) -> None:
        run(writers=2, replacements=3, payload_size=1024, readers=1, mode="same", timeout=30)

    def test_different_target_process_stress(self) -> None:
        run(writers=2, replacements=3, payload_size=1024, readers=1, mode="different", timeout=30)

    def test_invalid_mode_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "mode"):
            run(writers=1, replacements=1, payload_size=0, mode="invalid")


if __name__ == "__main__":
    unittest.main()
