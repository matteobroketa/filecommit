"""Inspect built wheel and source archives using only the standard library."""

from __future__ import annotations

import argparse
import hashlib
import tarfile
import zipfile
from email.message import Message
from email.parser import BytesParser
from pathlib import Path
from typing import Mapping, Optional, Sequence

try:
    from tools._project import ROOT, project_version, write_text
except ModuleNotFoundError:  # Running as ``python tools/validate_dist.py``.
    from _project import ROOT, project_version, write_text

_FORBIDDEN_PARTS = {"__pycache__", ".git", ".github"}
_FORBIDDEN_SUFFIXES = {".pyc", ".pyo"}
_EXPECTED_WHEEL_FILES = {
    "filecommit/__init__.py",
    "filecommit/_core.py",
    "filecommit/_errors.py",
    "filecommit/py.typed",
}
_EXPECTED_SDIST_FILES = {
    "CHANGELOG.md",
    "CONTRIBUTING.md",
    "LICENSE",
    "README.md",
    "ROADMAP.md",
    "SECURITY.md",
    "VALIDATION.md",
    "pyproject.toml",
    "src/filecommit/__init__.py",
    "src/filecommit/_core.py",
    "src/filecommit/_errors.py",
    "src/filecommit/py.typed",
}


class DistributionValidationError(RuntimeError):
    """Raised when a distribution archive violates release policy."""


def _forbidden(name: str) -> bool:
    path = Path(name)
    forbidden_part = any(part in _FORBIDDEN_PARTS for part in path.parts)
    return forbidden_part or path.suffix in _FORBIDDEN_SUFFIXES


def _wheel_metadata(archive: zipfile.ZipFile) -> tuple[str, str]:
    metadata_names = [name for name in archive.namelist() if name.endswith(".dist-info/METADATA")]
    wheel_names = [name for name in archive.namelist() if name.endswith(".dist-info/WHEEL")]
    if len(metadata_names) != 1 or len(wheel_names) != 1:
        raise DistributionValidationError("wheel must contain exactly one METADATA and one WHEEL")
    return metadata_names[0], wheel_names[0]


def _validate_requires_dist(message: Message) -> None:
    for requirement in message.get_all("Requires-Dist", []):
        if 'extra == "dev"' not in requirement and "extra == 'dev'" not in requirement:
            raise DistributionValidationError(f"wheel has a runtime dependency: {requirement}")


def validate_wheel(path: Path, *, version: str) -> None:
    """Validate wheel layout, metadata, and dependency policy."""

    expected_name = f"filecommit-{version}-py3-none-any.whl"
    if path.name != expected_name:
        raise DistributionValidationError(f"unexpected wheel filename: {path.name}")

    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        if any(_forbidden(name) for name in names):
            raise DistributionValidationError("wheel contains generated or repository-only files")
        missing = _EXPECTED_WHEEL_FILES.difference(names)
        if missing:
            raise DistributionValidationError(f"wheel is missing files: {sorted(missing)}")

        metadata_name, wheel_name = _wheel_metadata(archive)
        message = BytesParser().parsebytes(archive.read(metadata_name))
        if message["Name"] != "filecommit":
            raise DistributionValidationError("wheel metadata has the wrong project name")
        if message["Version"] != version:
            raise DistributionValidationError("wheel metadata version does not match the project")
        if message["Requires-Python"] != ">=3.9":
            raise DistributionValidationError("wheel metadata must declare Requires-Python >=3.9")
        _validate_requires_dist(message)

        wheel_text = archive.read(wheel_name).decode("utf-8")
        if "Root-Is-Purelib: true" not in wheel_text or "Tag: py3-none-any" not in wheel_text:
            raise DistributionValidationError("wheel must be a pure-Python py3-none-any wheel")

        records = [name for name in names if name.endswith(".dist-info/RECORD")]
        if len(records) != 1:
            raise DistributionValidationError("wheel must contain exactly one RECORD")
        record_names = {
            line.split(",", 1)[0]
            for line in archive.read(records[0]).decode("utf-8").splitlines()
            if line
        }
        if record_names != set(names):
            raise DistributionValidationError(
                "wheel RECORD does not enumerate every archive member"
            )


