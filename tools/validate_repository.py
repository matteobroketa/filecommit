"""Validate repository invariants without third-party dependencies."""

from __future__ import annotations

import argparse
import ast
import re
import subprocess
from pathlib import Path
from typing import Iterable, Optional, Sequence

try:
    from tools._project import (
        ROOT,
        changelog_section,
        package_version,
        project_version,
        read_text,
        requires_python,
    )
    from tools.check_workflows import WorkflowPolicyError, validate_workflows
except ModuleNotFoundError:  # Running as ``python tools/validate_repository.py``.
    from _project import (
        ROOT,
        changelog_section,
        package_version,
        project_version,
        read_text,
        requires_python,
    )
    from check_workflows import WorkflowPolicyError, validate_workflows

_TEXT_SUFFIXES = {
    ".cfg",
    ".ini",
    ".md",
    ".py",
    ".toml",
    ".txt",
    ".yaml",
    ".yml",
}
_TEXT_NAMES = {".editorconfig", ".gitattributes", ".gitignore", "LICENSE", "MANIFEST.in"}
_IGNORED_DIRECTORIES = {
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "__pycache__",
    "build",
    "dist",
}
_FORBIDDEN_ROOTS = (ROOT / "build", ROOT / "dist", ROOT / ".coverage")


class RepositoryValidationError(RuntimeError):
    """Raised when repository invariants are violated."""


