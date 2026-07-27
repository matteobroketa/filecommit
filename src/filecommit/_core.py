"""Dependency-free atomic file replacement primitives."""

from __future__ import annotations

import os
import stat
import tempfile
import threading
import time
import warnings
from enum import Enum
from types import TracebackType
from typing import (
    IO,
    Any,
    BinaryIO,
    ContextManager,
    Generic,
    Literal,
    Optional,
    TextIO,
    Type,
    TypeVar,
    Union,
    cast,
    overload,
)

from ._errors import (
    CleanupWarning,
    DirectorySyncError,
    UnsafeTargetError,
    UnsupportedDurabilityError,
)

PathLike = Union[str, bytes, os.PathLike[str], os.PathLike[bytes]]
PathValue = Union[str, bytes]
WriteMode = Literal["w", "wt", "wb"]
_T = TypeVar("_T", bound=IO[Any])


class Durability(str, Enum):
    """Requested persistence level for a committed replacement."""

    NONE = "none"
    DATA = "data"
    FULL = "full"


_DURABILITIES = {member.value: member for member in Durability}
_ALLOWED_MODES = frozenset(("w", "wt", "wb"))
_WINDOWS_TRANSIENT_REPLACE_ERRORS = frozenset((5, 32, 33))
_WINDOWS_REPLACE_TIMEOUT = 5.0
_WINDOWS_REPLACE_INITIAL_DELAY = 0.005
_WINDOWS_REPLACE_MAX_DELAY = 0.05


class _TargetLock:
    """A registry entry retained while an owner or waiter can reference it."""

    def __init__(self) -> None:
        # Context bodies may deliberately nest a replacement of the same
        # target.  The outer context must remain protected while its owner can
        # reacquire the per-target lock without self-deadlocking.
        self.lock = threading.RLock()
        self.references = 0


_WINDOWS_TARGET_LOCKS: dict[str, _TargetLock] = {}
_WINDOWS_TARGET_LOCKS_GUARD = threading.Lock()


def _windows_target_locks_enabled() -> bool:
    """Return whether this process needs the Windows target-lock registry."""

    return os.name == "nt"


def _windows_replace_retries_enabled() -> bool:
    """Return whether replacements need Windows sharing-violation retries."""

    return os.name == "nt"


def _coerce_path(path: PathLike) -> PathValue:
    value = os.fspath(path)
    if not value:
        raise ValueError("path must not be empty")

    # Resolve relative paths once so changing cwd while the context is open does
    # not redirect either the staging file or the final replacement.
    absolute = os.path.abspath(value)
    basename = os.path.basename(absolute)
    empty: Union[str, bytes] = b"" if isinstance(absolute, bytes) else ""
    if basename == empty:
        raise ValueError("path must name a file, not a directory ending in a separator")
    return absolute


def _coerce_durability(value: Union[Durability, str]) -> Durability:
    if isinstance(value, Durability):
        return value
    if not isinstance(value, str):
        raise TypeError("durability must be a Durability value or string")
    try:
        return _DURABILITIES[value]
    except KeyError:
        allowed = ", ".join(sorted(_DURABILITIES))
        raise ValueError(f"durability must be one of: {allowed}") from None


def _validate_permissions(permissions: Optional[int]) -> None:
    if permissions is None:
        return
    if isinstance(permissions, bool) or not isinstance(permissions, int):
        raise TypeError("permissions must be an integer mode or None")
    if not 0 <= permissions <= 0o777:
        raise ValueError("permissions must be between 0o000 and 0o777")


def _validate_target(path: PathValue, *, allow_hardlinks: bool) -> Optional[os.stat_result]:
    try:
        target_stat = os.lstat(path)
    except FileNotFoundError:
        return None

    if stat.S_ISLNK(target_stat.st_mode):
        raise UnsafeTargetError(
            path,
            "refusing to replace a symbolic link; resolve and pass the intended "
            "regular-file path explicitly",
        )
    if not stat.S_ISREG(target_stat.st_mode):
        raise UnsafeTargetError(path, "target exists and is not a regular file")
    if target_stat.st_nlink > 1 and not allow_hardlinks:
        raise UnsafeTargetError(
            path,
            "target has multiple hard links; atomic replacement would update only "
            "this directory entry (pass allow_hardlinks=True to accept that)",
        )
    return target_stat


