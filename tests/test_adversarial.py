from __future__ import annotations

import errno
import os
import stat
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest import mock

from filecommit import (
    UnsafeTargetError,
    UnsupportedDurabilityError,
    atomic_open,
    replace_bytes,
    replace_text,
)


class AdversarialTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def staging_files(self) -> list[Path]:
        return list(self.root.glob(".filecommit-*.tmp"))

    def test_context_exit_before_enter_is_rejected_without_side_effects(self) -> None:
        target = self.root / "target.txt"
        operation = atomic_open(target, "w")
        with self.assertRaisesRegex(RuntimeError, "before it is entered"):
            operation.__exit__(None, None, None)
        self.assertFalse(target.exists())
        self.assertEqual(self.staging_files(), [])

    def test_second_context_exit_is_harmless(self) -> None:
        target = self.root / "target.txt"
        operation = atomic_open(target, "w")
        with operation as staged:
            staged.write("new")
        self.assertFalse(operation.__exit__(None, None, None))
        self.assertEqual(target.read_text(encoding="utf-8"), "new")

    def test_root_directory_path_is_rejected_as_a_file(self) -> None:
        with self.assertRaisesRegex(ValueError, "must name a file"):
            atomic_open(os.path.abspath(os.sep), "w")

    def test_close_failure_does_not_mask_body_exception(self) -> None:
        target = self.root / "target.txt"
        body_error = RuntimeError("body")
        real_fdopen = os.fdopen

        class FailingCloseFile:
            def __init__(self, file_object: object) -> None:
                self.file_object = file_object

            @property
            def closed(self) -> bool:
                return self.file_object.closed  # type: ignore[union-attr]

            def write(self, value: str) -> int:
                return self.file_object.write(value)  # type: ignore[union-attr,no-any-return]

            def close(self) -> None:
                self.file_object.close()  # type: ignore[union-attr]
                raise OSError(errno.EIO, "close failed")

        def failing_fdopen(fd: int, *_args: object, **_kwargs: object) -> FailingCloseFile:
            return FailingCloseFile(real_fdopen(fd, "w", encoding="utf-8"))

        with mock.patch("filecommit._core.os.fdopen", side_effect=failing_fdopen):
            with self.assertRaises(RuntimeError) as raised:
                with atomic_open(target, "w") as staged:
                    staged.write("new")
                    raise body_error
        self.assertIs(raised.exception, body_error)
        self.assertFalse(target.exists())
        self.assertEqual(self.staging_files(), [])

    def test_failure_cleanup_closes_stream_before_removing_staging_file(self) -> None:
        target = self.root / "target.txt"
        real_fdopen = os.fdopen
        real_unlink = os.unlink
        opened: list[object] = []

        class TrackingFile:
            def __init__(self, file_object: object) -> None:
                self.file_object = file_object

            @property
            def closed(self) -> bool:
                return self.file_object.closed  # type: ignore[union-attr]

            def write(self, value: str) -> int:
                return self.file_object.write(value)  # type: ignore[union-attr,no-any-return]

            def close(self) -> None:
                self.file_object.close()  # type: ignore[union-attr]

        def tracked_fdopen(fd: int, *_args: object, **_kwargs: object) -> TrackingFile:
            tracked = TrackingFile(real_fdopen(fd, "w", encoding="utf-8"))
            opened.append(tracked)
            return tracked

        def assert_closed_then_unlink(path: object) -> None:
            self.assertEqual(len(opened), 1)
            self.assertTrue(opened[0].closed)  # type: ignore[union-attr]
            real_unlink(path)

        with mock.patch("filecommit._core.os.fdopen", side_effect=tracked_fdopen):
            with mock.patch("filecommit._core.os.unlink", side_effect=assert_closed_then_unlink):
                with self.assertRaisesRegex(RuntimeError, "body"):
                    with atomic_open(target, "w") as staged:
                        staged.write("new")
                        raise RuntimeError("body")
        self.assertFalse(target.exists())
        self.assertEqual(self.staging_files(), [])

    def test_write_helpers_reject_nonprogressing_binary_writer(self) -> None:
        class FakeContext:
            def __init__(self, result: object) -> None:
                self.result = result

            def __enter__(self) -> object:
                return self

            def __exit__(self, *args: object) -> bool:
                return False

            def write(self, value: object) -> object:
                return self.result

        target = self.root / "target.bin"
        for result in (None, 0, -1):
            with self.subTest(result=result):
                with mock.patch("filecommit._core.atomic_open", return_value=FakeContext(result)):
                    with self.assertRaises(OSError):
                        replace_bytes(target, b"content")

    def test_write_helpers_reject_nonprogressing_text_writer(self) -> None:
        class FakeContext:
            def __init__(self, result: object) -> None:
                self.result = result

            def __enter__(self) -> object:
                return self

            def __exit__(self, *args: object) -> bool:
                return False

            def write(self, value: object) -> object:
                return self.result

        target = self.root / "target.txt"
        for result in (None, 0, -1):
            with self.subTest(result=result):
                with mock.patch("filecommit._core.atomic_open", return_value=FakeContext(result)):
                    with self.assertRaises(OSError):
                        replace_text(target, "content")

    def test_encoding_failure_preserves_target(self) -> None:
        target = self.root / "target.txt"
        target.write_text("old", encoding="utf-8")
        with self.assertRaises(UnicodeEncodeError):
            replace_text(target, "not ASCII: é", encoding="ascii")
        self.assertEqual(target.read_text(encoding="utf-8"), "old")
        self.assertEqual(self.staging_files(), [])

    def test_permission_application_failure_preserves_target(self) -> None:
        target = self.root / "target.txt"
        target.write_text("old", encoding="utf-8")
        cause = OSError(errno.EPERM, "chmod failed")
        with mock.patch("filecommit._core._apply_permissions", side_effect=cause):
            with self.assertRaises(OSError) as raised:
                replace_text(target, "new")
        self.assertIs(raised.exception, cause)
        self.assertEqual(target.read_text(encoding="utf-8"), "old")
        self.assertEqual(self.staging_files(), [])

    def test_cleanup_retries_after_read_only_style_permission_error(self) -> None:
        target = self.root / "target.txt"
        target.write_text("old", encoding="utf-8")
        real_unlink = os.unlink
        calls = 0

        def fail_once(path: object) -> None:
            nonlocal calls
            calls += 1
            if calls == 1:
                raise PermissionError(errno.EACCES, "read-only")
            real_unlink(path)

        with mock.patch("filecommit._core.os.replace", side_effect=PermissionError("blocked")):
            with mock.patch("filecommit._core.os.unlink", side_effect=fail_once):
                with self.assertRaises(PermissionError):
                    replace_text(target, "new", permissions=0o444)
        self.assertEqual(calls, 2)
        self.assertEqual(target.read_text(encoding="utf-8"), "old")
        self.assertEqual(self.staging_files(), [])

    def test_target_becoming_directory_during_write_is_refused(self) -> None:
        target = self.root / "target"
        with self.assertRaises(UnsafeTargetError), atomic_open(target, "w") as staged:
            staged.write("new")
            target.mkdir()
        self.assertTrue(target.is_dir())
        self.assertEqual(self.staging_files(), [])

    def test_target_becoming_hardlinked_during_write_is_refused(self) -> None:
        if not hasattr(os, "link"):
            self.skipTest("hard links unavailable")
        target = self.root / "target.txt"
        alias = self.root / "alias.txt"
        target.write_text("old", encoding="utf-8")
        try:
            with self.assertRaises(UnsafeTargetError):
                with atomic_open(target, "w") as staged:
                    staged.write("new")
                    os.link(target, alias)
        except OSError as error:
            self.skipTest(f"cannot create hard link: {error}")
        self.assertEqual(target.read_text(encoding="utf-8"), "old")
        self.assertEqual(alias.read_text(encoding="utf-8"), "old")
        self.assertEqual(self.staging_files(), [])

    def test_keyboard_interrupt_preserves_target(self) -> None:
        target = self.root / "target.txt"
        target.write_text("old", encoding="utf-8")
        with self.assertRaises(KeyboardInterrupt), atomic_open(target, "w") as staged:
            staged.write("new")
            raise KeyboardInterrupt
        self.assertEqual(target.read_text(encoding="utf-8"), "old")
        self.assertEqual(self.staging_files(), [])

    def test_unsupported_full_durability_fails_before_staging(self) -> None:
        target = self.root / "target.txt"
        with mock.patch("filecommit._core._full_durability_supported", return_value=False):
            with self.assertRaises(UnsupportedDurabilityError):
                with atomic_open(target, "w", durability="full"):
                    pass
        self.assertFalse(target.exists())
        self.assertEqual(self.staging_files(), [])

    def test_full_durability_operates_on_local_posix_filesystem(self) -> None:
        if os.name != "posix":
            self.skipTest("full durability is POSIX-only")
        target = self.root / "target.txt"
        replace_text(target, "new", durability="full")
        self.assertEqual(target.read_text(encoding="utf-8"), "new")

    def test_staging_file_is_in_target_directory(self) -> None:
        target = self.root / "target.txt"
        with atomic_open(target, "w") as staged:
            staged.write("new")
            [temporary_path] = self.staging_files()
            self.assertEqual(temporary_path.parent, self.root)
            self.assertTrue(temporary_path.name.startswith(".filecommit-"))

    def test_noncontiguous_memoryview_is_supported(self) -> None:
        target = self.root / "target.bin"
        source = memoryview(b"0123456789")[::2]
        replace_bytes(target, source)
        self.assertEqual(target.read_bytes(), b"02468")

    def test_unicode_filename_is_supported(self) -> None:
        target = self.root / "Grüezi 東京.txt"
        replace_text(target, "content")
        self.assertEqual(target.read_text(encoding="utf-8"), "content")

    def test_undecodable_bytes_filename_is_supported_on_posix(self) -> None:
        if os.name != "posix":
            self.skipTest("POSIX byte-path semantics required")
        root = os.fsencode(self.root)
        target = root + b"/invalid-\xff-name"
        try:
            replace_bytes(target, b"content")
            with open(target, "rb") as file_object:
                self.assertEqual(file_object.read(), b"content")
        except OSError as error:
            self.skipTest(f"filesystem rejects undecodable byte filenames: {error}")
        finally:
            try:
                os.unlink(target)
            except FileNotFoundError:
                pass

    def test_invalid_text_buffering_cleans_created_staging_file(self) -> None:
        target = self.root / "target.txt"
        with self.assertRaises(ValueError), atomic_open(target, "w", buffering=0):
            pass
        self.assertFalse(target.exists())
        self.assertEqual(self.staging_files(), [])

    def test_abrupt_process_exit_never_publishes_staged_content(self) -> None:
        target = self.root / "target.txt"
        target.write_text("old", encoding="utf-8")
        source_root = Path(__file__).resolve().parents[1] / "src"
        script = textwrap.dedent(
            f"""
            import os
            from filecommit import atomic_open

            with atomic_open({str(target)!r}, "w") as staged:
                staged.write("new")
                staged.flush()
                os._exit(23)
            """
        )
        environment = os.environ.copy()
        environment["PYTHONPATH"] = str(source_root)
        result = subprocess.run(
            [sys.executable, "-c", script],
            env=environment,
            check=False,
        )
        self.assertEqual(result.returncode, 23)
        self.assertEqual(target.read_text(encoding="utf-8"), "old")
        staged = self.staging_files()
        self.assertEqual(len(staged), 1)
        self.assertEqual(staged[0].read_text(encoding="utf-8"), "new")
        if os.name == "posix":
            self.assertEqual(stat.S_IMODE(staged[0].stat().st_mode), 0o600)
        staged[0].unlink()


if __name__ == "__main__":
    unittest.main()
