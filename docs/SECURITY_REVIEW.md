# Security Review

## Review record

| Field | Value |
| --- | --- |
| Reviewer | Pending independent human review |
| Commit | Uncommitted hardening worktree; maintainer records merge commit |
| Date | 2026-07-27 |
| Findings | No unresolved high-severity finding recorded by this package review |
| Disposition | Implementation evidence recorded; independent-review status is unclaimed |

## Threat, evidence, and residual-risk matrix

| Threat | Mitigation boundary | Evidence | Residual risk |
| --- | --- | --- | --- |
| Partial target content during a failed write | Same-directory private staging and `os.replace()` in the core transaction | Fault matrix, model validation, reader stress, crash-boundary tests | Filesystem replacement semantics remain a platform trust boundary. |
| Predictable or exposed staging data | `tempfile.mkstemp()` and restrictive staging permissions | Fault matrix and crash-orphan tests | An abrupt exit can leave a private orphan containing new data. |
| Symlink target confusion | Target inspection refuses symlinks before staging and before commit | Native platform tests and filesystem probe | Parent directories are not pinned; callers must trust them. |
| Device, directory, FIFO, or socket replacement | Target-type inspection accepts only regular files | Adversarial and native platform tests | Races inside an attacker-controlled directory are out of scope. |
| Surprising hard-link behavior | Multi-linked targets refused unless explicitly opted in | Fault matrix, native tests, filesystem probe | `allow_hardlinks=True` deliberately transfers that decision to callers. |
| Lost updates between writers | Explicit last-successful-replacement-wins contract | Thread and multiprocess stress tests; README and architecture | No locking, compare-and-swap, or transaction isolation is provided. |
| Windows sharing violations | Per-target registry and bounded retry classification | Retry and native open-handle tests; stress tool | Other processes may hold a target longer than the five-second retry budget. |
| Crash durability overclaim | `data` and POSIX `full` are explicit; post-commit failure is `DirectorySyncError(committed=True)` | Crash/durability tests and architecture diagram | OS, filesystem, controller, and hardware can weaken durability. |
| Unsafe automatic orphan deletion | No cleanup API; cleanup applies only to the operation's own staging path | Crash-orphan tests; SECURITY.md | Applications remain responsible for stale files in private directories. |
| Metadata privilege or access widening | Only portable permission bits are handled; ownership, ACLs, labels, and extended metadata are not cloned | Permission and platform tests; README | Applications that need metadata semantics must implement platform-specific policy. |
| Supply-chain or release compromise | Pinned Actions, dependency review, CodeQL, archive validation, isolated-wheel consumers, attest/release workflow | Workflow validator, package/consumer CI jobs | Repository administration and PyPI/OIDC configuration require maintainer review. |
| Undocumented filesystem assumptions | Probe operates under a dedicated child directory and retains JSON evidence | Extended workflow and filesystem support table | NFS, SMB, FUSE, loopback, and other mounts remain experimental observations. |

## Terminology

* **Atomic visibility** means a successful replacement does not expose a mixed
  staged payload at the target path.
* **Committed** means `os.replace()` has succeeded. It does not mean durable on
  stable storage.
* **Full durability** is the supported POSIX request to synchronize the parent
  directory after replacement; it is rejected before staging where unavailable.
* **Trusted directory** means a caller-controlled directory whose entries and
  ancestor path are not adversarially renamed or replaced.

These terms match the README, architecture document, SECURITY.md, and runtime
exception contract. This document is implementation evidence, not an
independent security assessment.
