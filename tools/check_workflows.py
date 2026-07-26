"""Enforce a small security policy for GitHub Actions workflows."""

from __future__ import annotations

import argparse
import re
from pathlib import Path
from typing import Iterable, Optional, Sequence

try:
    from tools._project import ROOT, read_text
except ModuleNotFoundError:  # Running as ``python tools/check_workflows.py``.
    from _project import ROOT, read_text

WORKFLOW_DIRECTORY = ROOT / ".github" / "workflows"
_SHA = re.compile(r"^[0-9a-f]{40}$")
_USES = re.compile(r"^\s*-?\s*uses:\s*(?P<value>[^#\s]+)")
_FORBIDDEN_TRIGGERS = ("pull_request_target:",)
_FORBIDDEN_TOKENS = ("permissions: write-all", "secrets: inherit")


class WorkflowPolicyError(RuntimeError):
    """Raised when one or more workflows violate repository policy."""


def workflow_files(directory: Path = WORKFLOW_DIRECTORY) -> list[Path]:
    """Return workflows in deterministic order."""

    return sorted((*directory.glob("*.yml"), *directory.glob("*.yaml")))


def external_action_reference(value: str) -> Optional[tuple[str, str]]:
    """Split an external action reference into action path and revision."""

    value = value.strip("'\"")
    if value.startswith(("./", "docker://")):
        return None
    if "@" not in value:
        raise WorkflowPolicyError(f"external action has no revision: {value}")
    action, revision = value.rsplit("@", 1)
    if len(action.split("/")) < 2:
        raise WorkflowPolicyError(f"invalid external action reference: {value}")
    return action, revision


def _top_level_permissions(lines: Sequence[str]) -> dict[str, str]:
    for index, line in enumerate(lines):
        if line.rstrip() != "permissions:":
            continue
        permissions: dict[str, str] = {}
        for child in lines[index + 1 :]:
            if child and not child.startswith(" "):
                break
            match = re.match(r"^  ([A-Za-z-]+):\s*([A-Za-z-]+)\s*$", child)
            if match is not None:
                permissions[match.group(1)] = match.group(2)
        return permissions
    return {}


def validate_workflow(path: Path) -> list[str]:
    """Return policy violations for one workflow."""

    text = read_text(path)
    lines = text.splitlines()
    errors: list[str] = []

    permissions = _top_level_permissions(lines)
    if permissions != {"contents": "read"}:
        errors.append("top-level permissions must be exactly 'contents: read'")

    for forbidden in _FORBIDDEN_TRIGGERS:
        if any(line.strip() == forbidden for line in lines):
            errors.append(f"forbidden event trigger: {forbidden[:-1]}")
    for forbidden in _FORBIDDEN_TOKENS:
        if forbidden in text:
            errors.append(f"forbidden workflow construct: {forbidden}")

    for line_number, line in enumerate(lines, start=1):
        match = _USES.match(line)
        if match is None:
            continue
        value = match.group("value")
        try:
            reference = external_action_reference(value)
        except WorkflowPolicyError as error:
            errors.append(f"line {line_number}: {error}")
            continue
        if reference is None:
            continue
        _, revision = reference
        if _SHA.fullmatch(revision) is None:
            errors.append(
                f"line {line_number}: action revision must be a full lowercase SHA: {value}"
            )

    if "timeout-minutes:" not in text:
        errors.append("workflow jobs must set timeout-minutes")

    if path.name != "release.yml":
        if "id-token: write" in text:
            errors.append("OIDC token permission is allowed only in release.yml")
        if "contents: write" in text:
            errors.append("repository write permission is allowed only in release.yml")

    return errors


def validate_workflows(paths: Optional[Iterable[Path]] = None) -> None:
    """Validate workflows and raise one aggregated error."""

    selected = list(paths if paths is not None else workflow_files())
    if not selected:
        raise WorkflowPolicyError("no GitHub Actions workflows found")

    failures: list[str] = []
    for path in selected:
        for error in validate_workflow(path):
            failures.append(f"{path.relative_to(ROOT)}: {error}")
    if failures:
        raise WorkflowPolicyError("\n".join(failures))


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="*", type=Path)
    arguments = parser.parse_args(argv)
    try:
        validate_workflows(arguments.paths or None)
    except WorkflowPolicyError as error:
        parser.exit(1, f"workflow policy failed:\n{error}\n")
    print(f"workflow policy passed: {len(arguments.paths or workflow_files())} file(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
