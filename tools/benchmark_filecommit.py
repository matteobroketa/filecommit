"""Characterize filecommit performance without enforcing elapsed-time thresholds."""

from __future__ import annotations

import argparse
import json
import platform
import shutil
import statistics
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Callable, Optional, Sequence

from filecommit import Durability, FileCommitError, replace_bytes

_PAYLOAD_SIZES = (0, 1024, 64 * 1024, 1024 * 1024, 16 * 1024 * 1024)


def _percentile(samples: Sequence[float], percentage: int) -> float:
    ordered = sorted(samples)
    return ordered[max(0, (len(ordered) * percentage + 99) // 100 - 1)]


def _measure(operation: Callable[[], None], samples: int, payload_size: int) -> dict[str, Any]:
    observed: list[float] = []
    for _ in range(samples):
        start = time.perf_counter()
        operation()
        observed.append(time.perf_counter() - start)
    median = statistics.median(observed)
    return {
        "raw_seconds": observed,
        "minimum_seconds": min(observed),
        "maximum_seconds": max(observed),
        "median_seconds": median,
        "p90_seconds": _percentile(observed, 90),
        "p95_seconds": _percentile(observed, 95),
        "throughput_bytes_per_second": None if not median else payload_size / median,
    }


def _contended(root: Path, payload: bytes, same_target: bool) -> None:
    targets = (
        (root / "same.bin", root / "same.bin")
        if same_target
        else (
            root / "first.bin",
            root / "second.bin",
        )
    )
    barrier = threading.Barrier(2)
    errors: list[BaseException] = []

    def writer(target: Path) -> None:
        try:
            barrier.wait(5)
            replace_bytes(target, payload)
        except BaseException as error:  # Surface worker failure to the benchmark caller.
            errors.append(error)

    threads = [threading.Thread(target=writer, args=(target,)) for target in targets]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(10)
    if any(thread.is_alive() for thread in threads):
        raise RuntimeError("benchmark writer did not terminate")
    if errors:
        raise RuntimeError("benchmark writer failed") from errors[0]


def _wheel_size() -> dict[str, Any]:
    """Build a wheel in a temporary directory when the optional frontend exists."""

    with tempfile.TemporaryDirectory(prefix="filecommit-benchmark-wheel-") as directory:
        output = Path(directory)
        source = Path(__file__).resolve().parents[1]
        checkout = output / "source"
        shutil.copytree(
            source,
            checkout,
            ignore=shutil.ignore_patterns(".git", "build", "dist", "*.egg-info", "__pycache__"),
        )
        result = subprocess.run(
            [sys.executable, "-m", "build", "--wheel", "--outdir", str(output), str(checkout)],
            cwd=output,
            check=False,
            capture_output=True,
        )
        wheels = list(output.glob("*.whl"))
        if result.returncode or len(wheels) != 1:
            return {"status": "unavailable", "reason": "optional build frontend failed"}
        return {"status": "measured", "bytes": wheels[0].stat().st_size}


def run(*, samples: int, payload_sizes: Sequence[int] = _PAYLOAD_SIZES) -> dict[str, Any]:
    """Return portable benchmark observations for the requested payload sizes."""

    report: dict[str, Any] = {
        "platform": {
            "system": platform.system(),
            "release": platform.release(),
            "python": sys.version,
        },
        "parameters": {"samples": samples, "payload_sizes": list(payload_sizes)},
        "results": [],
        "wheel_size": _wheel_size(),
        "windows_retry": {"status": "not_forced", "reason": "native handle contention only"},
    }
    with tempfile.TemporaryDirectory(prefix="filecommit-benchmark-") as directory:
        root = Path(directory)
        import_start = time.perf_counter()
        imported = subprocess.run(
            [sys.executable, "-I", "-c", "import filecommit"], check=False, capture_output=True
        )
        report["isolated_import"] = (
            {"status": "measured", "seconds": time.perf_counter() - import_start}
            if imported.returncode == 0
            else {"status": "unavailable", "reason": "package is not installed for isolated import"}
        )
        for payload_size in payload_sizes:
            payload = b"x" * payload_size
            for durability in (Durability.NONE, Durability.DATA, Durability.FULL):
                target = root / f"{payload_size}-{durability.value}.bin"
                try:
                    result = _measure(
                        lambda target=target, payload=payload, durability=durability: replace_bytes(
                            target, payload, durability=durability
                        ),
                        samples,
                        payload_size,
                    )
                except (FileCommitError, OSError) as error:
                    result = {"status": "unsupported", "error": repr(error)}
                else:
                    result["status"] = "measured"
                report["results"].append(
                    {
                        "operation": "replace_existing",
                        "durability": durability.value,
                        "bytes": payload_size,
                        **result,
                    }
                )
            for operation, existing in (("replace_new", False), ("replace_existing", True)):
                target = root / f"{operation}-{payload_size}.bin"
                if existing:
                    target.write_bytes(payload)
                result = _measure(
                    lambda target=target, payload=payload: replace_bytes(target, payload),
                    samples,
                    payload_size,
                )
                report["results"].append(
                    {
                        "operation": operation,
                        "durability": "none",
                        "bytes": payload_size,
                        "status": "measured",
                        **result,
                    }
                )
            direct_target = root / f"direct-{payload_size}.bin"
            direct = _measure(
                lambda direct_target=direct_target, payload=payload: direct_target.write_bytes(
                    payload
                ),
                samples,
                payload_size,
            )
            report["results"].append(
                {
                    "operation": "direct_write",
                    "durability": "none",
                    "bytes": payload_size,
                    "status": "measured",
                    **direct,
                }
            )
            for same_target in (True, False):
                contention = _measure(
                    lambda payload=payload, same_target=same_target: _contended(
                        root, payload, same_target
                    ),
                    samples,
                    payload_size,
                )
                report["results"].append(
                    {
                        "operation": "same_target_contention"
                        if same_target
                        else "different_target_contention",
                        "durability": "none",
                        "bytes": payload_size,
                        "status": "measured",
                        **contention,
                    }
                )
    return report


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--samples", type=int, default=7)
    parser.add_argument("--json-output", type=Path, required=True)
    arguments = parser.parse_args(argv)
    if arguments.samples < 1:
        parser.error("--samples must be positive")
    report = run(samples=arguments.samples)
    arguments.json_output.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(f"benchmark evidence written to {arguments.json_output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
