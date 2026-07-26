"""Manual concurrent reader/writer stress test for local filesystems."""

from __future__ import annotations

import argparse
import hashlib
import tempfile
import threading
from pathlib import Path

from filecommit import replace_bytes


def run(*, writers: int, replacements: int, payload_size: int) -> None:
    with tempfile.TemporaryDirectory() as directory:
        target = Path(directory) / "stress.bin"
        stop = threading.Event()
        errors: list[BaseException] = []
        errors_lock = threading.Lock()
        payloads = []

        for writer_id in range(writers):
            marker = f"writer={writer_id};".encode("ascii")
            body = (marker * ((payload_size // len(marker)) + 1))[:payload_size]
            payloads.append(hashlib.sha256(body).digest() + body)

        def writer(payload: bytes) -> None:
            try:
                for _ in range(replacements):
                    replace_bytes(target, payload)
            except BaseException as error:
                with errors_lock:
                    errors.append(error)

        def reader() -> None:
            try:
                while not stop.is_set():
                    try:
                        value = target.read_bytes()
                    except FileNotFoundError:
                        continue
                    if len(value) < 32 or hashlib.sha256(value[32:]).digest() != value[:32]:
                        raise AssertionError("reader observed partial or mixed content")
            except BaseException as error:
                with errors_lock:
                    errors.append(error)

        reader_thread = threading.Thread(target=reader, name="filecommit-reader")
        reader_thread.start()
        writer_threads = [
            threading.Thread(target=writer, args=(payload,), name=f"filecommit-writer-{index}")
            for index, payload in enumerate(payloads)
        ]
        for thread in writer_threads:
            thread.start()
        for thread in writer_threads:
            thread.join()
        stop.set()
        reader_thread.join()

        if errors:
            details = "; ".join(repr(error) for error in errors)
            raise RuntimeError(f"stress test failures: {details}")
        if target.read_bytes() not in payloads:
            raise AssertionError("final target is not one complete writer payload")
        if list(Path(directory).glob(".filecommit-*.tmp")):
            raise AssertionError("successful stress run left staging files")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--writers", type=int, default=12)
    parser.add_argument("--replacements", type=int, default=80)
    parser.add_argument("--payload-size", type=int, default=96_000)
    arguments = parser.parse_args()
    run(
        writers=arguments.writers,
        replacements=arguments.replacements,
        payload_size=arguments.payload_size,
    )
    print(
        f"passed: {arguments.writers * arguments.replacements} replacements; "
        "no partial reader observations"
    )


if __name__ == "__main__":
    main()
