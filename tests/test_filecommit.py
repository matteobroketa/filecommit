from __future__ import annotations

import errno
import glob
import os
import stat
import tempfile
import threading
import unittest
import warnings
from pathlib import Path
from unittest import mock

import filecommit
from filecommit import (
    DirectorySyncError,
    Durability,
    UnsafeTargetError,
    atomic_open,
    replace_bytes,
    replace_text,
)
from filecommit._core import _replace


class FileCommitTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def staging_files(self) -> list[str]:
        return glob.glob(str(self.root / ".filecommit-*.tmp"))

    def test_replace_bytes_creates_file(self) -> None:
        target = self.root / "payload.bin"
        replace_bytes(target, b"abc\x00def")
        self.assertEqual(target.read_bytes(), b"abc\x00def")

    def test_replace_bytes_accepts_all_documented_bytes_like_types(self) -> None:
        target = self.root / "payload.bin"
        for value in (b"bytes", bytearray(b"bytearray"), memoryview(b"memoryview")):
            replace_bytes(target, value)
            self.assertEqual(target.read_bytes(), bytes(value))

    def test_replace_text_defaults_to_utf8(self) -> None:
        target = self.root / "unicode.txt"
        replace_text(target, "Grüezi — 東京")
        self.assertEqual(target.read_bytes(), "Grüezi — 東京".encode("utf-8"))

    def test_replace_text_supports_encoding_and_newline(self) -> None:
        target = self.root / "utf16.txt"
        replace_text(target, "a\nb\n", encoding="utf-16-le", newline="\r\n")
        self.assertEqual(target.read_bytes(), "a\r\nb\r\n".encode("utf-16-le"))

    def test_empty_payloads(self) -> None:
        binary = self.root / "empty.bin"
        text = self.root / "empty.txt"
        replace_bytes(binary, b"")
        replace_text(text, "")
        self.assertEqual(binary.read_bytes(), b"")
        self.assertEqual(text.read_text(encoding="utf-8"), "")

    def test_target_is_unchanged_until_successful_exit(self) -> None:
        target = self.root / "config.txt"
        target.write_text("old", encoding="utf-8")
        with atomic_open(target, "w") as staged:
            staged.write("new")
            self.assertEqual(target.read_text(encoding="utf-8"), "old")
            self.assertEqual(len(self.staging_files()), 1)
        self.assertEqual(target.read_text(encoding="utf-8"), "new")
        self.assertEqual(self.staging_files(), [])

    def test_body_exception_preserves_existing_target_and_cleans_staging(self) -> None:
        target = self.root / "config.txt"
        target.write_text("old", encoding="utf-8")
        with self.assertRaisesRegex(RuntimeError, "stop"):
            with atomic_open(target, "w") as staged:
                staged.write("new")
                raise RuntimeError("stop")
        self.assertEqual(target.read_text(encoding="utf-8"), "old")
        self.assertEqual(self.staging_files(), [])

    def test_body_exception_leaves_absent_target_absent(self) -> None:
        target = self.root / "config.txt"
        with self.assertRaises(RuntimeError), atomic_open(target, "w") as staged:
            staged.write("new")
            raise RuntimeError
        self.assertFalse(target.exists())
        self.assertEqual(self.staging_files(), [])

    def test_new_file_uses_secure_permissions_by_default(self) -> None:
        if os.name != "posix":
            self.skipTest("POSIX mode semantics required")
        target = self.root / "secret.txt"
        replace_text(target, "secret")
        self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o600)

    def test_existing_permissions_are_preserved(self) -> None:
        if os.name != "posix":
            self.skipTest("POSIX mode semantics required")
        target = self.root / "config.txt"
        target.write_text("old", encoding="utf-8")
        target.chmod(0o640)
        replace_text(target, "new")
        self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o640)

    def test_permissions_can_be_overridden(self) -> None:
        if os.name != "posix":
            self.skipTest("POSIX mode semantics required")
        target = self.root / "config.txt"
        target.write_text("old", encoding="utf-8")
        target.chmod(0o600)
        replace_text(target, "new", permissions=0o644)
        self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o644)

    def test_preservation_can_be_disabled(self) -> None:
        if os.name != "posix":
            self.skipTest("POSIX mode semantics required")
        target = self.root / "config.txt"
        target.write_text("old", encoding="utf-8")
        target.chmod(0o644)
        replace_text(target, "new", preserve_permissions=False)
        self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o600)

    def test_special_permission_bits_are_not_copied(self) -> None:
        if os.name != "posix":
            self.skipTest("POSIX mode semantics required")
        target = self.root / "program"
        target.write_bytes(b"old")
        target.chmod(0o4755)
        replace_bytes(target, b"new")
        self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o755)

    def test_staging_file_is_private_while_open(self) -> None:
        if os.name != "posix":
            self.skipTest("POSIX mode semantics required")
        target = self.root / "secret.txt"
        with atomic_open(target, "w", permissions=0o644) as staged:
            staged.write("secret")
            [temporary_path] = self.staging_files()
            self.assertEqual(stat.S_IMODE(os.stat(temporary_path).st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o644)

    def test_symlink_target_is_refused_without_modifying_either_file(self) -> None:
        if not hasattr(os, "symlink"):
            self.skipTest("symlinks unavailable")
        destination = self.root / "destination.txt"
        destination.write_text("destination", encoding="utf-8")
        link = self.root / "link.txt"
        try:
            link.symlink_to(destination)
        except (OSError, NotImplementedError) as error:
            self.skipTest(f"cannot create symlink: {error}")
        with self.assertRaises(UnsafeTargetError):
            replace_text(link, "new")
        self.assertTrue(link.is_symlink())
        self.assertEqual(destination.read_text(encoding="utf-8"), "destination")

    def test_target_becoming_symlink_during_write_is_refused(self) -> None:
        if not hasattr(os, "symlink"):
            self.skipTest("symlinks unavailable")
        destination = self.root / "destination.txt"
        destination.write_text("destination", encoding="utf-8")
        target = self.root / "target.txt"
        try:
            with self.assertRaises(UnsafeTargetError):
                with atomic_open(target, "w") as staged:
                    staged.write("new")
                    target.symlink_to(destination)
        except (OSError, NotImplementedError) as error:
            self.skipTest(f"cannot create symlink: {error}")
        self.assertTrue(target.is_symlink())
        self.assertEqual(destination.read_text(encoding="utf-8"), "destination")
        self.assertEqual(self.staging_files(), [])

    def test_directory_target_is_refused(self) -> None:
        with self.assertRaises(UnsafeTargetError):
            replace_text(self.root, "new")

    def test_fifo_target_is_refused(self) -> None:
        if not hasattr(os, "mkfifo"):
            self.skipTest("FIFOs unavailable")
        fifo = self.root / "pipe"
        os.mkfifo(fifo)
        with self.assertRaises(UnsafeTargetError):
            replace_bytes(fifo, b"new")

    def test_hardlinked_target_is_refused_by_default(self) -> None:
        if not hasattr(os, "link"):
            self.skipTest("hard links unavailable")
        target = self.root / "target.txt"
        alias = self.root / "alias.txt"
        target.write_text("old", encoding="utf-8")
        try:
            os.link(target, alias)
        except OSError as error:
            self.skipTest(f"cannot create hard link: {error}")
        with self.assertRaises(UnsafeTargetError):
            replace_text(target, "new")
        self.assertEqual(target.read_text(encoding="utf-8"), "old")
        self.assertEqual(alias.read_text(encoding="utf-8"), "old")

    def test_hardlinked_target_can_be_replaced_explicitly(self) -> None:
        if not hasattr(os, "link"):
            self.skipTest("hard links unavailable")
        target = self.root / "target.txt"
        alias = self.root / "alias.txt"
        target.write_text("old", encoding="utf-8")
        try:
            os.link(target, alias)
        except OSError as error:
            self.skipTest(f"cannot create hard link: {error}")
        replace_text(target, "new", allow_hardlinks=True)
        self.assertEqual(target.read_text(encoding="utf-8"), "new")
        self.assertEqual(alias.read_text(encoding="utf-8"), "old")

    def test_relative_path_is_bound_when_atomic_open_is_called(self) -> None:
        first = self.root / "first"
        second = self.root / "second"
        first.mkdir()
        second.mkdir()
        old_cwd = os.getcwd()
        try:
            os.chdir(first)
            operation = atomic_open("target.txt", "w")
            os.chdir(second)
            with operation as staged:
                staged.write("first")
        finally:
            os.chdir(old_cwd)
        self.assertEqual((first / "target.txt").read_text(encoding="utf-8"), "first")
        self.assertFalse((second / "target.txt").exists())

    def test_changing_cwd_inside_context_does_not_redirect_commit(self) -> None:
        first = self.root / "first"
        second = self.root / "second"
        first.mkdir()
        second.mkdir()
        old_cwd = os.getcwd()
        try:
            os.chdir(first)
            with atomic_open("target.txt", "w") as staged:
                staged.write("first")
                os.chdir(second)
        finally:
            os.chdir(old_cwd)
        self.assertEqual((first / "target.txt").read_text(encoding="utf-8"), "first")
        self.assertFalse((second / "target.txt").exists())

    def test_bytes_path_is_supported(self) -> None:
        target = os.fsencode(self.root / "bytes-path.txt")
        replace_bytes(target, b"content")
        with open(target, "rb") as file_object:
            self.assertEqual(file_object.read(), b"content")

    def test_custom_pathlike_is_supported(self) -> None:
        class CustomPath:
            def __fspath__(self) -> str:
                return str(self.root / "custom.txt")

            def __init__(self, root: Path) -> None:
                self.root = root

        target = CustomPath(self.root)
        replace_text(target, "content")
        self.assertEqual((self.root / "custom.txt").read_text(), "content")

    def test_closed_staging_file_does_not_commit(self) -> None:
        target = self.root / "target.txt"
        target.write_text("old", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "closed"):
            with atomic_open(target, "w") as staged:
                staged.write("new")
                staged.close()
        self.assertEqual(target.read_text(encoding="utf-8"), "old")
        self.assertEqual(self.staging_files(), [])

    def test_context_manager_is_one_shot(self) -> None:
        target = self.root / "target.txt"
        operation = atomic_open(target, "w")
        with operation as staged:
            staged.write("new")
        with self.assertRaisesRegex(RuntimeError, "only once"), operation:
            pass

    def test_replace_failure_preserves_target_and_cleans_staging(self) -> None:
        target = self.root / "target.txt"
        target.write_text("old", encoding="utf-8")
        error = PermissionError(errno.EACCES, "blocked")
        with mock.patch("filecommit._core.os.replace", side_effect=error):
            with self.assertRaises(PermissionError):
                replace_text(target, "new")
        self.assertEqual(target.read_text(encoding="utf-8"), "old")
        self.assertEqual(self.staging_files(), [])

    def test_file_sync_failure_preserves_target_and_cleans_staging(self) -> None:
        target = self.root / "target.txt"
        target.write_text("old", encoding="utf-8")
        error = OSError(errno.EIO, "sync failed")
        with mock.patch("filecommit._core.os.fsync", side_effect=error):
            with self.assertRaises(OSError) as raised:
                replace_text(target, "new", durability="data")
        self.assertIs(raised.exception, error)
        self.assertEqual(target.read_text(encoding="utf-8"), "old")
        self.assertEqual(self.staging_files(), [])

    def test_full_durability_syncs_parent_after_replacement(self) -> None:
        if os.name != "posix":
            self.skipTest("full durability is POSIX-only")
        target = self.root / "target.txt"
        with mock.patch("filecommit._core._sync_parent_directory") as sync_directory:
            replace_text(target, "new", durability=Durability.FULL)
        sync_directory.assert_called_once_with(os.path.abspath(target))
        self.assertEqual(target.read_text(encoding="utf-8"), "new")

    def test_directory_sync_failure_reports_committed_state(self) -> None:
        if os.name != "posix":
            self.skipTest("full durability is POSIX-only")
        target = self.root / "target.txt"
        target.write_text("old", encoding="utf-8")
        cause = OSError(errno.EIO, "directory sync failed")
        with mock.patch("filecommit._core._sync_parent_directory", side_effect=cause):
            with self.assertRaises(DirectorySyncError) as raised:
                replace_text(target, "new", durability="full")
        self.assertTrue(raised.exception.committed)
        self.assertIs(raised.exception.cause, cause)
        self.assertEqual(target.read_text(encoding="utf-8"), "new")
        self.assertEqual(self.staging_files(), [])

    def test_none_durability_does_not_call_fsync(self) -> None:
        target = self.root / "target.txt"
        with mock.patch("filecommit._core.os.fsync") as fsync:
            replace_text(target, "new", durability="none")
        fsync.assert_not_called()

    def test_data_durability_calls_fsync_once(self) -> None:
        target = self.root / "target.txt"
        real_fsync = os.fsync
        calls = []

        def record(fd: int) -> None:
            calls.append(fd)
            real_fsync(fd)

        with mock.patch("filecommit._core.os.fsync", side_effect=record):
            replace_text(target, "new", durability="data")
        self.assertEqual(len(calls), 1)

    def test_open_reader_keeps_old_inode_on_posix(self) -> None:
        if os.name != "posix":
            self.skipTest("POSIX open-file replacement semantics required")
        target = self.root / "target.txt"
        target.write_text("old", encoding="utf-8")
        with target.open("r", encoding="utf-8") as old_reader:
            replace_text(target, "new")
            self.assertEqual(old_reader.read(), "old")
        self.assertEqual(target.read_text(encoding="utf-8"), "new")

    def test_concurrent_writers_never_publish_partial_payload(self) -> None:
        target = self.root / "target.bin"
        payloads = [bytes([index]) * 128_000 for index in range(1, 9)]
        barrier = threading.Barrier(len(payloads))
        errors = []

        def writer(payload: bytes) -> None:
            try:
                barrier.wait()
                replace_bytes(target, payload)
            except BaseException as error:
                errors.append(error)

        threads = [threading.Thread(target=writer, args=(payload,)) for payload in payloads]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        self.assertEqual(errors, [])
        self.assertIn(target.read_bytes(), payloads)
        self.assertEqual(self.staging_files(), [])

    def test_replace_retries_transient_windows_sharing_violation(self) -> None:
        target = self.root / "target.txt"
        staging = self.root / "staging.txt"
        staging.write_text("new", encoding="utf-8")
        real_replace = os.replace
        calls = 0

        def fail_once(source: object, destination: object) -> None:
            nonlocal calls
            calls += 1
            if calls == 1:
                raise PermissionError(errno.EACCES, "sharing violation")
            real_replace(source, destination)

        with mock.patch("filecommit._core.os.name", "nt"):
            with mock.patch("filecommit._core.os.replace", side_effect=fail_once):
                with mock.patch("filecommit._core.time.sleep") as sleep:
                    _replace(staging, target)
        self.assertEqual(calls, 2)
        sleep.assert_called_once_with(0.01)
        self.assertEqual(target.read_text(encoding="utf-8"), "new")

    def test_cleanup_failure_does_not_mask_body_exception(self) -> None:
        target = self.root / "target.txt"
        body_error = RuntimeError("body failed")
        with mock.patch("filecommit._core.os.unlink", side_effect=PermissionError("blocked")):
            with warnings.catch_warnings(record=True) as caught:
                warnings.simplefilter("always")
                with self.assertRaises(RuntimeError) as raised:
                    with atomic_open(target, "w") as staged:
                        staged.write("new")
                        raise body_error
        self.assertIs(raised.exception, body_error)
        self.assertTrue(any(isinstance(item.message, filecommit.CleanupWarning) for item in caught))

    def test_api_validation(self) -> None:
        target = self.root / "target.txt"
        invalid_calls = (
            lambda: atomic_open(target, "r"),
            lambda: atomic_open(target, "a"),
            lambda: atomic_open(target, "w+"),
            lambda: atomic_open(target, "wb", encoding="utf-8"),
            lambda: atomic_open(target, "w", permissions=True),
            lambda: atomic_open(target, "w", permissions=-1),
            lambda: atomic_open(target, "w", permissions=0o1000),
            lambda: atomic_open(target, "w", durability="maximum"),
            lambda: atomic_open(target, "w", durability=1),
            lambda: atomic_open(target, "w", buffering=True),
            lambda: atomic_open(target, "w", preserve_permissions=1),
            lambda: atomic_open(target, "w", allow_hardlinks=1),
            lambda: replace_text(target, b"not text"),
            lambda: replace_bytes(target, "not bytes"),
            lambda: atomic_open("", "w"),
        )
        for invalid_call in invalid_calls:
            with self.subTest(call=invalid_call):
                with self.assertRaises((TypeError, ValueError)):
                    invalid_call()

    def test_missing_parent_raises_without_creating_anything(self) -> None:
        target = self.root / "missing" / "target.txt"
        with self.assertRaises(FileNotFoundError):
            replace_text(target, "new")
        self.assertFalse(target.exists())

    def test_public_exports_are_stable_and_versioned(self) -> None:
        self.assertEqual(filecommit.__version__, "0.1.0")
        self.assertEqual(
            set(filecommit.__all__),
            {
                "CleanupWarning",
                "DirectorySyncError",
                "Durability",
                "FileCommitError",
                "UnsupportedDurabilityError",
                "UnsafeTargetError",
                "atomic_open",
                "replace_bytes",
                "replace_text",
            },
        )


if __name__ == "__main__":
    unittest.main()
