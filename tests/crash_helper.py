"""Child process for deterministic abrupt-exit transaction tests."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
from typing import Optional, Sequence
from unittest import mock

from filecommit import Durability, atomic_open

_EXIT_CODE = 23


class _SignalAfterFlush:
    def __init__(self, stream: object, signal: object) -> None:
        self._stream = stream
        self._signal = signal

    @property
    def closed(self) -> bool:
        return self._stream.closed  # type: ignore[union-attr]

    def flush(self) -> None:
        self._stream.flush()  # type: ignore[union-attr]
        self._signal()  # type: ignore[operator]

    def __getattr__(self, name: str) -> object:
        return getattr(self._stream, name)


def run(target: Path, point: str, signal_path: Path) -> None:
    """Write staged content and terminate at one named transaction boundary."""

    def signal_and_exit() -> None:
        signal_path.write_text(point, encoding="utf-8")
        os._exit(_EXIT_CODE)

    if point == "after-staging":
        with mock.patch(
            "filecommit._core.os.fdopen", side_effect=lambda *_args, **_kwargs: signal_and_exit()
        ):
            with atomic_open(target, "w", durability=Durability.DATA):
                pass
    elif point == "after-partial-write":
        with atomic_open(target, "w", durability=Durability.DATA) as stream:
            stream.write("new-partial")
            signal_and_exit()
    elif point == "after-flush":
        real_fdopen = os.fdopen

        def signal_after_flush(
            fd: int, *arguments: object, **keywords: object
        ) -> _SignalAfterFlush:
            return _SignalAfterFlush(real_fdopen(fd, *arguments, **keywords), signal_and_exit)

        with mock.patch("filecommit._core.os.fdopen", side_effect=signal_after_flush):
            with atomic_open(target, "w", durability=Durability.DATA) as stream:
                stream.write("new")
    elif point == "after-file-sync":
        real_fsync = os.fsync

        def signal_after_file_sync(fd: int) -> None:
            real_fsync(fd)
            signal_and_exit()

        with mock.patch("filecommit._core.os.fsync", side_effect=signal_after_file_sync):
            with atomic_open(target, "w", durability=Durability.DATA) as stream:
                stream.write("new")
    elif point == "after-permissions":
        from filecommit import _core

        real_apply_permissions = _core._apply_permissions

        def signal_after_permissions(fd: int, staging: object, permissions: int) -> None:
            real_apply_permissions(fd, staging, permissions)
            signal_and_exit()

        with mock.patch(
            "filecommit._core._apply_permissions", side_effect=signal_after_permissions
        ):
            with atomic_open(target, "w", durability=Durability.DATA) as stream:
                stream.write("new")
    elif point in {"before-replace", "after-replace"}:
        from filecommit import _core

        real_replace = _core._replace

        def signal_around_replace(staging: object, destination: object) -> None:
            if point == "before-replace":
                signal_and_exit()
            real_replace(staging, destination)
            signal_and_exit()

        with mock.patch("filecommit._core._replace", side_effect=signal_around_replace):
            with atomic_open(target, "w", durability=Durability.DATA) as stream:
                stream.write("new")
    elif point in {"before-directory-sync", "after-directory-sync"}:
        from filecommit import _core

        real_sync_directory = _core._sync_parent_directory

        def signal_around_directory_sync(path: object) -> None:
            if point == "before-directory-sync":
                signal_and_exit()
            real_sync_directory(path)
            signal_and_exit()

        with mock.patch(
            "filecommit._core._sync_parent_directory", side_effect=signal_around_directory_sync
        ):
            with atomic_open(target, "w", durability=Durability.FULL) as stream:
                stream.write("new")
    else:
        raise ValueError(f"unknown crash point: {point}")


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", type=Path, required=True)
    parser.add_argument("--point", required=True)
    parser.add_argument("--signal", type=Path, required=True)
    arguments = parser.parse_args(argv)
    run(arguments.target, arguments.point, arguments.signal)
    raise AssertionError("crash helper returned normally")


if __name__ == "__main__":
    raise SystemExit(main())
