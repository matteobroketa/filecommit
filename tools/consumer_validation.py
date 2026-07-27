"""Validate the built wheel from the perspective of an isolated consumer."""

from __future__ import annotations

import argparse
import os
import subprocess
import tempfile
import venv
from pathlib import Path
from typing import Optional, Sequence

try:
    from tools.install_smoke import resolve_wheel
except ModuleNotFoundError:  # Running as ``python tools/consumer_validation.py``.
    from install_smoke import resolve_wheel

_CONSUMER = r"""
import importlib.metadata
import inspect
import json
import os
import re
import tempfile
from pathlib import Path

before = set(Path.cwd().iterdir())
import filecommit
after = set(Path.cwd().iterdir())
assert before == after, "import created files in the consumer directory"

snapshot = json.loads(Path(os.environ["FILECOMMIT_SNAPSHOT"]).read_text(encoding="utf-8"))
assert filecommit.__version__ == snapshot["version"]
assert filecommit.__all__ == snapshot["exports"]
for name, signature in snapshot["signatures"].items():
    assert str(inspect.signature(getattr(filecommit, name))) == signature

distribution = importlib.metadata.distribution("filecommit")
assert any(str(item).replace("\\", "/").endswith("filecommit/py.typed")
           for item in (distribution.files or [])), "py.typed is not installed"
for requirement in distribution.metadata.get_all("Requires-Dist", []):
    assert "extra == 'dev'" in requirement or 'extra == "dev"' in requirement

with tempfile.TemporaryDirectory() as directory:
    root = Path(directory)
    readme = Path(os.environ["FILECOMMIT_README"]).read_text(encoding="utf-8")
    examples = [
        example
        for example in re.findall(r"```python" + "\n" + r"(.*?)```", readme, flags=re.DOTALL)
        if example.startswith("from filecommit")
    ]
    assert len(examples) == 2, "README Python examples changed without consumer validation"
    previous_directory = Path.cwd()
    os.chdir(root)
    try:
        for example in examples:
            exec(compile(example, "README.md", "exec"), {})
    finally:
        os.chdir(previous_directory)
    assert (root / "settings.toml").read_text(encoding="utf-8") == "enabled = true" + "\n"
    assert (root / "manifest.json").read_text(encoding="utf-8").startswith("{")

    configuration = root / "settings.toml"
    manifest = root / "manifest.json"
    artifact = root / "artifact.bin"
    filecommit.replace_text(configuration, "enabled = true\n")
    with filecommit.atomic_open(manifest, "w", permissions=0o600) as stream:
        stream.write('{"files": ["artifact.bin"]}\n')
    filecommit.replace_bytes(artifact, memoryview(b"\x00\x01consumer"))
    assert configuration.read_text(encoding="utf-8") == "enabled = true\n"
    assert manifest.read_text(encoding="utf-8").startswith("{")
    assert artifact.read_bytes() == b"\x00\x01consumer"
print("wheel consumer validation passed")
"""


def _environment_python(directory: Path) -> Path:
    return directory / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def validate(wheel: Path, snapshot: Path) -> None:
    """Install only *wheel* in a fresh venv and run representative consumers."""

    wheel = resolve_wheel(wheel).resolve()
    snapshot = snapshot.resolve()
    if not snapshot.is_file():
        raise ValueError(f"snapshot does not exist: {snapshot}")
    with tempfile.TemporaryDirectory(prefix="filecommit-consumer-") as directory:
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
        consumer_directory = Path(directory) / "consumer"
        consumer_directory.mkdir()
        environment_variables = os.environ.copy()
        environment_variables["FILECOMMIT_SNAPSHOT"] = str(snapshot)
        environment_variables["FILECOMMIT_README"] = str(
            Path(__file__).resolve().parents[1] / "README.md"
        )
        subprocess.run(
            [str(python), "-I", "-c", _CONSUMER],
            cwd=consumer_directory,
            env=environment_variables,
            check=True,
        )


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("wheel", type=Path, help="wheel file or directory containing one wheel")
    parser.add_argument(
        "--snapshot",
        type=Path,
        default=Path("tests/public_api_snapshot.json"),
        help="committed public compatibility snapshot",
    )
    arguments = parser.parse_args(argv)
    try:
        validate(arguments.wheel, arguments.snapshot)
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        parser.exit(1, f"wheel consumer validation failed: {error}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
