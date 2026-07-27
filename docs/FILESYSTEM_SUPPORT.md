# Filesystem Probe Evidence

`tools/filesystem_probe.py DIRECTORY` records observations made below a new
temporary child directory of `DIRECTORY`. Its JSON is written to standard
output and its short human summary to standard error. The probe never inspects,
modifies, or deletes unrelated entries in the supplied directory.

The table is an evidence index, not a portability promise. A successful probe
describes one mounted filesystem, operating system, Python build, and runner at
one point in time. `filecommit` continues to treat the containing directory,
operating system, filesystem, and hardware as trust boundaries.

| Environment | Evidence | Status | Scope and limitation |
| --- | --- | --- | --- |
| GitHub-hosted Linux workspace | Weekly extended-workflow JSON artifact | Required | Native runner filesystem; the same workflow also probes `/dev/shm` when available. |
| GitHub-hosted macOS workspace | Weekly extended-workflow JSON artifact | Required | Native runner filesystem only. |
| GitHub-hosted Windows workspace | Weekly extended-workflow JSON artifact | Required | Native runner filesystem only; unsupported optional links and directory sync are reported rather than treated as contracts. |
| Linux tmpfs | Weekly extended-workflow JSON artifact when `/dev/shm` is writable | Optional | Absence or access denial is reported as unsupported. |
| NFS, SMB, FUSE, loopback, and other mounted filesystems | Manual invocation and retained JSON | Experimental | Non-blocking evidence; do not generalize an observation to another server, mount option, or implementation. |

The probe treats failed replacement, mixed reader visibility, unexpected target
changes after a pre-replacement crash, and unsafe-link acceptance as violated
`filecommit` contracts. Filesystem features that the platform does not expose,
such as symlink creation or directory synchronization, are recorded as
unsupported and do not make the probe fail.
