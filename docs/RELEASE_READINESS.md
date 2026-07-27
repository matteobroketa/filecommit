# Release Readiness

## Recommendation

**Current recommendation: alpha/beta only.** No release version or move to
`1.0.0` is proposed here. A maintainer makes that decision only after every
unmet gate below has retained evidence.

## Supported guarantees

* Dependency-free Python 3.9–3.14 API for same-directory atomic replacement.
* Private staging, target-type/symlink checks, opt-in hard-link handling, and
  last-successful-replacement-wins semantics.
* `none`, `data`, and supported POSIX `full` durability behavior, including
  `DirectorySyncError.committed == True` after a post-commit directory-sync
  failure.

See the README, [architecture](ARCHITECTURE.md), [security review](SECURITY_REVIEW.md),
and [filesystem support evidence](FILESYSTEM_SUPPORT.md) for scope and limits.

## Evidence status

| Requirement | Status | Evidence |
| --- | --- | --- |
| Required Linux, macOS, and Windows checks | Pending hosted runs | CI workflow required job |
| Three consecutive green weekly extended runs | Pending | Extended workflow artifacts: model, stress, probe, and benchmark reports |
| Reviewed native probe reports | Pending human review | `filesystem-probe-*` artifacts and filesystem support table |
| Two documented realistic consumers | Pending maintainer documentation | Isolated configuration/manifest/binary consumer validator is technical evidence, not two reviewed user cases |
| No unresolved high-severity findings | Provisional | Security-review matrix; maintainer confirms issue tracker status before release |
| Completed independent review | Pending human review | Reviewer, commit, findings, and disposition fields in SECURITY_REVIEW.md |
| Package integrity and typed consumer evidence | Implemented locally; pending hosted run | Wheel consumer validator, snapshot, strict mypy fixtures, archive validator |

## Tested environments and retained evidence

The CI matrix targets CPython 3.9–3.14 on GitHub-hosted Linux, macOS, and
Windows. Linux is the authoritative 95% branch-coverage job; OS-specific
behavior is tested in the native matrix. Extended weekly artifacts retain
model-validation, multiprocess stress, filesystem-probe, and benchmark JSON.

## Limitations and deferred features

The target directory, operating system, filesystem, controller, and hardware
are trust boundaries. Network and userspace filesystems are evidence-driven,
not guaranteed. The package intentionally defers locking, compare-and-swap,
automatic orphan cleanup, metadata cloning, native extensions, and stronger
sync primitives.

## Release decision checklist

Before a stable recommendation, the maintainer records links to three
consecutive weekly workflow runs, reviews all native probe reports, documents
two realistic consumers, resolves high-severity findings, and obtains an
independent human review. The maintainer then records the selected version and
final alpha/beta/stable recommendation here.
