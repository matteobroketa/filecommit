"""Small, dependency-free helpers shared by repository tooling."""

from __future__ import annotations

import ast
import re
from pathlib import Path
from typing import Iterable, Optional

ROOT = Path(__file__).resolve().parents[1]
PYPROJECT = ROOT / "pyproject.toml"
PACKAGE_INIT = ROOT / "src" / "atomicreplace" / "__init__.py"
CHANGELOG = ROOT / "CHANGELOG.md"
REPOSITORY_PLACEHOLDER = "https://github.com/OWNER/REPOSITORY"

_PROJECT_VERSION = re.compile(
    r'^version\s*=\s*"(?P<version>[^"\r\n]+)"\s*$',
    re.MULTILINE,
)
_REQUIRES_PYTHON = re.compile(
    r'^requires-python\s*=\s*"(?P<specifier>[^"\r\n]+)"\s*$',
    re.MULTILINE,
)
_CHANGELOG_HEADING = re.compile(
    r"^## (?P<version>\d+\.\d+\.\d+(?:[A-Za-z0-9.-]+)?)"
    r"(?: - (?P<date>\d{4}-\d{2}-\d{2}))?\s*$",
    re.MULTILINE,
)


class ProjectError(RuntimeError):
    """Raised when repository metadata is missing or inconsistent."""


def read_text(path: Path) -> str:
    """Read UTF-8 text without accepting a byte-order mark."""

    data = path.read_bytes()
    if data.startswith(b"\xef\xbb\xbf"):
        raise ProjectError(f"{path.relative_to(ROOT)} must not contain a UTF-8 BOM")
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ProjectError(f"{path.relative_to(ROOT)} must be valid UTF-8") from error


def write_text(path: Path, text: str) -> None:
    """Write UTF-8 text with stable LF newlines on every platform."""

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as stream:
        stream.write(text)


def project_version(path: Path = PYPROJECT) -> str:
    """Return the static project version from ``pyproject.toml``."""

    match = _PROJECT_VERSION.search(read_text(path))
    if match is None:
        raise ProjectError("pyproject.toml must contain a static project version")
    return match.group("version")


def requires_python(path: Path = PYPROJECT) -> str:
    """Return the declared ``Requires-Python`` specifier."""

    match = _REQUIRES_PYTHON.search(read_text(path))
    if match is None:
        raise ProjectError("pyproject.toml must declare requires-python")
    return match.group("specifier")


def package_version(path: Path = PACKAGE_INIT) -> str:
    """Read ``__version__`` without importing the package."""

    tree = ast.parse(read_text(path), filename=str(path))
    for statement in tree.body:
        if not isinstance(statement, (ast.Assign, ast.AnnAssign)):
            continue
        targets: Iterable[ast.expr]
        value: Optional[ast.expr]
        if isinstance(statement, ast.Assign):
            targets = statement.targets
            value = statement.value
        else:
            targets = (statement.target,)
            value = statement.value
        if any(isinstance(target, ast.Name) and target.id == "__version__" for target in targets):
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                return value.value
            raise ProjectError("__version__ must be assigned a string literal")
    raise ProjectError("src/atomicreplace/__init__.py must define __version__")


def changelog_section(version: str, path: Path = CHANGELOG) -> str:
    """Return one release section, excluding its heading."""

    text = read_text(path)
    headings = list(_CHANGELOG_HEADING.finditer(text))
    for index, heading in enumerate(headings):
        if heading.group("version") != version:
            continue
        start = heading.end()
        end = headings[index + 1].start() if index + 1 < len(headings) else len(text)
        section = text[start:end].strip()
        if not section:
            raise ProjectError(f"changelog section {version!r} is empty")
        return section + "\n"
    raise ProjectError(f"CHANGELOG.md has no section for version {version}")


def changelog_date(version: str, path: Path = CHANGELOG) -> Optional[str]:
    """Return the release date attached to a changelog version."""

    for heading in _CHANGELOG_HEADING.finditer(read_text(path)):
        if heading.group("version") == version:
            return heading.group("date")
    raise ProjectError(f"CHANGELOG.md has no section for version {version}")


def repository_url(path: Path = PYPROJECT) -> Optional[str]:
    """Return the configured repository URL, if present."""

    text = read_text(path)
    match = re.search(r'^Repository\s*=\s*"(?P<url>[^"\r\n]+)"\s*$', text, re.MULTILINE)
    return match.group("url") if match is not None else None
