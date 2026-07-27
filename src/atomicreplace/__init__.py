"""Explicit, dependency-free atomic file replacement."""

from ._core import Durability, atomic_open, replace_bytes, replace_text
from ._errors import (
    CleanupWarning,
    DirectorySyncError,
    AtomicReplaceError,
    UnsafeTargetError,
    UnsupportedDurabilityError,
)

__all__ = [
    "CleanupWarning",
    "DirectorySyncError",
    "Durability",
    "AtomicReplaceError",
    "UnsafeTargetError",
    "UnsupportedDurabilityError",
    "atomic_open",
    "replace_bytes",
    "replace_text",
]

__version__ = "0.1.0"
