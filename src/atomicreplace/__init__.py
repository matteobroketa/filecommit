"""Explicit, dependency-free atomic file replacement."""
# ruff: noqa: RUF022

from ._core import Durability, atomic_open, replace_bytes, replace_text
from ._errors import (
    AtomicReplaceError,
    CleanupWarning,
    DirectorySyncError,
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
