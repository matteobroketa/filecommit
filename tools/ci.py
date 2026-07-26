"""Cross-platform entry point for the same checks used by GitHub Actions."""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Optional, Sequence

ROOT = Path(__file__).resolve().parents[1]


class CommandError(RuntimeError):
    """Raised when a requested maintenance command cannot complete."""


def _run(*arguments: str) -> None:
    subprocess.run([sys.executable, *arguments], cwd=ROOT, check=True)


def _clean() -> None:
    targets = [ROOT / "build", ROOT / "dist", ROOT / ".coverage"]
    targets.extend(ROOT.glob("src/*.egg-info"))
    targets.extend(ROOT.rglob("__pycache__"))
    for path in targets:
        if path.is_dir():
            shutil.rmtree(path)
        elif path.exists():
            path.unlink()


def command(name: str) -> None:
    """Execute one repository task."""

    if name == "clean":
        _clean()
    elif name == "validate":
        _run("tools/validate_repository.py")
    elif name == "test":
        _run("-m", "unittest", "discover", "-s", "tests", "-v")
    elif name == "coverage":
        _run("-m", "coverage", "erase")
        _run("-m", "coverage", "run", "--branch", "-m", "unittest", "discover", "-s", "tests")
        _run("-m", "coverage", "report", "--fail-under=95")
    elif name == "quality":
        _run("tools/validate_repository.py", "--allow-generated")
        _run("-m", "ruff", "format", "--check", ".")
        _run("-m", "ruff", "check", ".")
        _run("-m", "mypy")
    elif name == "package":
        _clean()
        _run("-m", "build")
        _run("tools/validate_dist.py", "dist")
        wheels = list((ROOT / "dist").glob("*.whl"))
        sdists = list((ROOT / "dist").glob("*.tar.gz"))
        if len(wheels) != 1 or len(sdists) != 1:
            raise CommandError("build did not produce one wheel and one source archive")
        _run("tools/install_smoke.py", str(wheels[0]))
        _run("tools/rebuild_from_sdist.py", str(sdists[0]), str(wheels[0]))
    elif name == "stress":
        _run("tools/stress_atomicity.py")
    elif name == "all":
        for item in ("clean", "validate", "test", "coverage", "quality", "package", "stress"):
            command(item)
    else:
        raise CommandError(f"unknown command: {name}")


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command",
        choices=("clean", "validate", "test", "coverage", "quality", "package", "stress", "all"),
    )
    arguments = parser.parse_args(argv)
    try:
        command(arguments.command)
    except (CommandError, OSError, subprocess.CalledProcessError) as error:
        parser.exit(1, f"CI command failed: {error}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
