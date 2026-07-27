"""Subprocess validation of atomic visibility across abrupt process termination."""

from __future__ import annotations

import glob
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from atomicreplace import replace_text

_HELPER = Path(__file__).with_name("crash_helper.py")
_EXIT_CODE = 23


class CrashBoundaryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def staging_files(self) -> list[Path]:
        return [Path(path) for path in glob.glob(str(self.root / ".atomicreplace-*.tmp"))]

    def _crash(self, point: str) -> Path:
        target = self.root / f"{point}.txt"
        target.write_text("old", encoding="utf-8")
        signal = self.root / f"{point}.signal"
        environment = os.environ.copy()
        source_root = Path(__file__).resolve().parents[1] / "src"
        environment["PYTHONPATH"] = str(source_root)
        result = subprocess.run(
            [
                sys.executable,
                str(_HELPER),
                "--target",
                str(target),
                "--point",
                point,
                "--signal",
                str(signal),
            ],
            env=environment,
            check=False,
            timeout=20,
        )
        self.assertEqual(result.returncode, _EXIT_CODE)
        self.assertEqual(signal.read_text(encoding="utf-8"), point)
        return target

    def test_pre_replacement_crashes_preserve_old_target_and_orphan(self) -> None:
        points = (
            "after-staging",
            "after-partial-write",
            "after-flush",
            "after-file-sync",
            "after-permissions",
            "before-replace",
        )
        for point in points:
            with self.subTest(point=point):
                target = self._crash(point)
                self.assertEqual(target.read_text(encoding="utf-8"), "old")
                staging = self.staging_files()
                self.assertEqual(len(staging), 1)
                if os.name == "posix":
                    expected_mode = (
                        stat.S_IMODE(target.stat().st_mode)
                        if point in {"after-file-sync", "after-permissions", "before-replace"}
                        else 0o600
                    )
                    self.assertEqual(stat.S_IMODE(staging[0].stat().st_mode), expected_mode)
                staging[0].unlink()

    def test_post_replacement_crash_publishes_new_target(self) -> None:
        target = self._crash("after-replace")
        self.assertEqual(target.read_text(encoding="utf-8"), "new")
        self.assertEqual(self.staging_files(), [])

    @unittest.skipUnless(os.name == "posix", "parent-directory synchronization requires POSIX")
    def test_parent_directory_crash_boundaries_publish_new_target(self) -> None:
        for point in ("before-directory-sync", "after-directory-sync"):
            with self.subTest(point=point):
                target = self._crash(point)
                self.assertEqual(target.read_text(encoding="utf-8"), "new")
                self.assertEqual(self.staging_files(), [])

    def test_new_operation_does_not_delete_crash_orphan(self) -> None:
        target = self._crash("after-partial-write")
        [orphan] = self.staging_files()
        replace_text(target, "replacement")
        self.assertTrue(orphan.exists())
        self.assertEqual(target.read_text(encoding="utf-8"), "replacement")
        orphan.unlink()


if __name__ == "__main__":
    unittest.main()