def _full_durability_supported() -> bool:
    # The Python standard library exposes a portable file fsync, but not a
    # portable Windows directory-handle flush. Refuse rather than silently
    # claiming a guarantee we cannot implement.
    return os.name == "posix" and hasattr(os, "fsync")


def _ensure_durability_supported(path: PathValue, durability: Durability) -> None:
    if durability is Durability.FULL and not _full_durability_supported():
        raise UnsupportedDurabilityError(
            path,
            "full durability requires parent-directory synchronization, which "
            "is not available through the Python standard library on this platform",
        )


def _temporary_name_parts(path: PathValue) -> tuple[PathValue, PathValue, PathValue]:
    parent = os.path.dirname(path)
    if isinstance(path, bytes):
        return parent, b".filecommit-", b".tmp"
    return parent, ".filecommit-", ".tmp"


def _windows_target_lock_key(path: PathValue) -> str:
    """Return a lexical Windows lock key without resolving parent symlinks."""

    return os.path.normcase(os.fsdecode(path))


def _acquire_windows_target_lock(path: PathValue) -> _TargetLock:
    """Acquire a per-target lock and retain its registry entry while waiting."""

    key = _windows_target_lock_key(path)
    with _WINDOWS_TARGET_LOCKS_GUARD:
        entry = _WINDOWS_TARGET_LOCKS.get(key)
        if entry is None:
            entry = _TargetLock()
            _WINDOWS_TARGET_LOCKS[key] = entry
        entry.references += 1
    try:
        entry.lock.acquire()
    except BaseException:
        # An interrupted blocking acquisition has not been attached to an
        # _AtomicOpen instance, so its entry reference must be retired here.
        with _WINDOWS_TARGET_LOCKS_GUARD:
            entry.references -= 1
            if entry.references == 0 and _WINDOWS_TARGET_LOCKS.get(key) is entry:
                del _WINDOWS_TARGET_LOCKS[key]
        raise
    return entry


def _release_windows_target_lock(entry: _TargetLock) -> None:
    """Release one owner reference and discard an entry with no waiters."""

    entry.lock.release()
    with _WINDOWS_TARGET_LOCKS_GUARD:
        entry.references -= 1
        if entry.references == 0:
            for key, candidate in list(_WINDOWS_TARGET_LOCKS.items()):
                if candidate is entry:
                    del _WINDOWS_TARGET_LOCKS[key]
                    break


def _apply_permissions(fd: int, temporary_path: PathValue, permissions: int) -> None:
    if hasattr(os, "fchmod"):
        os.fchmod(fd, permissions)
    else:  # pragma: no cover - exercised on platforms without os.fchmod
        os.chmod(temporary_path, permissions)


def _sync_parent_directory(path: PathValue) -> None:
    parent = os.path.dirname(path)
    flags = os.O_RDONLY
    flags |= getattr(os, "O_DIRECTORY", 0)
    flags |= getattr(os, "O_CLOEXEC", 0)
    directory_fd = os.open(parent, flags)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


def _warn_cleanup_failure(temporary_path: PathValue, cause: BaseException) -> None:
    warnings.warn(CleanupWarning(temporary_path, cause), stacklevel=3)


