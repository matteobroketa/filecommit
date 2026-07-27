"""Run deterministic filesystem replacement scenarios against a small reference model."""

from __future__ import annotations

import argparse
import os
import random
import stat
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Sequence, Union
from unittest import mock

from filecommit import Durability, UnsafeTargetError, atomic_open, replace_bytes, replace_text

DEFAULT_SEEDS = (0xF11EC0, 0xB17E5, 0xC0FFEE, 0x5EED)
EXTENDED_SEEDS = tuple(range(16))


class _CustomPath:
    def __init__(self, path: Path) -> None:
        self._path = path

    def __fspath__(self) -> str:
        return str(self._path)


class _FailingFlush:
    def __init__(self, stream: object, error: OSError) -> None:
        self._stream = stream
        self._error = error

    @property
    def closed(self) -> bool:
        return self._stream.closed  # type: ignore[union-attr]

    def flush(self) -> None:
        raise self._error

    def __getattr__(self, name: str) -> object:
        return getattr(self._stream, name)


@dataclass
class _ModelState:
    payload: Optional[bytes] = None
    permissions: Optional[int] = None


def _payload(randomizer: random.Random, *, binary: bool) -> Union[bytes, str, memoryview]:
    size = randomizer.choice((0, 1, 31, 4095, 4096, 4097, 65_537))
    if binary:
        data = bytes(randomizer.getrandbits(8) for _ in range(size))
        if randomizer.randrange(5) == 0:
            return memoryview(data)[::2]
        return data
    edge_values = ("", "\x00", "Grüezi — 東京", "line 1\r\nline 2\n", "😀")
    if randomizer.randrange(4) == 0:
        return randomizer.choice(edge_values)
    return "".join(chr(0x20 + randomizer.randrange(95)) for _ in range(size))


def _path_argument(target: Path, randomizer: random.Random) -> tuple[object, Optional[Path]]:
    choice = randomizer.choice(("absolute", "custom", "bytes", "relative"))
    if choice == "absolute":
        return str(target), None
    if choice == "custom":
        return _CustomPath(target), None
    if choice == "bytes":
        return os.fsencode(target), None
    return target.name, target.parent


def _durability(randomizer: random.Random) -> Durability:
    values = [Durability.NONE, Durability.DATA]
    if os.name == "posix":
        values.append(Durability.FULL)
    return randomizer.choice(values)


def _expected_permissions(
    state: _ModelState, permissions: Optional[int], preserve_permissions: bool
) -> int:
    if permissions is not None:
        return permissions
    if preserve_permissions and state.permissions is not None:
        return state.permissions
    return 0o600


def _assert_state(target: Path, state: _ModelState, *, seed: int, case: int) -> None:
    prefix = f"seed={seed} case={case}"
    if state.payload is None:
        if target.exists():
            raise AssertionError(f"{prefix}: model expects an absent target")
        return
    if not target.is_file() or target.read_bytes() != state.payload:
        raise AssertionError(f"{prefix}: target content differs from the reference model")
    if os.name == "posix":
        observed = stat.S_IMODE(target.stat().st_mode)
        if observed != state.permissions:
            raise AssertionError(
                f"{prefix}: expected permissions {state.permissions:o}, observed {observed:o}"
            )


