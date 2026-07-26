"""Run the standard-library test suite repeatedly in fresh processes."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path
from typing import Optional, Sequence

ROOT = Path(__file__).resolve().parents[1]


def repeat(runs: int) -> None:
    """Execute the complete suite ``runs`` times, stopping at the first failure."""

    if runs < 1:
        raise ValueError("runs must be at least one")
    command = [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"]
    for index in range(1, runs + 1):
        print(f"test repetition {index}/{runs}", flush=True)
        subprocess.run(command, cwd=ROOT, check=True)


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs", type=int, default=10)
    arguments = parser.parse_args(argv)
    try:
        repeat(arguments.runs)
    except (ValueError, subprocess.CalledProcessError) as error:
        parser.exit(1, f"repeated tests failed: {error}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
