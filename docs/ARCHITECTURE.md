# Architecture

## Core transaction

A successful operation follows one narrow transaction:

1. Resolve the caller's path to an absolute lexical path.
2. Validate that an existing target is a regular, non-symlink file and, by
   default, has only one hard link.
3. Create a secure random staging file in the same directory.
4. Give the caller a standard Python text or binary stream.
5. On an exception, close and remove the staging file.
6. On success, flush Python buffers.
7. Revalidate the target and determine final permission bits.
8. Apply permission bits to the staging file.
9. Optionally synchronize the staging file.
10. Close the stream and call `os.replace(staging, target)`.
11. For full durability, synchronize the parent directory.

The only irreversible step is step 10. Failures before it leave the existing
target unchanged. A failure in step 11 raises `DirectorySyncError`, explicitly
reporting that the replacement is already committed.

## Transaction states

```text
entry
  |
  v
validate target -> create private staging -> caller writes -> flush/sync/permissions
  |                    |                       |                 |
  | failure            | failure               | body/failure    | failure
  +--------------------+-----------------------+-----------------+
                                      |
                                      v
                              close and remove staging
                                      |
                                      v
                         target remains old (or absent)

flush/sync/permissions -> close staging -> os.replace -> sync parent directory
                                                   |                 |
                                                   |                 | failure
                                                   v                 v
                                           target is new       target is new;
                                                           DirectorySyncError
```

The fault matrix exercises every displayed pre-replacement failure boundary.
Cleanup failures are reported as warnings without replacing an active body or
commit exception. Parent-directory synchronization is the only tested
post-replacement failure and therefore always reports committed state.

## Why same-directory staging

Replacement must not cross filesystem boundaries. Creating the staging file in
the target's directory also avoids relying on environment-controlled global
temporary directories.

## Why only write modes

Append, read/write update, and exclusive creation have different concurrency
contracts. Implementing them by reading the old file and writing a replacement
would create lost-update behavior and potentially unbounded memory or disk use.
The package therefore accepts only `w`, `wt`, and `wb`.

## Windows same-process serialization

Windows uses a private, reference-counted per-target `RLock` from initial
inspection through context exit. It serializes same-process writes to the same
lexical, case-normalized target to reduce sharing-violation replacement
failures. The lock is reentrant: a nested same-thread operation may commit, and
the later successful outer replacement wins. It is not a public locking API,
does not coordinate other processes, and does not prevent logical lost updates.

## Why symlinks are refused

`os.replace()` replaces a symlink directory entry rather than writing through
it. That behavior is safe at the system-call level but often contradicts user
intent. Refusal forces the caller to identify the intended regular-file path.

## Why hard links are refused by default

Replacing one hard-linked path creates a new filesystem object for that path;
other links continue referencing the old object. The operation is valid but
surprising, so it requires `allow_hardlinks=True`.

## Why metadata copying is limited

Portable Python can preserve basic mode bits. Ownership, ACLs, labels, extended
attributes, alternate streams, and platform flags have platform-specific
security and race implications. The package avoids pretending there is a safe,
portable policy.

## Why full durability is POSIX-only

Python exposes `os.fsync()` for ordinary files on POSIX and Windows, but its
standard library does not expose a portable Windows directory-handle flush.
Silently degrading `full` would make the API dishonest, so the request is
rejected before staging.

## Non-goals

- File locking
- Compare-and-swap updates
- Atomic no-overwrite creation
- Directory-tree transactions
- Metadata cloning
- Serialization
- Encryption
- Remote or network storage abstractions
- Automatic stale-file deletion