def _replace(temporary_path: PathValue, path: PathValue) -> None:
    """Replace *path*, retrying transient sharing violations on Windows."""

    if not _windows_replace_retries_enabled():
        os.replace(temporary_path, path)
        return

    deadline = time.monotonic() + _WINDOWS_REPLACE_TIMEOUT
    delay = _WINDOWS_REPLACE_INITIAL_DELAY
    while True:
        try:
            os.replace(temporary_path, path)
            return
        except OSError as error:
            # ``os.replace`` reports a normal reader-held destination as
            # WinError 5 on current CPython/Windows.  32 and 33 are the
            # documented sharing and lock violations.  Do not retry generic
            # PermissionError instances or unrelated access failures.
            if getattr(error, "winerror", None) not in _WINDOWS_TRANSIENT_REPLACE_ERRORS:
                raise
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise
            time.sleep(min(delay, remaining))
            delay = min(delay * 2, _WINDOWS_REPLACE_MAX_DELAY)


class _AtomicOpen(Generic[_T], ContextManager[_T]):
    """One-shot context manager implementing staged replacement."""

    def __init__(
        self,
        path: PathLike,
        mode: WriteMode,
        *,
        buffering: int,
        encoding: Optional[str],
        errors: Optional[str],
        newline: Optional[str],
        permissions: Optional[int],
        preserve_permissions: bool,
        durability: Union[Durability, str],
        allow_hardlinks: bool,
    ) -> None:
        if mode not in _ALLOWED_MODES:
            raise ValueError("mode must be exactly 'w', 'wt', or 'wb'")
        if not isinstance(buffering, int) or isinstance(buffering, bool):
            raise TypeError("buffering must be an integer")
        if not isinstance(preserve_permissions, bool):
            raise TypeError("preserve_permissions must be bool")
        if not isinstance(allow_hardlinks, bool):
            raise TypeError("allow_hardlinks must be bool")
        _validate_permissions(permissions)

        binary = mode == "wb"
        if binary and any(value is not None for value in (encoding, errors, newline)):
            raise ValueError("encoding, errors, and newline are invalid in binary mode")
        if not binary and encoding is None:
            encoding = "utf-8"
        if errors is None and not binary:
            errors = "strict"

        self._path = _coerce_path(path)
        self._mode = mode
        self._buffering = buffering
        self._encoding = encoding
        self._errors = errors
        self._newline = newline
        self._permissions = permissions
        self._preserve_permissions = preserve_permissions
        self._durability = _coerce_durability(durability)
        self._allow_hardlinks = allow_hardlinks

        self._file: Optional[_T] = None
        self._temporary_path: Optional[PathValue] = None
        self._target_lock: Optional[_TargetLock] = None
        self._entered = False
        self._finished = False

    def __enter__(self) -> _T:
        if self._entered:
            raise RuntimeError("an atomic_open context manager can be entered only once")
        self._entered = True

        try:
            if _windows_target_locks_enabled():
                self._target_lock = _acquire_windows_target_lock(self._path)

            _ensure_durability_supported(self._path, self._durability)
            _validate_target(self._path, allow_hardlinks=self._allow_hardlinks)

            parent, prefix, suffix = _temporary_name_parts(self._path)
            fd, temporary_path = tempfile.mkstemp(
                dir=parent,  # type: ignore[arg-type]
                prefix=prefix,  # type: ignore[arg-type]
                suffix=suffix,  # type: ignore[arg-type]
            )
            self._temporary_path = temporary_path

            try:
                file_object: IO[Any]
                if self._mode == "wb":
                    file_object = os.fdopen(fd, self._mode, buffering=self._buffering)
                else:
                    file_object = os.fdopen(
                        fd,
                        self._mode,
                        buffering=self._buffering,
                        encoding=self._encoding,
                        errors=self._errors,
                        newline=self._newline,
                    )
                self._file = cast(_T, file_object)
                return self._file
            except BaseException:
                try:
                    os.close(fd)
                except OSError:
                    pass
                self._cleanup_staging_file(warn=True)
                raise
        except BaseException:
            self._release_target_lock()
            raise

    def __exit__(
        self,
        exc_type: Optional[Type[BaseException]],
        exc: Optional[BaseException],
        traceback: Optional[TracebackType],
    ) -> Literal[False]:
        if not self._entered:
            raise RuntimeError("an atomic_open context cannot exit before it is entered")
        if self._finished:
            return False
        self._finished = True

        try:
            if self._file is None or self._temporary_path is None:
                return False

            if exc_type is not None:
                self._close_after_failure()
                self._cleanup_staging_file(warn=True)
                return False

            try:
                self._commit()
            except BaseException:
                self._close_after_failure()
                self._cleanup_staging_file(warn=True)
                raise
            return False
        finally:
            self._release_target_lock()

    def _release_target_lock(self) -> None:
        entry = self._target_lock
        if entry is None:
            return
        self._target_lock = None
        _release_windows_target_lock(entry)

    def _commit(self) -> None:
        assert self._file is not None
        assert self._temporary_path is not None

        if self._file.closed:
            raise ValueError("the staging file was closed before the context exited")

        self._file.flush()
        target_stat = _validate_target(
            self._path,
            allow_hardlinks=self._allow_hardlinks,
        )

        if self._permissions is not None:
            final_permissions = self._permissions
        elif self._preserve_permissions and target_stat is not None:
            # Special mode bits are intentionally not copied onto newly written
            # content. Callers who genuinely require them must restore them
            # explicitly after considering their platform's security semantics.
            final_permissions = stat.S_IMODE(target_stat.st_mode) & 0o777
        else:
            final_permissions = 0o600

        _apply_permissions(
            self._file.fileno(),
            self._temporary_path,
            final_permissions,
        )

        if self._durability in (Durability.DATA, Durability.FULL):
            os.fsync(self._file.fileno())

        self._file.close()
        _replace(self._temporary_path, self._path)
        self._temporary_path = None

        if self._durability is Durability.FULL:
            try:
                _sync_parent_directory(self._path)
            except OSError as cause:
                raise DirectorySyncError(self._path, cause) from cause

    def _close_after_failure(self) -> None:
        if self._file is None or self._file.closed:
            return
        try:
            self._file.close()
        except BaseException:
            # A body or commit exception is already active. Preserve it; the
            # subsequent unlink attempt reports separately if the open handle
            # prevents staging-file cleanup on this platform.
            pass

    def _cleanup_staging_file(self, *, warn: bool) -> None:
        temporary_path = self._temporary_path
        if temporary_path is None:
            return
        try:
            os.unlink(temporary_path)
        except FileNotFoundError:
            pass
        except PermissionError:
            # Windows represents a read-only file with a file attribute and may
            # reject unlinking it. A staging file can become read-only when the
            # requested final permissions are applied before a failed replace.
            try:
                os.chmod(temporary_path, 0o600)
                os.unlink(temporary_path)
            except FileNotFoundError:
                pass
            except BaseException as cause:
                if warn:
                    _warn_cleanup_failure(temporary_path, cause)
        except BaseException as cause:
            if warn:
                _warn_cleanup_failure(temporary_path, cause)
        finally:
            self._temporary_path = None


