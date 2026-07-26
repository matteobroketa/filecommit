# Security policy and threat model

## What filecommit protects

`filecommit` prevents a target path from exposing a partially written staged
version when normal Python execution fails before `os.replace()` completes.
The existing target remains untouched while the staging file is written.

The package also:

- creates staging files with `tempfile.mkstemp()`;
- creates them in the target directory so replacement stays on one filesystem;
- starts staging files with private access;
- refuses symbolic-link targets;
- refuses non-regular targets;
- refuses multiply hard-linked targets unless the caller opts in;
- never evaluates or deserializes file contents;
- has no import-time or constructor-time filesystem writes.

## Trust boundary

The directory containing the target must be trusted. This package does not
protect against an attacker who can rename, replace, or control entries in that
directory or in its parent path. Parent-directory symbolic links are not
resolved or pinned.

The filesystem and operating system are also part of the trust boundary.
Network filesystems, userspace filesystems, synchronized folders, filter
drivers, virtual filesystems, and faulty storage can provide semantics weaker
than local filesystems.

## Sensitive data

A process terminated before replacement can leave a private
`.filecommit-*.tmp` staging file containing the new data. Applications writing
secrets must place targets in a private directory and should have an
application-specific orphan-cleanup policy. `filecommit` deliberately does not
provide a global cleanup function because deleting files selected only by a
name pattern would create its own security risk.

## Metadata

Atomic replacement creates a new filesystem object. Existing permission bits
are preserved by default, but special permission bits are removed. Ownership,
ACLs, extended attributes, timestamps, alternate data streams, labels, and
other platform metadata are not copied.

This is intentional. Copying ownership or broad metadata before replacement
can grant another principal access to the staging file, while copying it after
replacement introduces a committed-but-incomplete state and pathname races.
Applications requiring those semantics should implement and audit them for
their specific platform.

## Concurrency

Concurrent writers use last-successful-replacement-wins semantics. There is no
locking, version check, or compare-and-swap operation. Callers requiring lost
update protection must coordinate separately.

An already-open reader may continue reading the old filesystem object after a
replacement. A new open observes the replacement according to the filesystem's
coherency rules.

## Durability

Atomic visibility is not crash durability.

- `none` flushes Python buffers before replacement.
- `data` additionally calls `os.fsync()` on the staged file.
- `full` also calls `os.fsync()` on the parent directory after replacement.

`full` is rejected before staging on platforms where the standard library does
not expose directory synchronization. A `DirectorySyncError` means replacement
has already happened; its `committed` attribute is always `True`.

No mode can guarantee behavior beyond what the operating system, filesystem,
storage controller, and hardware actually provide.

## Reporting vulnerabilities

Use GitHub private vulnerability reporting for exploitable findings. Do not open
a public issue before maintainers have had a reasonable opportunity to assess
the report. Include the affected version, platform, filesystem, minimal
reproduction, expected result, observed result, and whether the target or
containing directory was attacker-controlled.

Release workflows use read-only permissions by default, full-SHA action pins,
separate OIDC and repository-write jobs, dependency review, and CodeQL. A
workflow change that broadens permissions or introduces a mutable action
reference is treated as security-sensitive.
