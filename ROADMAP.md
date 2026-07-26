# Agile delivery record

## Sprint 1 — working vertical slice

Completed:

- same-directory secure staging;
- `atomic_open`, `replace_bytes`, and `replace_text`;
- commit with `os.replace()`;
- rollback on caller exceptions;
- zero runtime dependencies.

## Sprint 2 — safety semantics

Completed:

- symbolic-link and non-regular target refusal;
- explicit hard-link policy;
- secure default permissions and permission preservation;
- special-bit removal;
- one-shot context lifecycle checks;
- cleanup retry for read-only staging files;
- explicit pre-commit and post-commit failure semantics.

## Sprint 3 — durability and compatibility

Completed:

- `none`, `data`, and `full` durability levels;
- parent-directory synchronization on POSIX;
- explicit unsupported-platform error;
- string, bytes, path-like, Unicode, and undecodable POSIX byte paths;
- relative paths bound before context opening;
- Python 3.9 compatibility policy.

## Sprint 4 — release hardening

Completed:

- branch-aware coverage gate above 95 percent;
- abrupt-process-exit, deterministic randomized, and concurrency tests;
- typed package marker;
- wheel and source archive validation;
- isolated wheel installation and source-archive rebuild;
- security, architecture, and release documentation.

## Sprint 5 — GitHub release system

Completed in source:

- 18-combination operating-system and Python test matrix;
- stable aggregate checks for branch protection;
- strict formatting, linting, typing, coverage, packaging, and stress gates;
- weekly repeated, sustained, and reproducibility validation;
- dependency review and CodeQL for Python and Actions workflows;
- immutable full-SHA action policy;
- Dependabot maintenance;
- annotated-tag, main-ancestry, version, and changelog release gates;
- separated build, attestation, OIDC publication, and GitHub release jobs;
- SHA-256 release manifests, verification at every privileged boundary, and
  retained validated artifacts;
- Windows-compatible local command runner and setup instructions.

External activation remaining:

- configure the final GitHub repository URL;
- run the committed workflows on GitHub-hosted runners;
- enable the documented branch ruleset and security settings;
- configure the `pypi` environment and PyPI Trusted Publisher;
- obtain independent human review;
- record results from representative non-local filesystems.

## Deferred product backlog

Items remain deferred until validated user demand justifies their semantic and
maintenance cost:

- atomic no-overwrite creation;
- optional compare-and-swap based on file identity;
- platform-specific stronger synchronization primitives;
- opt-in metadata adapters maintained outside the core package.
