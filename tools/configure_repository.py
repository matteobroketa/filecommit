"""Replace repository URL placeholders before the first release."""

from __future__ import annotations

import argparse
import re
from pathlib import Path
from typing import Optional, Sequence

try:
    from tools._project import PYPROJECT, REPOSITORY_PLACEHOLDER, write_text
except ModuleNotFoundError:  # Running as ``python tools/configure_repository.py``.
    from _project import PYPROJECT, REPOSITORY_PLACEHOLDER, write_text

_REPOSITORY = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")


def configure(repository: str, path: Path = PYPROJECT) -> bool:
    """Set project URLs and return whether the file changed."""

    if _REPOSITORY.fullmatch(repository) is None:
        raise ValueError("repository must have the form OWNER/REPOSITORY")
    text = path.read_text(encoding="utf-8")
    url = f"https://github.com/{repository}"
    updated = text.replace(REPOSITORY_PLACEHOLDER, url)
    if updated == text:
        if url in text:
            return False
        raise ValueError("repository URL placeholder was not found")
    write_text(path, updated)
    return True


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("repository", help="GitHub OWNER/REPOSITORY")
    arguments = parser.parse_args(argv)
    try:
        changed = configure(arguments.repository)
    except ValueError as error:
        parser.exit(2, f"configuration failed: {error}\n")
    print("repository URLs configured" if changed else "repository URLs already configured")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
