"""Multiprocess concurrent reader/writer stress test for local filesystems."""

from __future__ import annotations

import argparse
import hashlib
import multiprocessing
import queue
import tempfile
import time
from pathlib import Path
from typing import Optional, Sequence

from atomicreplace import replace_bytes

_FRAME_SIZE = 32
_TRANSIENT_WINDOWS_ERRORS = frozenset((5, 32, 33))


def _framed_payload(writer_id: int, payload_size: int) -> bytes:
    marker = f"writer={writer_id};".encode("ascii")
    body = (marker * ((payload_size // len(marker)) + 1))[:payload_size]
    return hashlib.sha256(body).digest() + body


def _report_error(
    errors: multiprocessing.queues.Queue[tuple[str, int, str]],
    role: str,
    index: int,
    error: BaseException,
) -> None:
    errors.put((role, index, repr(error)))


def _writer(
    target: str,
    payload: bytes,
    replacements: int,
    start: multiprocessing.synchronize.Event,
    errors: multiprocessing.queues.Queue[tuple[str, int, str]],
    index: int,
) -> None:
    try:
        if not start.wait(15):
            raise TimeoutError("writer start signal timed out")
        for _ in range(replacements):
            replace_bytes(target, payload)
    except BaseException as error:
        _report_error(errors, "writer", index, error)


def _reader(
    targets: tuple[str, ...],
    stop: multiprocessing.synchronize.Event,
    start: multiprocessing.synchronize.Event,
    errors: multiprocessing.queues.Queue[tuple[str, int, str]],
    index: int,
) -> None:
    try:
        if not start.wait(15):
            raise TimeoutError("reader start signal timed out")
        while not stop.is_set():
            for target in targets:
                try:
                    value = Path(target).read_bytes()
                except FileNotFoundError:
                    continue
                except PermissionError:
                    # A reader can race a replacement on Windows when its
                    # open request conflicts with delete sharing.  It has not
                    # observed data, so retry rather than treating this as a
                    # torn-read result.
                    continue
                except OSError as error:
                    if getattr(error, "winerror", None) in _TRANSIENT_WINDOWS_ERRORS:
                        continue
                    raise
                if (
                    len(value) < _FRAME_SIZE
                    or hashlib.sha256(value[_FRAME_SIZE:]).digest() != value[:_FRAME_SIZE]
                ):
                    raise AssertionError(f"reader observed partial or mixed content at {target}")
    except BaseException as error:
        _report_error(errors, "reader", index, error)


def _join_or_terminate(processes: Sequence[multiprocessing.Process], timeout: float) -> None:
    deadline = time.monotonic() + timeout
    for process in processes:
        remaining = deadline - time.monotonic()
        process.join(max(0.0, remaining))
    remaining_processes = [process for process in processes if process.is_alive()]
    for process in remaining_processes:
        process.terminate()
    for process in remaining_processes:
        process.join(5)
    if remaining_processes:
        names = ", ".join(process.name for process in remaining_processes)
        raise TimeoutError(f"stress child processes exceeded {timeout:.1f}s: {names}")


def _drain_errors(errors: multiprocessing.queues.Queue[tuple[str, int, str]]) -> list[str]:
    details: list[str] = []
    while True:
        try:
            role, index, error = errors.get_nowait()
        except queue.Empty:
            return details
        details.append(f"{role}[{index}]: {error}")


def run(
    *,
    writers: int,
    replacements: int,
    payload_size: int,
    readers: int = 1,
    mode: str = "same",
    timeout: float = 60.0,
) -> None:
    """Exercise independent writers/readers using a spawn-compatible context."""

    if writers <= 0 or replacements <= 0 or payload_size < 0 or readers <= 0 or timeout <= 0:
        raise ValueError("writers, replacements, readers, and timeout must be positive")
    if mode not in {"same", "different"}:
        raise ValueError("mode must be 'same' or 'different'")

    context = multiprocessing.get_context("spawn")
    with tempfile.TemporaryDirectory(prefix="atomicreplace-stress-") as directory:
        root = Path(directory)
        targets = (
            (root / "stress.bin",)
            if mode == "same"
            else tuple(root / f"stress-{writer_id}.bin" for writer_id in range(writers))
        )
        payloads = tuple(_framed_payload(writer_id, payload_size) for writer_id in range(writers))
        start = context.Event()
        stop = context.Event()
        errors = context.Queue()
        reader_processes = [
            context.Process(
                target=_reader,
                args=(tuple(map(str, targets)), stop, start, errors, index),
                name=f"atomicreplace-reader-{index}",
            )
            for index in range(readers)
        ]
        writer_processes = [
            context.Process(
                target=_writer,
                args=(
                    str(targets[0] if mode == "same" else targets[index]),
                    payload,
                    replacements,
                    start,
                    errors,
                    index,
                ),
                name=f"atomicreplace-writer-{index}",
            )
            for index, payload in enumerate(payloads)
        ]

        for process in [*reader_processes, *writer_processes]:
            process.start()
        start.set()
        try:
            _join_or_terminate(writer_processes, timeout)
        finally:
            stop.set()
            _join_or_terminate(reader_processes, timeout)

        details = _drain_errors(errors)
        failed = [
            f"{process.name} exited with {process.exitcode}"
            for process in [*writer_processes, *reader_processes]
            if process.exitcode != 0
        ]
        if details or failed:
            raise RuntimeError("stress test failures: " + "; ".join([*details, *failed]))

        if mode == "same":
            if targets[0].read_bytes() not in payloads:
                raise AssertionError("final target is not one complete writer payload")
        else:
            for target, payload in zip(targets, payloads):
                if target.read_bytes() != payload:
                    raise AssertionError(f"final target differs from its writer payload: {target}")
        if list(root.glob(".atomicreplace-*.tmp")):
            raise AssertionError("successful stress run left staging files")


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--writers", type=int, default=12)
    parser.add_argument("--replacements", type=int, default=80)
    parser.add_argument("--payload-size", type=int, default=96_000)
    parser.add_argument("--readers", type=int, default=1)
    parser.add_argument("--mode", choices=("same", "different"), default="same")
    parser.add_argument("--timeout", type=float, default=60.0)
    arguments = parser.parse_args(argv)
    try:
        run(
            writers=arguments.writers,
            replacements=arguments.replacements,
            payload_size=arguments.payload_size,
            readers=arguments.readers,
            mode=arguments.mode,
            timeout=arguments.timeout,
        )
    except (OSError, RuntimeError, TimeoutError, ValueError) as error:
        parser.exit(1, f"atomicity stress failed: {error}\n")
    replacements_completed = arguments.writers * arguments.replacements
    print(
        f"passed: mode={arguments.mode}; {replacements_completed} replacements; "
        "no partial reader observations"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
