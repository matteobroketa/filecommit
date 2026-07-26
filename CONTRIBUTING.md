# Contributing

Changes must preserve the package's narrow contract, Python 3.9 compatibility,
and zero runtime dependency policy. Prefer a documented Python standard-library
primitive over a new abstraction or dependency.

## Local checks

Install exact development tools:

```console
python -m pip install -e ".[dev]"
```

Run the same tasks used by GitHub Actions:

```console
python tools/ci.py validate
python tools/ci.py test
python tools/ci.py coverage
python tools/ci.py quality
python tools/ci.py package
python tools/ci.py stress
```

`python tools/ci.py all` runs the complete local sequence. The commands are
cross-platform and do not require a POSIX shell.

## Change requirements

Before changing commit order or filesystem calls, document the state before the
irreversible replacement, the exact commit point, and every possible
post-commit error. A platform fallback must be explicit; never silently weaken
a requested guarantee.

Tests must cover normal success, caller-body failure, operating-system failure,
cleanup behavior, target mutation during staging, and relevant platform
behavior. Platform-dependent tests must skip with a concrete reason rather than
turning an unexpected failure into a pass.

External GitHub Actions must use a reviewed full commit SHA. Runtime dependencies
remain prohibited unless the fundamental package contract is reconsidered in a
public design decision.

See `docs/CI_ARCHITECTURE.md` for merge and release gates.
