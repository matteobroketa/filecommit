# atomicreplace

`atomicreplace` safely replaces files without exposing readers to partial content. It uses private same-directory staging, explicit durability controls, permission preservation, and strict target validation.

Unlike basic atomic-write wrappers, atomicreplace refuses unsafe target types, handles permissions explicitly, distinguishes committed post-replacement errors, and validates behavior across Linux, macOS, and Windows.

```python
from atomicreplace import replace_text

rendered_settings = "enabled = true\n"
replace_text("settings.toml", rendered_settings, durability="data")
```

Or write incrementally:

```python
from atomicreplace import atomic_open

rendered_manifest = '{"files": []}\n'
with atomic_open("manifest.json", "w", permissions=0o600) as file:
    file.write(rendered_manifest)
```

The target remains unchanged until the context exits successfully. A staging
file is created beside the target and committed with `os.replace()`.

## Install

```console
python -m pip install atomicreplace
```

Python 3.9 or newer is supported. Runtime dependencies: none.

## API

```python
replace_bytes(path, data, *, permissions=None,
              preserve_permissions=True, durability="none",
              allow_hardlinks=False)

replace_text(path, text, *, encoding="utf-8", errors="strict", newline=None,
             permissions=None, preserve_permissions=True,
             durability="none", allow_hardlinks=False)

atomic_open(path, mode="w", *, buffering=-1, encoding=None, errors=None,
            newline=None, permissions=None, preserve_permissions=True,
            durability="none", allow_hardlinks=False)
```

Only `w`, `wt`, and `wb` modes are accepted. Append, update, exclusive-create,
and read modes would require different concurrency semantics and are rejected.

## Durability

- `none`: flush Python buffers and atomically replace the directory entry.
- `data`: additionally call `os.fsync()` on the staged file before replacement.
- `full`: additionally synchronize the parent directory after replacement.

`full` is available on POSIX systems. It is rejected before staging on
platforms where the standard library cannot synchronize a directory. A
`DirectorySyncError` means the new target is already committed, but the full
crash-durability request was not completed.

Atomic visibility and crash durability are different properties. Filesystems,
network mounts, virtual filesystems, storage controllers, and hardware may
provide weaker behavior than the host operating-system API suggests.

## Safety behavior

- The target path is mandatory and is bound to an absolute path when the
  operation is created.
- The staging file is created in the target directory with private permissions.
- Existing regular-file permissions are preserved by default; new files default
  to `0o600`.
- Symbolic-link targets are refused. Parent-directory symlinks are not resolved;
  callers must trust the directory path they supply.
- Multi-linked regular files are refused by default because replacement changes
  only one directory entry. Use `allow_hardlinks=True` only when that is intended.
- Directories, devices, FIFOs, sockets, and other non-regular targets are refused.
- Special permission bits, ownership, ACLs, extended attributes, timestamps,
  alternate data streams, and platform-specific metadata are not copied.
- Concurrent writers are last-successful-replacement-wins. On Windows,
  same-process writes to the same lexical target are internally serialized for
  the lifetime of each context to reduce replacement sharing failures; nested
  use by the same thread is supported and the outer successful replacement wins.
  This implementation safety mechanism neither coordinates other processes nor
  provides locking or compare-and-swap semantics for logical updates.
- A process terminated before replacement can leave a `.atomicreplace-*.tmp`
  staging file in the target directory. Existing target data remains untouched.

The containing directory must be trusted. No path-based API can defend a file
inside a directory whose entries an attacker may freely rename or replace.

## Development and release trust

The runtime package has no dependencies. Development tools are exact, optional
dependencies:

```console
python -m pip install -e ".[dev]"
python tools/ci.py all
```

Every pull request is tested on Python 3.9 through 3.14 on Linux, macOS, and
Windows. GitHub Actions are pinned to immutable commit SHAs. Releases are rebuilt
and retested from the exact tag, checked for metadata and archive integrity,
installed in isolation, attested, and published to PyPI with OIDC Trusted
Publishing rather than a stored API token.

See `docs/CI_ARCHITECTURE.md`, `docs/GITHUB_SETUP.md`, and
`docs/RELEASING.md`.
