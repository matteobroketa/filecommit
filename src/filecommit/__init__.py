"""Explicit, dependency-free atomic file replacement."""

from ._core import Durability, atomic_open, replace_bytes, replace_text
from ._errors import (
    CleanupWarning,
    DirectorySyncError,
    FileCommitError,
    UnsupportedDurabilityError,
    UnsafeTargetError,
)

__all__ = [
    "CleanupWarning",
    "DirectorySyncError",
    "Durability",
    "FileCommitError",
    "UnsupportedDurabilityError",
    "UnsafeTargetError",
    "atomic_open",
    "replace_bytes",
    "replace_text",
]

__version__ = "0.1.0"
