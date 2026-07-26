"""Rebuild a wheel from the source distribution and compare its contents."""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path
from typing import Optional, Sequence, Tuple

try:
    from tools.validate_dist import compare_archives
except ModuleNotFoundError:  # Running as ``python tools/rebuild_from_sdist.py``.
    from validate_dist import compare_archives


def resolve_distributions(
    path: Path, reference_wheel: Optional[Path] = None
) -> Tuple[Path, Path]:
    """Resolve an sdist and reference wheel from paths or one directory."""

    if path.is_dir():
        sdists = sorted(path.glob("*.tar.gz"))
        wheels = sorted(path.glob("*.whl"))
        if len(sdists) != 1 or len(wheels) != 1:
            raise ValueError(
                f"expected one source archive and one wheel in {path}; "
                f"found {len(sdists)} and {len(wheels)}"
            )
        return sdists[0], wheels[0]
    if reference_wheel is None:
        raise ValueError("a reference wheel is required when the first argument is an archive")
    return path, reference_wheel


def _extract_safely(archive: tarfile.TarFile, destination: Path) -> None:
    """Extract regular files and directories without trusting archive paths."""

    destination = destination.resolve()
    for member in archive.getmembers():
        if member.issym() or member.islnk() or not (member.isfile() or member.isdir()):
            raise RuntimeError(f"unsupported source archive member: {member.name}")
        target = (destination / member.name).resolve()
        try:
            target.relative_to(destination)
        except ValueError as error:
            raise RuntimeError(f"unsafe source archive path: {member.name}") from error
        if member.isdir():
            target.mkdir(parents=True, exist_ok=True)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        source = archive.extractfile(member)
        if source is None:
            raise RuntimeError(f"could not read source archive member: {member.name}")
        with source, target.open("wb") as output:
            shutil.copyfileobj(source, output)


def rebuild(sdist: Path, reference_wheel: Path) -> None:
    """Build from an sdist in a temporary directory and compare wheel payloads."""

    sdist = sdist.resolve()
    reference_wheel = reference_wheel.resolve()
    with tempfile.TemporaryDirectory(prefix="filecommit-sdist-") as directory:
        root = Path(directory)
        source = root / "source"
        output = root / "dist"
        source.mkdir()
        output.mkdir()
        with tarfile.open(sdist, "r:gz") as archive:
            _extract_safely(archive, source)
        projects = [path for path in source.iterdir() if path.is_dir()]
        if len(projects) != 1:
            raise RuntimeError("source archive must extract to exactly one directory")
        project = projects[0]
        frontend_probe = subprocess.run(
            [
                sys.executable,
                "-c",
                "import importlib.util\n"
                "try:\n"
                "    available = importlib.util.find_spec('build.__main__') is not None\n"
                "except ModuleNotFoundError:\n"
                "    available = False\n"
                "raise SystemExit(not available)\n",
            ],
            cwd=project,
            check=False,
        )
        if frontend_probe.returncode == 0:
            subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "build",
                    "--wheel",
                    "--outdir",
                    str(output),
                    ".",
                ],
                cwd=project,
                check=True,
            )
        else:
            # Local fallback for a checkout without the optional build frontend.
            # GitHub release jobs always exercise the isolated PEP 517 path above.
            subprocess.run(
                [
                    sys.executable,
                    "-c",
                    "from setuptools.build_meta import build_wheel; "
                    f"print(build_wheel({str(output)!r}))",
                ],
                cwd=project,
                check=True,
            )
        wheels = list(output.glob("*.whl"))
        if len(wheels) != 1:
            raise RuntimeError("sdist rebuild did not produce exactly one wheel")
        compare_archives(reference_wheel, wheels[0])


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("sdist_or_directory", type=Path)
    parser.add_argument("reference_wheel", nargs="?", type=Path)
    arguments = parser.parse_args(argv)
    try:
        sdist, wheel = resolve_distributions(
            arguments.sdist_or_directory, arguments.reference_wheel
        )
        rebuild(sdist, wheel)
    except (
        OSError,
        ValueError,
        RuntimeError,
        subprocess.CalledProcessError,
        tarfile.TarError,
    ) as error:
        parser.exit(1, f"source rebuild failed: {error}\n")
    print("source archive rebuilt to an equivalent wheel")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