@overload
def atomic_open(
    path: PathLike,
    mode: Literal["w", "wt"] = "w",
    *,
    buffering: int = -1,
    encoding: Optional[str] = None,
    errors: Optional[str] = None,
    newline: Optional[str] = None,
    permissions: Optional[int] = None,
    preserve_permissions: bool = True,
    durability: Union[Durability, str] = Durability.NONE,
    allow_hardlinks: bool = False,
) -> ContextManager[TextIO]: ...


@overload
def atomic_open(
    path: PathLike,
    mode: Literal["wb"],
    *,
    buffering: int = -1,
    encoding: None = None,
    errors: None = None,
    newline: None = None,
    permissions: Optional[int] = None,
    preserve_permissions: bool = True,
    durability: Union[Durability, str] = Durability.NONE,
    allow_hardlinks: bool = False,
) -> ContextManager[BinaryIO]: ...


def atomic_open(
    path: PathLike,
    mode: WriteMode = "w",
    *,
    buffering: int = -1,
    encoding: Optional[str] = None,
    errors: Optional[str] = None,
    newline: Optional[str] = None,
    permissions: Optional[int] = None,
    preserve_permissions: bool = True,
    durability: Union[Durability, str] = Durability.NONE,
    allow_hardlinks: bool = False,
) -> ContextManager[IO[Any]]:
    """Return a context manager for explicit atomic file replacement.

    The target is untouched until the context exits normally. An exception from
    the body closes and removes the staging file. The supplied path is resolved
    to an absolute lexical path when this function is called, so later working-
    directory changes cannot redirect the commit.

    ``mode`` must be ``"w"``, ``"wt"``, or ``"wb"``. Text mode defaults to
    UTF-8 rather than the process locale. New targets default to mode ``0o600``;
    existing ordinary permission bits are preserved unless overridden.

    ``durability`` accepts :class:`Durability` or its string values. ``"data"``
    synchronizes the staged file before replacement. ``"full"`` additionally
    synchronizes the parent directory and can raise :class:`DirectorySyncError`
    after the new target has already been committed.

    Symbolic links and non-regular targets are always refused. Multiply linked
    regular files require ``allow_hardlinks=True`` because replacement updates
    only the named directory entry.
    """

    return _AtomicOpen(
        path,
        mode,
        buffering=buffering,
        encoding=encoding,
        errors=errors,
        newline=newline,
        permissions=permissions,
        preserve_permissions=preserve_permissions,
        durability=durability,
        allow_hardlinks=allow_hardlinks,
    )


