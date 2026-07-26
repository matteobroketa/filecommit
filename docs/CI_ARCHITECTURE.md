# Continuous integration and release architecture

The repository treats GitHub Actions as an untrusted automation boundary with
explicit permissions and immutable inputs. Every external action is pinned to a
full commit SHA. The repository policy test rejects mutable action tags,
`pull_request_target`, inherited secrets, broad workflow permissions, and OIDC
access outside the release workflow.

## Pull-request path

Every push and pull request runs `.github/workflows/ci.yml`:

1. Repository policy validates version consistency, Python 3.9 syntax, UTF-8/LF
   text hygiene, generated-file cleanliness, pinned tooling, zero runtime
   dependencies, and workflow security policy.
2. Static quality checks formatting, lint, and strict typing.
3. The functional suite runs on Python 3.9 through 3.14 on Linux, macOS, and
   Windows.
4. Branch-aware coverage must remain at or above 95 percent.
5. Package integrity builds the wheel and source archive, inspects their
   contents and metadata, installs the wheel without an index or dependencies,
   and rebuilds an equivalent wheel from the source archive.
6. A concurrent reader/writer stress test runs on all three operating systems.
7. One stable `Required` job aggregates every result for branch protection.

The test matrix installs the package before running tests. It therefore tests
what packaging exposes rather than importing `src/` through an environment
variable.

## Security path

`.github/workflows/security.yml` runs workflow-policy enforcement, dependency
review for pull requests, and CodeQL analysis for both Python and GitHub Actions.
The workflow has a separate stable `Required` aggregate check.

`.github/dependabot.yml` proposes reviewed updates for development tools and
pinned GitHub Actions. Dependency updates never publish automatically.

## Extended path

`.github/workflows/extended.yml` runs weekly and on demand. It repeats the full
test suite in fresh processes, performs sustained stress testing on the oldest
and newest supported Python versions across all operating systems, and checks
that independent builds contain equivalent files.

These tests are intentionally outside the pull-request critical path. They
increase confidence without making ordinary review dependent on long stress
runs.

## Release path

`.github/workflows/release.yml` is triggered only by a pushed `v*` tag.
Publishing is divided into jobs with different trust levels:

1. The release gate verifies that the tag, `pyproject.toml`, `__version__`, and
   dated changelog entry agree and that repository URL placeholders are gone.
2. Static quality, coverage, and the full 18-job operating-system/Python matrix
   run again against the exact tagged commit.
3. One build job creates the wheel and source archive once, validates them,
   installs the wheel in isolation, rebuilds from the source archive, and
   creates SHA-256 checksums.
4. A separate OIDC job downloads the release candidate, verifies its
   SHA-256 manifest, and creates GitHub artifact attestations for those exact
   files.
5. A minimal PyPI environment job independently downloads and verifies the
   same candidate, then uses
   Trusted Publishing. It has no checkout step and no long-lived API token.
6. A final job independently verifies the downloaded candidate and creates
   the GitHub release from those files.

Build, attestation, PyPI publication, and GitHub release publication are not
combined. A compromise or implementation error in one job therefore does not
inherit every release permission.

## Local entry point

`tools/ci.py` provides the same main tasks on Windows, macOS, and Linux:

```console
python tools/ci.py validate
python tools/ci.py test
python tools/ci.py quality
python tools/ci.py package
python tools/ci.py stress
```

The package remains dependency-free at runtime. Build, lint, type-check, and
coverage tools are exact development-only dependencies.
