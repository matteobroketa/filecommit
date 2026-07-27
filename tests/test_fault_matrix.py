"""Fault-injection tests for the atomic replacement transaction boundaries."""

from __future__ import annotations

import errno
import glob
import os
import stat
import tempfile
import unittest
import warnings
from contextlib import ExitStack
from pathlib import Path
from unittest import mock

from filecommit import CleanupWarning, DirectorySyncError, atomic_open, replace_text


class _FailingStream:
    """Delegate stream operations except for one deterministic transaction boundary."""

    def __init__(self, file_object: object, method: str, error: OSError) -> None:
        self._file_object = file_object
        self._method = method
        self._error = error

    @property
    def closed(self) -> bool:
        return self._file_object.closed  # type: ignore[union-attr]

    def write(self, value: str) -> int:
        if self._method == "write":
            raise self._error
        return self._file_object.write(value)  # type: ignore[union-attr,no-any-return]

    def flush(self) -> None:
        if self._method == "flush":
            raise self._error
        self._file_object.flush()  # type: ignore[union-attr]

    def close(self) -> None:
        self._file_object.close()  # type: ignore[union-attr]
        if self._method == "close":
            raise self._error

    def __getattr__(self, name: str) -> object:
        return getattr(self._file_object, name)


class TransactionFaultMatrixTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def staging_files(self) -> list[str]:
        return glob.glob(str(self.root / ".filecommit-*.tmp"))

    def _failing_fdopen(self, method: str, error: OSError) -> mock._patch:
        real_fdopen = os.fdopen

        def open_failing_stream(fd: int, *_args: object, **_kwargs: object) -> _FailingStream:
            return _FailingStream(real_fdopen(fd, "w", encoding="utf-8"), method, error)

        return mock.patch("filecommit._core.os.fdopen", side_effect=open_failing_stream)

    def test_precommit_fault_matrix_preserves_old_target_and_cleans_staging(self) -> None:
        cases = (
            "initial target inspection",
            "temporary creation",
            "descriptor-to-stream wrapping",
            "user write",
            "stream flush",
            "file synchronization",
            "final target revalidation",
            "permission calculation",
            "permission application",
            "stream close",
            "replacement",
        )

        for boundary in cases:
            with self.subTest(boundary=boundary):
                target = self.root / f"{boundary.replace(' ', '-')}.txt"
                target.write_text("old", encoding="utf-8")
                error = OSError(errno.EIO, f"{boundary} failed")
                durability = "data" if boundary == "file synchronization" else "none"

                with ExitStack() as stack:
                    if boundary == "initial target inspection":
                        stack.enter_context(
                            mock.patch("filecommit._core._validate_target", side_effect=error)
                        )
                    elif boundary == "temporary creation":
                        stack.enter_context(
                            mock.patch("filecommit._core.tempfile.mkstemp", side_effect=error)
                        )
                    elif boundary == "descriptor-to-stream wrapping":
                        stack.enter_context(
                            mock.patch("filecommit._core.os.fdopen", side_effect=error)
                        )
                    elif boundary == "user write":
                        stack.enter_context(self._failing_fdopen("write", error))
                    elif boundary == "stream flush":
                        stack.enter_context(self._failing_fdopen("flush", error))
                    elif boundary == "file synchronization":
                        stack.enter_context(
                            mock.patch("filecommit._core.os.fsync", side_effect=error)
                        )
                    elif boundary == "final target revalidation":
                        stack.enter_context(
                            mock.patch(
                                "filecommit._core._validate_target", side_effect=(None, error)
                            )
                        )
                    elif boundary == "permission calculation":
                        stack.enter_context(
                            mock.patch("filecommit._core.stat.S_IMODE", side_effect=error)
                        )
                    elif boundary == "permission application":
                        stack.enter_context(
                            mock.patch("filecommit._core._apply_permissions", side_effect=error)
                        )
                    elif boundary == "stream close":
                        stack.enter_context(self._failing_fdopen("close", error))
                    elif boundary == "replacement":
                        stack.enter_context(
                            mock.patch("filecommit._core.os.replace", side_effect=error)
                        )

                    with self.assertRaises(OSError) as raised:
                        replace_text(target, "new", durability=durability)

                self.assertIs(raised.exception, error)
                self.assertEqual(target.read_text(encoding="utf-8"), "old")
                self.assertTrue(stat.S_ISREG(target.stat().st_mode))
                self.assertEqual(self.staging_files(), [])

    def test_body_failure_has_precedence_over_cleanup_failure(self) -> None:
        target = self.root / "target.txt"
        body_error = RuntimeError("body failed")
        real_unlink = os.unlink

        with mock.patch("filecommit._core.os.unlink", side_effect=PermissionError("blocked")):
            with warnings.catch_warnings(record=True) as caught:
                warnings.simplefilter("always")
                with self.assertRaises(RuntimeError) as raised:
                    with atomic_open(target) as stream:
                        stream.write("new")
                        raise body_error
        self.assertIs(raised.exception, body_error)
        self.assertTrue(any(isinstance(item.message, CleanupWarning) for item in caught))
        [staging] = self.staging_files()
        real_unlink(staging)

    @unittest.skipUnless(os.name == "posix", "parent-directory synchronization requires POSIX")
    def test_postreplacement_directory_fault_matrix_reports_committed_state(self) -> None:
        cases = (
            "parent directory open",
            "parent directory synchronization",
            "parent directory close",
        )

        for boundary in cases:
            with self.subTest(boundary=boundary):
                target = self.root / f"{boundary.replace(' ', '-')}.txt"
                target.write_text("old", encoding="utf-8")
                error = OSError(errno.EIO, f"{boundary} failed")

                with ExitStack() as stack:
                    if boundary == "parent directory open":
                        real_open = os.open
                        expected_parent = os.path.dirname(os.path.abspath(target))
                        directory_flag = getattr(os, "O_DIRECTORY", 0)

                        def fail_parent_directory_open(
                            path: object,
                            flags: int,
                            *arguments: object,
                            parent: str = expected_parent,
                            required_flag: int = directory_flag,
                            original_open: object = real_open,
                            directory_error: OSError = error,
                        ) -> int:
                            if path == parent and (required_flag == 0 or flags & required_flag):
                                raise directory_error
                            return original_open(path, flags, *arguments)  # type: ignore[operator]

                        stack.enter_context(
                            mock.patch(
                                "filecommit._core.os.open", side_effect=fail_parent_directory_open
                            )
                        )
                    elif boundary == "parent directory synchronization":
                        real_fsync = os.fsync
                        calls = 0

                        def fail_directory_sync(
                            fd: int,
                            real_file_sync: object = real_fsync,
                            directory_error: OSError = error,
                        ) -> None:
                            nonlocal calls
                            calls += 1
                            if calls == 1:
                                real_file_sync(fd)  # type: ignore[operator]
                            else:
                                raise directory_error

                        stack.enter_context(
                            mock.patch("filecommit._core.os.fsync", side_effect=fail_directory_sync)
                        )
                    else:
                        real_close = os.close

                        def fail_directory_close(
                            fd: int,
                            real_directory_close: object = real_close,
                            directory_error: OSError = error,
                        ) -> None:
                            real_directory_close(fd)  # type: ignore[operator]
                            raise directory_error

                        stack.enter_context(
                            mock.patch(
                                "filecommit._core.os.close", side_effect=fail_directory_close
                            )
                        )

                    with self.assertRaises(DirectorySyncError) as raised:
                        replace_text(target, "new", durability="full")

                self.assertTrue(raised.exception.committed)
                self.assertIs(raised.exception.cause, error)
                self.assertIs(raised.exception.__cause__, error)
                self.assertEqual(target.read_text(encoding="utf-8"), "new")
                self.assertTrue(stat.S_ISREG(target.stat().st_mode))
                self.assertEqual(self.staging_files(), [])


if __name__ == "__main__":
    unittest.main()
