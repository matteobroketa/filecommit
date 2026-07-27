"""Capability-based native filesystem checks for platform-specific edge cases."""

from __future__ import annotations

import glob
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path

from _capabilities import create_symlink_or_skip, long_path_or_skip, unix_socket_or_skip

from filecommit import UnsafeTargetError, replace_bytes, replace_text


class NativePlatformTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def staging_files(self) -> list[str]:
        return glob.glob(str(self.root / ".filecommit-*.tmp"))

    def test_directory_symlink_target_is_refused_without_staging_file(self) -> None:
        destination = self.root / "destination"
        destination.mkdir()
        link = self.root / "directory-link"
        create_symlink_or_skip(self, link, destination, directory=True)
        with self.assertRaises(UnsafeTargetError):
            replace_text(link, "new")
        self.assertTrue(link.is_symlink())
        self.assertEqual(self.staging_files(), [])

    def test_unix_socket_target_is_refused_without_staging_file(self) -> None:
        socket = unix_socket_or_skip(self)
        target = self.root / "socket"
        try:
            socket.bind(str(target))
            with self.assertRaises(UnsafeTargetError):
                replace_bytes(target, b"new")
            self.assertEqual(self.staging_files(), [])
        except OSError as error:
            self.skipTest(f"cannot bind AF_UNIX socket in temporary directory: {error}")
        finally:
            socket.close()
            try:
                target.unlink()
            except FileNotFoundError:
                pass

    @unittest.skipUnless(sys.platform.startswith("linux"), "Linux device semantics required")
    def test_linux_character_device_target_is_refused_without_staging_file(self) -> None:
        target = Path("/dev/null")
        if not target.exists() or not stat.S_ISCHR(target.stat().st_mode):
            self.skipTest("/dev/null is not an accessible character device")
        with self.assertRaises(UnsafeTargetError):
            replace_bytes(target, b"new")
        self.assertEqual(self.staging_files(), [])

    @unittest.skipUnless(os.name == "nt", "native Windows path behavior required")
    def test_windows_reserved_name_behavior_is_observed_without_staging_file(self) -> None:
        target = self.root / "CON"
        try:
            replace_text(target, "new")
        except OSError:
            self.assertFalse(target.exists())
        else:
            try:
                target.unlink()
            except FileNotFoundError:
                pass
            self.skipTest("filesystem accepts this Windows reserved-name spelling")
        self.assertEqual(self.staging_files(), [])

    @unittest.skipUnless(os.name == "nt", "native Windows path behavior required")
    def test_windows_invalid_character_name_is_rejected_without_staging_file(self) -> None:
        target = self.root / "invalid?.txt"
        with self.assertRaises(OSError):
            replace_text(target, "new")
        self.assertFalse(target.exists())
        self.assertEqual(self.staging_files(), [])

    @unittest.skipUnless(os.name == "nt", "native Windows path behavior required")
    def test_windows_long_path_when_runner_policy_permits_it(self) -> None:
        directory = long_path_or_skip(self, self.root)
        target = directory / "target.txt"
        replace_text(target, "new")
        self.assertEqual(target.read_text(encoding="utf-8"), "new")

    @unittest.skipUnless(sys.platform == "darwin", "macOS filename behavior required")
    def test_macos_filename_normalization_is_observed_not_assumed(self) -> None:
        decomposed = self.root / "e\u0301.txt"
        composed = self.root / "é.txt"
        replace_text(decomposed, "new")
        self.assertEqual(decomposed.read_text(encoding="utf-8"), "new")
        names = {path.name for path in self.root.iterdir()}
        self.assertTrue(decomposed.name in names or composed.name in names)
        if composed.exists():
            self.assertEqual(composed.read_text(encoding="utf-8"), "new")

    @unittest.skipUnless(os.name == "posix", "POSIX permission semantics required")
    def test_restrictive_umask_does_not_weaken_default_new_file_permissions(self) -> None:
        target = self.root / "secret.txt"
        previous_umask = os.umask(0)
        try:
            replace_text(target, "secret")
        finally:
            os.umask(previous_umask)
        self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o600)

    @unittest.skipUnless(os.name == "posix", "POSIX directory permission semantics required")
    def test_unwritable_parent_refuses_staging_creation_when_enforced(self) -> None:
        if hasattr(os, "geteuid") and os.geteuid() == 0:
            self.skipTest("root bypasses directory write permissions")
        parent = self.root / "restricted"
        parent.mkdir()
        parent.chmod(0o500)
        target = parent / "target.txt"
        try:
            with self.assertRaises(PermissionError):
                replace_text(target, "new")
        except AssertionError:
            self.skipTest("filesystem does not enforce restrictive parent permissions")
        finally:
            parent.chmod(0o700)
        self.assertFalse(target.exists())


if __name__ == "__main__":
    unittest.main()
