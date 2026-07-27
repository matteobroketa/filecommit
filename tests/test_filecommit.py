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
from filecommit._core import _WINDOWS_TARGET_LOCKS, _replace


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

    def test_threaded_readers_observe_only_complete_payloads(self) -> None:
        target = self.root / "target.bin"
        payloads = [bytes([index]) * 32_000 for index in range(1, 4)]
        target.write_bytes(payloads[0])
        start = threading.Barrier(len(payloads))
        stop = threading.Event()
        reader_started = threading.Event()
        errors: list[BaseException] = []

        def reader() -> None:
            try:
                reader_started.set()
                while not stop.is_set():
                    try:
                        observed = target.read_bytes()
                    except (FileNotFoundError, PermissionError):
                        continue
                    if observed not in payloads:
                        raise AssertionError("reader observed partial or mixed payload")
            except BaseException as error:
                errors.append(error)

        def writer(payload: bytes) -> None:
            try:
                start.wait()
                for _ in range(10):
                    replace_bytes(target, payload)
            except BaseException as error:
                errors.append(error)

        reader_thread = threading.Thread(target=reader, name="filecommit-reader")
        writer_threads = [threading.Thread(target=writer, args=(payload,)) for payload in payloads]
        reader_thread.start()
        self.assertTrue(reader_started.wait(5))
        for thread in writer_threads:
            thread.start()
        for thread in writer_threads:
            thread.join(5)
        stop.set()
        reader_thread.join(5)
        self.assertTrue(all(not thread.is_alive() for thread in writer_threads))
        self.assertFalse(reader_thread.is_alive())
        self.assertEqual(errors, [])
        self.assertIn(target.read_bytes(), payloads)

    def test_native_windows_reader_handle_releases_retrying_replacement(self) -> None:
        if os.name != "nt":
            self.skipTest("native Windows sharing semantics required")
        target = self.root / "target.txt"
        target.write_text("old", encoding="utf-8")
        replacement_was_blocked = threading.Event()
        errors: list[BaseException] = []
        real_replace = os.replace

        def record_real_replacement(source: object, destination: object) -> None:
            try:
                real_replace(source, destination)
            except PermissionError:
                replacement_was_blocked.set()
                raise

        def writer() -> None:
            try:
                replace_text(target, "new")
            except BaseException as error:
                errors.append(error)

        with target.open("rb") as reader:
            with mock.patch("filecommit._core.os.replace", side_effect=record_real_replacement):
                writer_thread = threading.Thread(target=writer, name="filecommit-writer")
                writer_thread.start()
                self.assertTrue(replacement_was_blocked.wait(5))
                reader.close()
                writer_thread.join(5)
        self.assertFalse(writer_thread.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(target.read_text(encoding="utf-8"), "new")

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
                error = PermissionError(errno.EACCES, "sharing violation")
                error.winerror = 5
                raise error
            real_replace(source, destination)

        with mock.patch("filecommit._core._windows_replace_retries_enabled", return_value=True):
            with mock.patch("filecommit._core.os.replace", side_effect=fail_once):
                with mock.patch("filecommit._core.time.monotonic", side_effect=(0.0, 0.0)):
                    with mock.patch("filecommit._core.time.sleep") as sleep:
                        _replace(staging, target)
        self.assertEqual(calls, 2)
        sleep.assert_called_once_with(0.005)
        self.assertEqual(target.read_text(encoding="utf-8"), "new")

    def test_windows_retry_simulation_does_not_mutate_os_name(self) -> None:
        target = self.root / "target.txt"
        staging = self.root / "staging.txt"
        staging.write_text("new", encoding="utf-8")
        actual_os_name = os.name
        real_replace = os.replace
        calls = 0

        def fail_once(source: object, destination: object) -> None:
            nonlocal calls
            calls += 1
            if calls == 1:
                error = PermissionError(errno.EACCES, "sharing violation")
                error.winerror = 5
                raise error
            real_replace(source, destination)

        with mock.patch("filecommit._core._windows_replace_retries_enabled", return_value=True):
            with mock.patch("filecommit._core.os.replace", side_effect=fail_once):
                with mock.patch("filecommit._core.time.monotonic", side_effect=(0.0, 0.0)):
                    with mock.patch("filecommit._core.time.sleep"):
                        _replace(staging, target)

        self.assertEqual(os.name, actual_os_name)
        self.assertEqual(calls, 2)

    def test_replace_retries_only_explicit_transient_windows_errors(self) -> None:
        target = self.root / "target.txt"
        staging = self.root / "staging.txt"
        real_replace = os.replace

        for winerror in (5, 32, 33):
            with self.subTest(winerror=winerror):
                staging.write_text("new", encoding="utf-8")
                calls = 0

                def fail_once(
                    source: object, destination: object, expected_winerror: int = winerror
                ) -> None:
                    nonlocal calls
                    calls += 1
                    if calls == 1:
                        error = PermissionError(errno.EACCES, "transient replacement failure")
                        error.winerror = expected_winerror
                        raise error
                    real_replace(source, destination)

                with mock.patch(
                    "filecommit._core._windows_replace_retries_enabled", return_value=True
                ):
                    with mock.patch("filecommit._core.os.replace", side_effect=fail_once):
                        with mock.patch("filecommit._core.time.monotonic", side_effect=(0.0, 0.0)):
                            with mock.patch("filecommit._core.time.sleep") as sleep:
                                _replace(staging, target)
                self.assertEqual(calls, 2)
                sleep.assert_called_once_with(0.005)
                self.assertEqual(target.read_text(encoding="utf-8"), "new")

    def test_replace_does_not_retry_nontransient_windows_errors(self) -> None:
        target = self.root / "target.txt"
        staging = self.root / "staging.txt"
        staging.write_text("new", encoding="utf-8")
        error = PermissionError(errno.EACCES, "permanent failure")
        error.winerror = 87
        with mock.patch("filecommit._core._windows_replace_retries_enabled", return_value=True):
            with mock.patch("filecommit._core.os.replace", side_effect=error) as replace:
                with mock.patch("filecommit._core.time.sleep") as sleep:
                    with self.assertRaises(PermissionError) as raised:
                        _replace(staging, target)
        self.assertIs(raised.exception, error)
        replace.assert_called_once_with(staging, target)
        sleep.assert_not_called()

    def test_replace_does_not_retry_permission_error_without_winerror(self) -> None:
        target = self.root / "target.txt"
        staging = self.root / "staging.txt"
        staging.write_text("new", encoding="utf-8")
        error = PermissionError(errno.EACCES, "unclassified failure")
        with mock.patch("filecommit._core._windows_replace_retries_enabled", return_value=True):
            with mock.patch("filecommit._core.os.replace", side_effect=error) as replace:
                with mock.patch("filecommit._core.time.sleep") as sleep:
                    with self.assertRaises(PermissionError) as raised:
                        _replace(staging, target)
        self.assertIs(raised.exception, error)
        replace.assert_called_once_with(staging, target)
        sleep.assert_not_called()

    def test_replace_stops_at_monotonic_deadline(self) -> None:
        target = self.root / "target.txt"
        staging = self.root / "staging.txt"
        staging.write_text("new", encoding="utf-8")
        error = PermissionError(errno.EACCES, "sharing violation")
        error.winerror = 5
        with mock.patch("filecommit._core._windows_replace_retries_enabled", return_value=True):
            with mock.patch("filecommit._core.os.replace", side_effect=error) as replace:
                with mock.patch("filecommit._core.time.monotonic", side_effect=(0.0, 0.0, 5.0)):
                    with mock.patch("filecommit._core.time.sleep") as sleep:
                        with self.assertRaises(PermissionError) as raised:
                            _replace(staging, target)
        self.assertIs(raised.exception, error)
        self.assertEqual(replace.call_count, 2)
        sleep.assert_called_once_with(0.005)

    def test_windows_same_target_writers_serialize_and_registry_is_reclaimed(self) -> None:
        if os.name != "nt":
            self.skipTest("Windows target locking required")
        target = self.root / "target.txt"
        first_entered = threading.Event()
        release_first = threading.Event()
        second_entered = threading.Event()
        errors: list[BaseException] = []

        def first_writer() -> None:
            try:
                with atomic_open(target) as stream:
                    first_entered.set()
                    if not release_first.wait(5):
                        raise TimeoutError("first writer was not released")
                    stream.write("first")
            except BaseException as error:
                errors.append(error)

        def second_writer() -> None:
            try:
                with atomic_open(target) as stream:
                    second_entered.set()
                    stream.write("second")
            except BaseException as error:
                errors.append(error)

        first = threading.Thread(target=first_writer)
        second = threading.Thread(target=second_writer)
        first.start()
        self.assertTrue(first_entered.wait(5))
        second.start()
        self.assertFalse(second_entered.wait(0.1))
        release_first.set()
        first.join(5)
        second.join(5)
        self.assertFalse(first.is_alive())
        self.assertFalse(second.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(target.read_text(encoding="utf-8"), "second")
        self.assertEqual(_WINDOWS_TARGET_LOCKS, {})

    def test_windows_nested_same_target_write_is_reentrant_and_outer_commit_wins(self) -> None:
        if os.name != "nt":
            self.skipTest("Windows target locking required")
        target = self.root / "target.txt"
        with atomic_open(target) as outer:
            replace_text(target, "inner")
            outer.write("outer")
        self.assertEqual(target.read_text(encoding="utf-8"), "outer")
        self.assertEqual(_WINDOWS_TARGET_LOCKS, {})

    def test_windows_interrupted_lock_acquisition_reclaims_registry_reference(self) -> None:
        if os.name != "nt":
            self.skipTest("Windows target locking required")

        class InterruptedLock:
            def acquire(self) -> None:
                raise KeyboardInterrupt("interrupted")

            def release(self) -> None:
                raise AssertionError("interrupted lock must not be released")

        class Entry:
            def __init__(self) -> None:
                self.lock = InterruptedLock()
                self.references = 0

        entry = Entry()
        with mock.patch("filecommit._core._TargetLock", return_value=entry):
            with self.assertRaisesRegex(KeyboardInterrupt, "interrupted"):
                atomic_open(self.root / "target.txt").__enter__()
        self.assertEqual(entry.references, 0)
        self.assertEqual(_WINDOWS_TARGET_LOCKS, {})

    def test_windows_different_targets_can_progress_concurrently(self) -> None:
        if os.name != "nt":
            self.skipTest("Windows target locking required")
        first_target = self.root / "first.txt"
        second_target = self.root / "second.txt"
        first_entered = threading.Event()
        release_first = threading.Event()
        second_entered = threading.Event()
        errors: list[BaseException] = []

        def first_writer() -> None:
            try:
                with atomic_open(first_target) as stream:
                    first_entered.set()
                    if not release_first.wait(5):
                        raise TimeoutError("first writer was not released")
                    stream.write("first")
            except BaseException as error:
                errors.append(error)

        def second_writer() -> None:
            try:
                with atomic_open(second_target) as stream:
                    second_entered.set()
                    stream.write("second")
            except BaseException as error:
                errors.append(error)

        first = threading.Thread(target=first_writer)
        second = threading.Thread(target=second_writer)
        first.start()
        self.assertTrue(first_entered.wait(5))
        second.start()
        self.assertTrue(second_entered.wait(5))
        release_first.set()
        first.join(5)
        second.join(5)
        self.assertFalse(first.is_alive())
        self.assertFalse(second.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(_WINDOWS_TARGET_LOCKS, {})

    def test_windows_relative_and_absolute_aliases_share_a_lock(self) -> None:
        if os.name != "nt":
            self.skipTest("Windows target locking required")
        previous_directory = os.getcwd()
        first_entered = threading.Event()
        release_first = threading.Event()
        second_entered = threading.Event()
        errors: list[BaseException] = []
        try:
            os.chdir(self.root)
            relative_operation = atomic_open("target.txt")
            absolute_operation = atomic_open(self.root / "target.txt")
        finally:
            os.chdir(previous_directory)

        def first_writer() -> None:
            try:
                with relative_operation as stream:
                    first_entered.set()
                    if not release_first.wait(5):
                        raise TimeoutError("first writer was not released")
                    stream.write("first")
            except BaseException as error:
                errors.append(error)

        def second_writer() -> None:
            try:
                with absolute_operation as stream:
                    second_entered.set()
                    stream.write("second")
            except BaseException as error:
                errors.append(error)

        first = threading.Thread(target=first_writer)
        second = threading.Thread(target=second_writer)
        first.start()
        self.assertTrue(first_entered.wait(5))
        second.start()
        self.assertFalse(second_entered.wait(0.1))
        release_first.set()
        first.join(5)
        second.join(5)
        self.assertFalse(first.is_alive())
        self.assertFalse(second.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(_WINDOWS_TARGET_LOCKS, {})

    def test_windows_case_variants_share_a_lock(self) -> None:
        if os.name != "nt":
            self.skipTest("Windows target locking required")
        target = self.root / "Target.TXT"
        alias = self.root / "target.txt"
        first_entered = threading.Event()
        release_first = threading.Event()
        second_entered = threading.Event()
        errors: list[BaseException] = []

        def first_writer() -> None:
            try:
                with atomic_open(target) as stream:
                    first_entered.set()
                    if not release_first.wait(5):
                        raise TimeoutError("first writer was not released")
                    stream.write("first")
            except BaseException as error:
                errors.append(error)

        def second_writer() -> None:
            try:
                with atomic_open(alias) as stream:
                    second_entered.set()
                    stream.write("second")
            except BaseException as error:
                errors.append(error)

        first = threading.Thread(target=first_writer)
        second = threading.Thread(target=second_writer)
        first.start()
        self.assertTrue(first_entered.wait(5))
        second.start()
        self.assertFalse(second_entered.wait(0.1))
        release_first.set()
        first.join(5)
        second.join(5)
        self.assertFalse(first.is_alive())
        self.assertFalse(second.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(target.read_text(encoding="utf-8"), "second")
        self.assertEqual(_WINDOWS_TARGET_LOCKS, {})

    def test_windows_lock_is_released_after_body_exception(self) -> None:
        if os.name != "nt":
            self.skipTest("Windows target locking required")
        target = self.root / "target.txt"
        with self.assertRaisesRegex(RuntimeError, "body"):
            with atomic_open(target) as stream:
                stream.write("new")
                raise RuntimeError("body")
        self.assertEqual(_WINDOWS_TARGET_LOCKS, {})
        replace_text(target, "replacement")
        self.assertEqual(target.read_text(encoding="utf-8"), "replacement")
        self.assertEqual(_WINDOWS_TARGET_LOCKS, {})

    def test_windows_lock_registry_does_not_grow_for_historical_targets(self) -> None:
        if os.name != "nt":
            self.skipTest("Windows target locking required")
        for index in range(64):
            replace_text(self.root / f"target-{index}.txt", str(index))
        self.assertEqual(_WINDOWS_TARGET_LOCKS, {})

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
