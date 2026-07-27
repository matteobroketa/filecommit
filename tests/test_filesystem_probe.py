"""Tests for the isolated filesystem capability probe."""

from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from tools import filesystem_probe


class FilesystemProbeTests(unittest.TestCase):
    def test_run_keeps_the_supplied_directory_clean(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            report = filesystem_probe.run(root)
            self.assertEqual(report["directory"], str(root.resolve()))
            self.assertEqual(
                set(report["observations"]),
                {
                    "same_directory_temporary",
                    "replacement",
                    "reader_visibility",
                    "file_fsync",
                    "parent_directory_fsync",
                    "permissions",
                    "symlink_refusal",
                    "hardlink_refusal",
                    "crash_orphan",
                },
            )
            self.assertEqual(list(root.iterdir()), [])

    def test_cli_writes_json_to_stdout_and_summary_to_stderr(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            stdout = io.StringIO()
            stderr = io.StringIO()
            with redirect_stdout(stdout), redirect_stderr(stderr):
                self.assertEqual(filesystem_probe.main([directory]), 0)
            report = json.loads(stdout.getvalue())
            self.assertIn("observations", report)
            self.assertIn("atomicreplace filesystem probe:", stderr.getvalue())

    def test_invalid_directory_raises_a_clear_error(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(ValueError):
                filesystem_probe.run(Path(directory) / "missing")


if __name__ == "__main__":
    unittest.main()
