"""Run strict type checks for the documented public consumer examples."""

from __future__ import annotations

import subprocess
import sys

try:
    from tools._project import ROOT
except ModuleNotFoundError:  # Running as ``python tools/typecheck_consumers.py``.
    from _project import ROOT


def main() -> int:
    valid = ROOT / "tests" / "typecheck" / "valid_consumer.py"
    invalid = ROOT / "tests" / "typecheck" / "invalid_consumer.py"
    command = [sys.executable, "-m", "mypy", "--strict"]
    subprocess.run([*command, str(valid)], check=True, cwd=ROOT)
    result = subprocess.run([*command, str(invalid)], check=False, cwd=ROOT, capture_output=True)
    if result.returncode == 0:
        raise SystemExit("invalid documented consumer calls unexpectedly passed mypy")
    output = result.stdout.decode("utf-8", "replace") + result.stderr.decode("utf-8", "replace")
    if output.count("error:") < 2:
        raise SystemExit(f"invalid consumer diagnostics were incomplete:\n{output}")
    print("strict consumer type checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
