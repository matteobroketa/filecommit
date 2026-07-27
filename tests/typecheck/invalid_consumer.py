"""Documented invalid public calls rejected by strict mypy."""

from filecommit import replace_bytes, replace_text

replace_text("configuration.toml", b"not text")  # E: Argument 2 has incompatible type
replace_bytes("artifact.bin", "not bytes")  # E: Argument 2 has incompatible type
