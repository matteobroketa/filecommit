"""Documented valid public calls accepted by strict mypy."""

from pathlib import Path

from atomicreplace import atomic_open, replace_bytes, replace_text

target = Path("consumer.txt")
replace_text(target, "configuration", durability="data")
replace_bytes(target.with_suffix(".bin"), memoryview(b"artifact"))
with atomic_open(target, "w", permissions=0o600) as stream:
    stream.write("manifest")
