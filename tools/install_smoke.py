"""Install a wheel in an isolated virtual environment and exercise its public API."""

from __future__ import annotations

import argparse
import os
import subprocess
import tempfile
import venv
from pathlib import Path
from typing import Optional, Sequence

_SMOKE = r"""
import importlib.metadata
import tempfile
from pathlib import Path

from atomicreplace import Durability, atomic_open, replace_bytes, replace_text

metadata = importlib.metadata.metadata("atomicreplace")
for requirement in metadata.get_all("Requires-Dist", []):
    if 'extra == "dev"' not in requirement and "extra == 'dev'" not in requirement:
        raise AssertionError(f"runtime dependency found: {requirement}")

with tempfile.TemporaryDirectory() as directory:
    root = Path(directory)
    text = root / "value.txt"
    binary = root / "value.bin"
    replace_text(text, "first")
    with atomic_open(text, durability=Durability.NONE) as stream:
        stream.write("second")
    replace_bytes(binary, b"\x00\x01\x02")
    assert text.read_text(encoding="utf-8") == "second"
    assert binary.read_bytes() == b"\x00\x01\x02"
print("isolated wheel smoke test passed")
"""


def _environment_python(directory: Path) -> Path:
    if os.name == "nt":
        return directory / "Scripts" / "python.exe"
    return directory / "bin" / "python"


def resolve_wheel(path: Path) -> Path:
    """Resolve one wheel from a direct path or distribution directory."""

    if path.is_dir():
        wheels = sorted(path.glob("*.whl"))
        if len(wheels) != 1:
            raise ValueError(f"expected one wheel in {path}; found {len(wheels)}")
        return wheels[0]
    return path


def smoke_test(wheel: Path) -> None:
    """Install one wheel without indexes or dependencies and run a smoke test."""

    wheel = resolve_wheel(wheel).resolve()
    if not wheel.is_file() or wheel.suffix != ".whl":
        raise ValueError(f"not a wheel: {wheel}")
    with tempfile.TemporaryDirectory(prefix="atomicreplace-smoke-") as directory:
        environment = Path(directory) / "venv"
        venv.EnvBuilder(with_pip=True, clear=True).create(environment)
        python = _environment_python(environment)
        subprocess.run(
            [
                str(python),
                "-m",
                "pip",
                "install",
                "--disable-pip-version-check",
                "--no-deps",
                "--no-index",
                str(wheel),
            ],
            check=True,
        )
        subprocess.run([str(python), "-I", "-c", _SMOKE], check=True)


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("wheel", type=Path, help="wheel file or directory containing one wheel")
    arguments = parser.parse_args(argv)
    try:
        smoke_test(arguments.wheel)
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        parser.exit(1, f"isolated wheel smoke test failed: {error}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
