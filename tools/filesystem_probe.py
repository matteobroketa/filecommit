"""Characterize filecommit behavior on one filesystem without modifying user files."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import stat
import subprocess
import sys
import tempfile
import threading
from pathlib import Path
from typing import Any, Optional, Sequence

from filecommit import UnsafeTargetError, replace_bytes, replace_text


class ProbeContractError(RuntimeError):
    """Raised when a required filecommit contract is violated by a probe."""


def _record(results: dict[str, dict[str, Any]], name: str, **values: Any) -> None:
    results[name] = values


def _unsupported(error: OSError) -> dict[str, Any]:
    return {"status": "unsupported", "error": repr(error)}


def _framed(index: int) -> bytes:
    body = (f"probe={index};".encode("ascii") * 128)[:1024]
    return hashlib.sha256(body).digest() + body


def _probe_reader_visibility(target: Path) -> dict[str, Any]:
    payloads = tuple(_framed(index) for index in range(4))
    replace_bytes(target, payloads[0])
    stop = threading.Event()
    errors: list[str] = []

    def reader() -> None:
        while not stop.is_set():
            try:
                value = target.read_bytes()
            except (FileNotFoundError, PermissionError):
                continue
            if len(value) < 32 or hashlib.sha256(value[32:]).digest() != value[:32]:
                errors.append("reader observed partial or mixed content")
                stop.set()
                return

    thread = threading.Thread(target=reader, name="filecommit-filesystem-probe-reader")
    thread.start()
    try:
        for index in range(20):
            replace_bytes(target, payloads[index % len(payloads)])
    finally:
        stop.set()
        thread.join(5)
    if thread.is_alive():
        raise ProbeContractError("reader thread did not terminate")
    if errors:
        raise ProbeContractError(errors[0])
    return {"status": "supported", "replacements": 20}


def _probe_crash_orphan(root: Path) -> dict[str, Any]:
    target = root / "crash-target.txt"
    target.write_text("old", encoding="utf-8")
    script = (
        "import os\n"
        "from filecommit import atomic_open\n"
        f"with atomic_open({str(target)!r}, 'w') as stream:\n"
        "    stream.write('new')\n"
        "    stream.flush()\n"
        "    os._exit(23)\n"
    )
    environment = os.environ.copy()
    source_root = Path(__file__).resolve().parents[1] / "src"
    existing = environment.get("PYTHONPATH")
    environment["PYTHONPATH"] = (
        str(source_root) if not existing else str(source_root) + os.pathsep + existing
    )
    result = subprocess.run(
        [sys.executable, "-c", script], env=environment, check=False, timeout=20
    )
    if result.returncode != 23 or target.read_text(encoding="utf-8") != "old":
        raise ProbeContractError("pre-replacement crash changed the target")
    orphans = sorted(path.name for path in root.glob(".filecommit-*.tmp"))
    for orphan in orphans:
        (root / orphan).unlink()
    return {"status": "supported", "orphan_paths": orphans}


def run(directory: Path) -> dict[str, Any]:
    """Run all probes under a private child directory and return observations."""

    directory = directory.resolve()
    if not directory.is_dir():
        raise ValueError(f"not a directory: {directory}")
    results: dict[str, dict[str, Any]] = {}
    report: dict[str, Any] = {
        "directory": str(directory),
        "platform": {
            "os_name": os.name,
            "platform": sys.platform,
            "python": sys.version.split()[0],
        },
        "filesystem": {},
        "observations": results,
    }
    try:
        details = os.statvfs(directory)
    except (AttributeError, OSError) as error:
        report["filesystem"]["statvfs"] = {"status": "unavailable", "error": repr(error)}
    else:
        report["filesystem"]["statvfs"] = {
            "block_size": details.f_bsize,
            "name_max": details.f_namemax,
        }
    report["filesystem"]["platform_release"] = platform.release()

    with tempfile.TemporaryDirectory(prefix="filecommit-probe-", dir=directory) as temporary:
        root = Path(temporary)
        target = root / "target.bin"
        descriptor, staging = tempfile.mkstemp(prefix=".filecommit-probe-", suffix=".tmp", dir=root)
        os.close(descriptor)
        Path(staging).unlink()
        _record(results, "same_directory_temporary", status="supported")

        replace_bytes(target, b"first")
        replace_bytes(target, b"second")
        if target.read_bytes() != b"second":
            raise ProbeContractError("replacement did not publish the new payload")
        _record(results, "replacement", status="supported", existing_and_absent=True)

        _record(results, "reader_visibility", **_probe_reader_visibility(target))

        with target.open("r+b") as stream:
            try:
                os.fsync(stream.fileno())
            except OSError as error:
                _record(results, "file_fsync", **_unsupported(error))
            else:
                _record(results, "file_fsync", status="supported")

        flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
        try:
            directory_fd = os.open(root, flags)
        except OSError as error:
            _record(results, "parent_directory_fsync", **_unsupported(error))
        else:
            try:
                os.fsync(directory_fd)
            except OSError as error:
                _record(results, "parent_directory_fsync", **_unsupported(error))
            else:
                _record(results, "parent_directory_fsync", status="supported")
            finally:
                os.close(directory_fd)

        replace_text(target, "permissions", permissions=0o600)
        observed_mode = stat.S_IMODE(target.stat().st_mode)
        _record(results, "permissions", status="observed", mode=oct(observed_mode))

        destination = root / "destination.txt"
        destination.write_text("destination", encoding="utf-8")
        link = root / "link.txt"
        try:
            os.symlink(destination, link)
        except (AttributeError, NotImplementedError, OSError) as error:
            _record(results, "symlink_refusal", **_unsupported(error))
        else:
            try:
                replace_text(link, "new")
            except UnsafeTargetError:
                _record(results, "symlink_refusal", status="supported")
            else:
                raise ProbeContractError("symlink target was not refused")

        hardlink = root / "hardlink.txt"
        try:
            os.link(destination, hardlink)
        except (AttributeError, OSError) as error:
            _record(results, "hardlink_refusal", **_unsupported(error))
        else:
            try:
                replace_text(destination, "new")
            except UnsafeTargetError:
                _record(results, "hardlink_refusal", status="supported")
            else:
                raise ProbeContractError("hard-linked target was not refused")

        _record(results, "crash_orphan", **_probe_crash_orphan(root))

    return report


def _human_report(report: dict[str, Any]) -> str:
    lines = [f"filecommit filesystem probe: {report['directory']}"]
    for name, observation in report["observations"].items():
        lines.append(f"- {name}: {observation['status']}")
    return "\n".join(lines)


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path, help="mounted directory to probe")
    arguments = parser.parse_args(argv)
    try:
        report = run(arguments.directory)
    except (OSError, ProbeContractError, ValueError, subprocess.SubprocessError) as error:
        parser.exit(1, f"filesystem probe failed: {error}\n")
    print(_human_report(report), file=sys.stderr)
    print(json.dumps(report, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
