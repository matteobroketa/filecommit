"""Public exceptions raised by :mod:`filecommit`."""

from __future__ import annotations

import errno
import os
from typing import Union

PathValue = Union[str, bytes]
_NOT_SUPPORTED = getattr(errno, "ENOTSUP", getattr(errno, "EOPNOTSUPP", errno.EINVAL))


class FileCommitError(OSError):
    """Base class for filecommit-specific operating-system errors."""


class UnsafeTargetError(FileCommitError):
    """Raised when replacing the target would have surprising semantics."""

    def __init__(self, path: PathValue, reason: str) -> None:
        self.path = path
        self.reason = reason
        super().__init__(errno.EINVAL, reason, path)


class UnsupportedDurabilityError(FileCommitError):
    """Raised before writing when the requested durability is unsupported."""

    def __init__(self, path: PathValue, reason: str) -> None:
        self.path = path
        self.reason = reason
        super().__init__(_NOT_SUPPORTED, reason, path)


class DirectorySyncError(FileCommitError):
    """Raised when the file was replaced but its directory could not be synced.

    ``committed`` is always true. The target already contains the new data, but
    the requested crash-durability guarantee could not be completed.
    """

    committed = True

    def __init__(self, path: PathValue, cause: OSError) -> None:
        self.path = path
        self.cause = cause
        message = f"the target was replaced, but synchronizing its parent directory failed: {cause}"
        super().__init__(cause.errno or errno.EIO, message, path)


class CleanupWarning(RuntimeWarning):
    """Warns that a staging file could not be removed after another failure."""

    def __init__(self, temporary_path: PathValue, cause: BaseException) -> None:
        self.temporary_path = temporary_path
        self.cause = cause
        super().__init__(
            f"could not remove filecommit staging file {os.fsdecode(temporary_path)!r}: {cause}"
        )
