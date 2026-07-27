# Release procedure

Releases are generated only from protected tags. Local machines do not build or
upload production distributions.

## Prepare

1. Merge all intended changes through `main`.
2. Update the project version, package `__version__`, and dated changelog entry.
3. Run `python tools/ci.py all` where development tools are installed.
4. Merge the release-preparation pull request after `CI / CI required` and
   `Security / Security required` pass.
5. Confirm the latest scheduled extended validation has no unexplained failure.

## Publish

Create and push an annotated tag matching the version:

```console
git tag -a v1.2.3 -m "atomicreplace 1.2.3"
git push origin v1.2.3
```

The release workflow is the sole publisher. Do not build locally and upload
files manually. Do not store a PyPI API token in the repository.

## Failure handling

- Before PyPI publication: fix the cause, delete the unpublished tag, merge the
  correction, and create the tag on the corrected commit.
- After PyPI publication: never replace the version. Preserve the release and
  issue a new patch version.
- If PyPI succeeds but GitHub release creation fails, use the retained
  `release-candidate-vX.Y.Z` artifact and checksums from that workflow run. Do
  not rebuild the files locally.
- Treat an unexplained checksum, attestation, dependency-review, or CodeQL
  failure as a release stop.
