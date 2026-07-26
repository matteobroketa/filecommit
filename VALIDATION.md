# Validation record

Validation date: 2026-07-26

Local environment:

- Python 3.13.5
- Linux 6.12, x86-64, glibc 2.41
- Local POSIX filesystem supplied by the execution environment

Completed locally:

- 73 unit, adversarial, randomized, and repository-tool tests passed.
- The package source retained branch-aware coverage above the 95 percent gate.
- Source, tests, and repository tools parse under the Python 3.9 grammar policy.
- Workflow and issue-form YAML parsed successfully with two independent YAML
  parsers available in the build environment.
- All external GitHub Actions are pinned to full commit SHAs.
- Workflow policy enforces read-only defaults and confines OIDC and repository
  write permission to release jobs.
- A stress run completed 960 replacements from 12 concurrent writers while a
  reader verified SHA-256-framed payloads without observing partial content.
- Abrupt process termination before context exit left the old target unchanged
  and a private orphan staging file, matching the documented contract.
- A real local `durability="full"` operation completed successfully.
- Distribution inspection, isolated wheel installation, and source-archive
  rebuild are implemented as release gates.
- Release policy now requires an annotated tag on `main`, a single build, and
  checksum verification after every privileged artifact download.

Configured for GitHub-hosted validation:

- Python 3.9 through 3.14 on Linux, macOS, and Windows for every pull request and
  release tag;
- strict Ruff formatting and linting, strict mypy, and 95 percent branch
  coverage;
- package metadata and archive inspection, isolated installation, and rebuild
  equivalence;
- operating-system stress tests on every pull request;
- weekly repeated tests, sustained stress, and reproducibility checks;
- dependency review and CodeQL for Python and GitHub Actions;
- tag/version/changelog release gating, GitHub artifact attestations, PyPI
  Trusted Publishing, checksums, and GitHub release assets.

Not yet claimed:

- Local Ruff 0.16.0 and mypy 2.3.0 execution. The isolated build environment's
  package mirror did not expose those versions; the committed hosted workflows
  install and enforce the exact pins.
- Results from the newly added hosted Windows, macOS, and full Python-version
  matrix. Those become evidence only after the workflows run in the final
  GitHub repository.
- Network filesystems, userspace filesystems, synchronized folders, unusual
  mount options, storage power-loss behavior, or hardware write-cache behavior.
- Independent human code and security review.

The one-time repository, branch-ruleset, security-feature, environment, and PyPI
configuration is documented in `docs/GITHUB_SETUP.md`.
