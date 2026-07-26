"""Validate release-tag metadata and produce GitHub release notes."""

from __future__ import annotations

import argparse
import os
import re
from datetime import date
from pathlib import Path
from typing import Optional, Sequence

try:
    from tools._project import (
        REPOSITORY_PLACEHOLDER,
        changelog_date,
        changelog_section,
        package_version,
        project_version,
        read_text,
        repository_url,
        write_text,
    )
except ModuleNotFoundError:  # Running as ``python tools/release_gate.py``.
    from _project import (
        REPOSITORY_PLACEHOLDER,
        changelog_date,
        changelog_section,
        package_version,
        project_version,
        read_text,
        repository_url,
        write_text,
    )

_TAG = re.compile(r"^v(?P<version>\d+\.\d+\.\d+(?:[A-Za-z0-9.-]+)?)$")


class ReleaseGateError(RuntimeError):
    """Raised when a tagged commit is not publishable."""


def validate_release(tag: str) -> tuple[str, str]:
    """Validate release metadata and return version plus release notes."""

    match = _TAG.fullmatch(tag)
    if match is None:
        raise ReleaseGateError("release tag must have the form vMAJOR.MINOR.PATCH")
    version = match.group("version")
    if project_version() != version or package_version() != version:
        raise ReleaseGateError("tag, project version, and package __version__ must match exactly")

    release_date = changelog_date(version)
    if release_date is None:
        raise ReleaseGateError("the release changelog heading must include an ISO date")
    try:
        parsed_date = date.fromisoformat(release_date)
    except ValueError as error:
        raise ReleaseGateError("the release changelog date is not valid ISO-8601") from error
    if parsed_date > date.today():
        raise ReleaseGateError("the release changelog date must not be in the future")

    url = repository_url()
    if url is None or url == REPOSITORY_PLACEHOLDER or "OWNER/REPOSITORY" in url:
        raise ReleaseGateError(
            "configure [project.urls] with tools/configure_repository.py before releasing"
        )

    pyproject = read_text(Path(__file__).resolve().parents[1] / "pyproject.toml")
    if "REPLACE" in pyproject or "TODO" in pyproject:
        raise ReleaseGateError("release metadata contains an unresolved placeholder")

    notes = changelog_section(version)
    return version, notes


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tag", default=os.environ.get("GITHUB_REF_NAME"))
    parser.add_argument("--notes", type=Path)
    parser.add_argument("--github-output", type=Path)
    arguments = parser.parse_args(argv)

    if not arguments.tag:
        parser.exit(2, "release gate requires --tag or GITHUB_REF_NAME\n")
    try:
        version, notes = validate_release(arguments.tag)
    except ReleaseGateError as error:
        parser.exit(1, f"release gate failed: {error}\n")

    if arguments.notes is not None:
        write_text(arguments.notes, notes)
    if arguments.github_output is not None:
        with arguments.github_output.open("a", encoding="utf-8", newline="\n") as output:
            output.write(f"version={version}\n")

    print(f"release gate passed: {arguments.tag}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