def _run_case(
    root: Path, state: _ModelState, randomizer: random.Random, seed: int, case: int
) -> None:
    target = root / "state.bin"
    operation = randomizer.choice(
        (
            "binary",
            "text",
            "abort",
            "encoding",
            "flush failure",
            "sync failure",
            "replace failure",
            "mutation",
        )
    )
    path, relative_root = _path_argument(target, randomizer)
    old_directory: Optional[str] = None
    if relative_root is not None:
        old_directory = os.getcwd()
        os.chdir(relative_root)

    try:
        if operation == "binary":
            payload = _payload(randomizer, binary=True)
            assert isinstance(payload, (bytes, memoryview))
            permissions = randomizer.choice((None, 0o600, 0o640))
            preserve_permissions = randomizer.choice((True, False))
            replace_bytes(
                path,
                payload,
                permissions=permissions,
                preserve_permissions=preserve_permissions,
                durability=_durability(randomizer),
            )
            state.payload = bytes(payload)
            state.permissions = _expected_permissions(state, permissions, preserve_permissions)
        elif operation == "text":
            payload = _payload(randomizer, binary=False)
            assert isinstance(payload, str)
            permissions = randomizer.choice((None, 0o600, 0o640))
            preserve_permissions = randomizer.choice((True, False))
            replace_text(
                path,
                payload,
                newline="",
                permissions=permissions,
                preserve_permissions=preserve_permissions,
                durability=_durability(randomizer),
            )
            state.payload = payload.encode("utf-8")
            state.permissions = _expected_permissions(state, permissions, preserve_permissions)
        elif operation == "abort":
            payload = _payload(randomizer, binary=False)
            assert isinstance(payload, str)
            try:
                with atomic_open(path, durability=_durability(randomizer)) as stream:
                    stream.write(payload)
                    raise RuntimeError("model abort")
            except RuntimeError as error:
                if str(error) != "model abort":
                    raise
        elif operation == "encoding":
            try:
                replace_text(path, "\ud800", encoding="utf-8", durability=_durability(randomizer))
            except UnicodeEncodeError:
                pass
            else:
                raise AssertionError(
                    f"seed={seed} case={case}: lone surrogate unexpectedly encoded"
                )
        elif operation == "flush failure":
            error = OSError("model flush failure")
            real_fdopen = os.fdopen

            def failing_fdopen(fd: int, *arguments: object, **keywords: object) -> _FailingFlush:
                return _FailingFlush(real_fdopen(fd, *arguments, **keywords), error)

            with mock.patch("filecommit._core.os.fdopen", side_effect=failing_fdopen):
                try:
                    replace_text(path, "new", newline="")
                except OSError as observed:
                    if observed is not error:
                        raise
                else:
                    raise AssertionError(f"seed={seed} case={case}: flush failure did not raise")
        elif operation == "sync failure":
            error = OSError("model sync failure")
            with mock.patch("filecommit._core.os.fsync", side_effect=error):
                try:
                    replace_text(path, "new", newline="", durability=Durability.DATA)
                except OSError as observed:
                    if observed is not error:
                        raise
                else:
                    raise AssertionError(f"seed={seed} case={case}: sync failure did not raise")
        elif operation == "replace failure":
            error = OSError("model replacement failure")
            with mock.patch("filecommit._core.os.replace", side_effect=error):
                try:
                    replace_text(path, "new", newline="")
                except OSError as observed:
                    if observed is not error:
                        raise
                else:
                    raise AssertionError(f"seed={seed} case={case}: replace failure did not raise")
        else:
            try:
                with atomic_open(path, durability=_durability(randomizer)) as stream:
                    stream.write("new")
                    if target.exists():
                        target.unlink()
                    target.mkdir()
            except UnsafeTargetError:
                pass
            finally:
                if target.is_dir():
                    target.rmdir()
            state.payload = None
            state.permissions = None
    finally:
        if old_directory is not None:
            os.chdir(old_directory)

    _assert_state(target, state, seed=seed, case=case)


def run(seeds: Sequence[int], *, cases: int) -> None:
    """Run every seed deterministically, raising an actionable assertion on divergence."""

    if cases <= 0:
        raise ValueError("cases must be positive")
    for seed in seeds:
        randomizer = random.Random(seed)
        with tempfile.TemporaryDirectory(prefix="filecommit-model-") as directory:
            root = Path(directory)
            state = _ModelState()
            for case in range(cases):
                try:
                    _run_case(root, state, randomizer, seed, case)
                except BaseException as error:
                    raise AssertionError(
                        f"model validation failed: seed={seed} case={case}"
                    ) from error


def _seed(value: str) -> int:
    return int(value, 0)


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", action="append", type=_seed, help="repeatable deterministic seed")
    parser.add_argument("--cases", type=int, default=100, help="cases to run for each seed")
    arguments = parser.parse_args(argv)
    seeds = tuple(arguments.seed) if arguments.seed else DEFAULT_SEEDS
    try:
        run(seeds, cases=arguments.cases)
    except (AssertionError, OSError, ValueError) as error:
        parser.exit(1, f"model validation failed: {error}\n")
    print(f"passed: seeds={','.join(str(seed) for seed in seeds)} cases={arguments.cases}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