def _repository_files() -> Iterable[Path]:
    for path in sorted(ROOT.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(ROOT)
        ignored = any(
            part in _IGNORED_DIRECTORIES or part.endswith(".egg-info") for part in relative.parts
        )
        if ignored:
            continue
        yield path


def _text_files() -> Iterable[Path]:
    for path in _repository_files():
        if path.suffix in _TEXT_SUFFIXES or path.name in _TEXT_NAMES:
            yield path


def _validate_versions(errors: list[str]) -> None:
    try:
        project = project_version()
        package = package_version()
        if package != project:
            errors.append(f"package version {package!r} does not match project version {project!r}")
        changelog_section(project)
    except RuntimeError as error:
        errors.append(str(error))


def _validate_metadata(errors: list[str]) -> None:
    text = read_text(ROOT / "pyproject.toml")
    if requires_python() != ">=3.9":
        errors.append("requires-python must remain exactly '>=3.9'")
    if not re.search(r"^dependencies\s*=\s*\[\s*\]\s*$", text, re.MULTILINE):
        errors.append("runtime dependencies must remain an explicit empty list")
    if 'requires = ["setuptools==82.0.1"]' not in text:
        errors.append("build backend dependency must remain pinned to setuptools==82.0.1")
    # Build requirements cannot be queried reliably without downloading their
    # metadata.  The required Python 3.9 package-build matrix below is the
    # authoritative compatibility check for this exact offline pin.
    expected_tools = (
        '"build==1.5.0"',
        '"coverage==7.15.2"',
        '"mypy==2.3.0"',
        '"ruff==0.16.0"',
    )
    for tool in expected_tools:
        if tool not in text:
            errors.append(f"development tool must be exactly pinned: {tool}")


def _validate_workflow_architecture(errors: list[str]) -> None:
    required_fragments = {
        "ci.yml": (
            "os: [ubuntu-latest, macos-latest, windows-latest]",
            'python-version: ["3.9", "3.10", "3.11", "3.12", "3.13", "3.14"]',
            "python -m coverage report --fail-under=95",
            "python tools/install_smoke.py dist",
            "python tools/consumer_validation.py dist",
            "python tools/typecheck_consumers.py",
            "python tools/rebuild_from_sdist.py dist",
            "python tools/stress_atomicity.py",
            "name: CI required",
        ),
        "security.yml": (
            "language: [python, actions]",
            "queries: security-extended",
            "actions/dependency-review-action@",
            "github/codeql-action/init@",
            "github/codeql-action/analyze@",
            "name: Security required",
        ),
        "extended.yml": (
            "python tools/repeat_tests.py --runs 15",
            "python tools/model_validation.py --cases 500",
            "python tools/stress_atomicity.py",
            "python tools/filesystem_probe.py . > filesystem-probe.json",
            "filesystem-probe*.json",
            "python tools/benchmark_atomicreplace.py --json-output benchmark.json",
            "benchmark.json",
            "--compare-directory dist-second",
        ),
        "release.yml": (
            'test "$(git cat-file -t "$GITHUB_REF")" = "tag"',
            'git merge-base --is-ancestor "$GITHUB_SHA" refs/remotes/origin/main',
            "os: [ubuntu-latest, macos-latest, windows-latest]",
            'python-version: ["3.9", "3.10", "3.11", "3.12", "3.13", "3.14"]',
            "actions/attest@",
            "pypa/gh-action-pypi-publish@",
            "name: pypi",
            "skip-existing: ${{ github.run_attempt > 1 }}",
        ),
    }

    workflow_root = ROOT / ".github" / "workflows"
    for filename, fragments in required_fragments.items():
        path = workflow_root / filename
        if not path.is_file():
            errors.append(f"required workflow is missing: {filename}")
            continue
        text = read_text(path)
        for fragment in fragments:
            if fragment not in text:
                errors.append(f"{filename} is missing required release control: {fragment}")

    release_path = workflow_root / "release.yml"
    if not release_path.is_file():
        return
    release = read_text(release_path)
    if release.count("sha256sum --check ../release/SHA256SUMS.txt") != 3:
        errors.append(
            "release.yml must verify checksums in attest, publish, and GitHub release jobs"
        )
    if release.count("run: python -m build") != 1:
        errors.append("release.yml must build distributions exactly once")

    for job, next_job in (("attest", "publish"), ("publish", "github-release")):
        start_marker = f"  {job}:\n"
        end_marker = f"  {next_job}:\n"
        if start_marker not in release or end_marker not in release:
            continue
        block = release.split(start_marker, 1)[1].split(end_marker, 1)[0]
        if "actions/checkout@" in block:
            errors.append(f"release job {job!r} must not check out repository source")


def _validate_python_39_syntax(errors: list[str]) -> None:
    for path in _repository_files():
        if path.suffix != ".py":
            continue
        try:
            source = read_text(path)
            ast.parse(source, filename=str(path), feature_version=(3, 9))
        except (SyntaxError, RuntimeError) as error:
            errors.append(f"Python 3.9 syntax failure in {path.relative_to(ROOT)}: {error}")


def _validate_text_hygiene(errors: list[str]) -> None:
    for path in _text_files():
        data = path.read_bytes()
        relative = path.relative_to(ROOT)
        if b"\r" in data:
            errors.append(f"{relative} contains CR bytes; repository text must use LF")
        if data and not data.endswith(b"\n"):
            errors.append(f"{relative} must end with one newline")
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            errors.append(f"{relative} is not valid UTF-8")
            continue
        if any(line.rstrip(" \t") != line for line in text.splitlines()):
            errors.append(f"{relative} contains trailing whitespace")


def _validate_generated_files(errors: list[str]) -> None:
    for path in _FORBIDDEN_ROOTS:
        if path.exists():
            errors.append(f"generated path must not be present in a clean checkout: {path.name}")

    try:
        result = subprocess.run(
            ["git", "ls-files", "-z"],
            cwd=ROOT,
            check=True,
            capture_output=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return

    tracked = [Path(value.decode("utf-8")) for value in result.stdout.split(b"\0") if value]
    generated = [
        path
        for path in tracked
        if "__pycache__" in path.parts
        or any(part.endswith(".egg-info") for part in path.parts)
        or path.suffix in {".pyc", ".pyo"}
    ]
    if generated:
        errors.append("generated files are tracked: " + ", ".join(map(str, sorted(generated))))


def validate_repository(*, allow_generated: bool = False) -> None:
    """Validate all repository policies and raise one aggregated error."""

    errors: list[str] = []
    _validate_versions(errors)
    _validate_metadata(errors)
    _validate_workflow_architecture(errors)
    _validate_python_39_syntax(errors)
    _validate_text_hygiene(errors)
    if not allow_generated:
        _validate_generated_files(errors)
    try:
        validate_workflows()
    except WorkflowPolicyError as error:
        errors.extend(str(error).splitlines())

    if errors:
        message = "\n".join(f"- {error}" for error in errors)
        raise RepositoryValidationError(message)


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--allow-generated",
        action="store_true",
        help="allow local build and cache outputs while validating source invariants",
    )
    arguments = parser.parse_args(argv)
    try:
        validate_repository(allow_generated=arguments.allow_generated)
    except RepositoryValidationError as error:
        parser.exit(1, f"repository validation failed:\n{error}\n")
    print("repository validation passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