def validate_sdist(path: Path, *, version: str) -> None:
    """Validate source archive layout and required release files."""

    expected_name = f"filecommit-{version}.tar.gz"
    if path.name != expected_name:
        raise DistributionValidationError(f"unexpected source archive filename: {path.name}")
    prefix = f"filecommit-{version}/"

    with tarfile.open(path, "r:gz") as archive:
        members = archive.getmembers()
        names = [member.name for member in members]
        if any(member.issym() or member.islnk() for member in members):
            raise DistributionValidationError("source archive must not contain links")
        if any(member.name.startswith(("/", "../")) or "/../" in member.name for member in members):
            raise DistributionValidationError("source archive contains an unsafe path")
        if any(_forbidden(name) for name in names):
            raise DistributionValidationError(
                "source archive contains generated or forbidden files"
            )
        if any(not name.startswith(prefix) and name != prefix.rstrip("/") for name in names):
            raise DistributionValidationError("source archive has an unexpected top-level path")

        relative_names = {
            name[len(prefix) :]
            for name in names
            if name.startswith(prefix) and name != prefix
        }
        missing = _EXPECTED_SDIST_FILES.difference(relative_names)
        if missing:
            raise DistributionValidationError(f"source archive is missing files: {sorted(missing)}")


def _archive_payload(path: Path) -> Mapping[str, bytes]:
    if path.suffix == ".whl":
        with zipfile.ZipFile(path) as archive:
            return {
                name: archive.read(name)
                for name in archive.namelist()
                if not name.endswith("/")
            }
    with tarfile.open(path, "r:gz") as archive:
        prefix = archive.getmembers()[0].name.split("/", 1)[0] + "/"
        payload: dict[str, bytes] = {}
        for member in archive.getmembers():
            if not member.isfile():
                continue
            extracted = archive.extractfile(member)
            assert extracted is not None
            name = member.name[len(prefix) :] if member.name.startswith(prefix) else member.name
            payload[name] = extracted.read()
        return payload


def compare_archives(first: Path, second: Path) -> None:
    """Compare archive file content while ignoring container timestamps."""

    first_payload = _archive_payload(first)
    second_payload = _archive_payload(second)
    if first_payload.keys() != second_payload.keys():
        missing = sorted(first_payload.keys() - second_payload.keys())
        added = sorted(second_payload.keys() - first_payload.keys())
        raise DistributionValidationError(
            f"archive members differ; missing={missing!r}, added={added!r}"
        )
    changed = [name for name in first_payload if first_payload[name] != second_payload[name]]
    if changed:
        raise DistributionValidationError(f"archive content differs: {changed}")


def write_checksums(paths: Sequence[Path], destination: Path) -> None:
    """Write deterministic SHA-256 checksums for release artifacts."""

    lines = []
    for path in sorted(paths, key=lambda item: item.name):
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        lines.append(f"{digest}  {path.name}")
    write_text(destination, "\n".join(lines) + "\n")


def validate_directory(directory: Path) -> tuple[Path, Path]:
    """Validate the one wheel and one source archive in a directory."""

    version = project_version()
    wheels = sorted(directory.glob("*.whl"))
    sdists = sorted(directory.glob("*.tar.gz"))
    if len(wheels) != 1 or len(sdists) != 1:
        raise DistributionValidationError(
            f"expected one wheel and one source archive in {directory}; "
            f"found {len(wheels)} wheel(s) and {len(sdists)} source archive(s)"
        )
    validate_wheel(wheels[0], version=version)
    validate_sdist(sdists[0], version=version)
    return wheels[0], sdists[0]


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", nargs="?", type=Path, default=ROOT / "dist")
    parser.add_argument("--write-checksums", type=Path)
    parser.add_argument("--compare-wheel", type=Path)
    parser.add_argument("--compare-sdist", type=Path)
    parser.add_argument("--compare-directory", type=Path)
    arguments = parser.parse_args(argv)

    try:
        wheel, sdist = validate_directory(arguments.directory)
        if arguments.compare_directory is not None:
            other_wheel, other_sdist = validate_directory(arguments.compare_directory)
            compare_archives(wheel, other_wheel)
            compare_archives(sdist, other_sdist)
        if arguments.compare_wheel is not None:
            compare_archives(wheel, arguments.compare_wheel)
        if arguments.compare_sdist is not None:
            compare_archives(sdist, arguments.compare_sdist)
        if arguments.write_checksums is not None:
            write_checksums((wheel, sdist), arguments.write_checksums)
    except (DistributionValidationError, OSError, tarfile.TarError, zipfile.BadZipFile) as error:
        parser.exit(1, f"distribution validation failed: {error}\n")

    print(f"distribution validation passed: {wheel.name}, {sdist.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
