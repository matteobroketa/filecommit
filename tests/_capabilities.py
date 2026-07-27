"""Small capability probes shared by native filesystem tests."""

from __future__ import annotations

import os
import socket
import unittest
from pathlib import Path


def create_symlink_or_skip(
    test: unittest.TestCase, link: Path, destination: Path, *, directory: bool
) -> None:
    """Create a symlink or skip with the exact unavailable capability."""

    try:
        os.symlink(destination, link, target_is_directory=directory)
    except (AttributeError, NotImplementedError, OSError) as error:
        test.skipTest(f"cannot create {'directory' if directory else 'file'} symlink: {error}")


def unix_socket_or_skip(test: unittest.TestCase) -> socket.socket:
    """Return an AF_UNIX socket or skip when the filesystem lacks that capability."""

    if not hasattr(socket, "AF_UNIX"):
        test.skipTest("AF_UNIX sockets are unavailable")
    try:
        return socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    except OSError as error:
        test.skipTest(f"cannot create AF_UNIX socket: {error}")


def long_path_or_skip(test: unittest.TestCase, root: Path, *, minimum_length: int = 270) -> Path:
    """Create a long nested path or skip when runner policy disables it."""

    path = root
    try:
        while len(str(path)) < minimum_length:
            path /= "segment0123456789"
        path.mkdir(parents=True)
    except OSError as error:
        test.skipTest(f"filesystem or runner policy rejects long paths: {error}")
    return path