def replace_bytes(
    path: PathLike,
    data: Union[bytes, bytearray, memoryview],
    *,
    permissions: Optional[int] = None,
    preserve_permissions: bool = True,
    durability: Union[Durability, str] = Durability.NONE,
    allow_hardlinks: bool = False,
) -> None:
    """Atomically replace ``path`` with bytes-like ``data``.

    ``bytearray`` and ``memoryview`` inputs are snapshotted before staging, so
    concurrent caller-side mutation cannot change an in-progress replacement.
    See :func:`atomic_open` for permission, target, and durability semantics.
    """

    if not isinstance(data, (bytes, bytearray, memoryview)):
        raise TypeError("data must be bytes, bytearray, or memoryview")
    payload = bytes(data)
    with atomic_open(
        path,
        "wb",
        permissions=permissions,
        preserve_permissions=preserve_permissions,
        durability=durability,
        allow_hardlinks=allow_hardlinks,
    ) as file_object:
        view = memoryview(payload)
        while view:
            written = file_object.write(view)
            if written is None:
                raise OSError("binary writer returned no byte count")
            if written <= 0:
                raise OSError("binary writer made no forward progress")
            view = view[written:]


def replace_text(
    path: PathLike,
    text: str,
    *,
    encoding: str = "utf-8",
    errors: str = "strict",
    newline: Optional[str] = None,
    permissions: Optional[int] = None,
    preserve_permissions: bool = True,
    durability: Union[Durability, str] = Durability.NONE,
    allow_hardlinks: bool = False,
) -> None:
    """Atomically replace ``path`` with ``text``.

    Encoding occurs while writing the private staging file. Encoding failures
    therefore leave an existing target unchanged. See :func:`atomic_open` for
    permission, target, and durability semantics.
    """

    if not isinstance(text, str):
        raise TypeError("text must be str")
    with atomic_open(
        path,
        "w",
        encoding=encoding,
        errors=errors,
        newline=newline,
        permissions=permissions,
        preserve_permissions=preserve_permissions,
        durability=durability,
        allow_hardlinks=allow_hardlinks,
    ) as file_object:
        remaining = text
        while remaining:
            written = file_object.write(remaining)
            if written is None:
                raise OSError("text writer returned no character count")
            if written <= 0:
                raise OSError("text writer made no forward progress")
            remaining = remaining[written:]
