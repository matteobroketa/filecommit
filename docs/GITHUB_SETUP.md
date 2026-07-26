# GitHub setup for a solo Windows maintainer

The workflows are committed with the source. GitHub still requires a few
one-time repository and PyPI settings that cannot be encoded safely without the
final repository owner and URL.

## 1. Configure project URLs

From PowerShell in the repository root:

```powershell
py tools/configure_repository.py YOUR_GITHUB_NAME/filecommit
py tools/ci.py validate
```

The release gate refuses to publish while `OWNER/REPOSITORY` remains in
`pyproject.toml`.

## 2. Push the initial repository

Create an empty GitHub repository, then push this source. Do not create the
first release tag yet. Let `CI` and `Security` run once so their check names are
available to repository rules.

## 3. Restrict Actions

In **Settings → Actions → General**:

- keep workflow token permissions read-only by default;
- do not allow workflows to create or approve pull requests;
- allow only the actions used by this repository if the account plan exposes
  action allow-listing;
- retain artifact and log access for long enough to investigate failures.

The committed workflow policy independently rejects mutable action tags and
unexpected write permissions.

## 4. Protect `main`

Create a branch ruleset targeting `main` with these controls:

- changes require a pull request;
- at least one approval is required when another reviewer is available;
- stale approvals are dismissed after new commits;
- all review conversations must be resolved;
- the branch must be current before merge;
- required checks are `CI / Required` and `Security / Required`;
- force pushes and branch deletion are blocked;
- linear history is required;
- administrators should not bypass the rules during ordinary work.

For a one-person project, retain an emergency bypass role but use it only to
repair repository infrastructure. A bypassed commit must still pass both
workflows before any release tag is created.

Create a second ruleset for tags matching `v*`:

- restrict tag creation to the maintainer role;
- block tag updates and deletion;
- do not permit force updates;
- retain an emergency bypass only for an unpublished tag.

The release workflow independently requires an annotated tag whose commit is
contained in `main`. A protected tag cannot bypass the complete release matrix.

## 5. Enable repository security features

In **Settings → Security** or **Code security and analysis**, enable:

- dependency graph;
- Dependabot alerts;
- Dependabot security updates;
- code scanning;
- secret scanning when available;
- private vulnerability reporting.

The committed security workflow performs CodeQL and dependency-review checks;
the repository settings make their results visible and enforceable.

## 6. Create the protected PyPI environment

In **Settings → Environments**, create an environment named exactly `pypi`.

Configure it with:

- deployment branches and tags restricted to tags matching `v*`;
- a required reviewer when a second trusted maintainer exists;
- no stored PyPI password or API token.

The environment name is part of the Trusted Publisher identity and must match
`.github/workflows/release.yml` exactly.

## 7. Configure PyPI Trusted Publishing

In the PyPI project settings, add a GitHub Trusted Publisher with:

- owner: the GitHub account or organization;
- repository: `filecommit`;
- workflow: `release.yml`;
- environment: `pypi`.

For a first publication, use PyPI's pending-publisher flow if the project name
has not yet been created. Do not add a PyPI token to GitHub secrets.

## 8. First release

Before tagging:

```powershell
py -m pip install -e ".[dev]"
py tools/ci.py all
```

Update these three values in one pull request:

- `[project].version` in `pyproject.toml`;
- `__version__` in `src/filecommit/__init__.py`;
- the dated release section in `CHANGELOG.md`.

After the protected pull request is merged and both required checks pass:

```powershell
git switch main
git pull --ff-only
git tag -a v0.1.0 -m "filecommit 0.1.0"
git push origin v0.1.0
```

The tag workflow reruns every release gate, builds once, attests the artifacts,
publishes through OIDC, and creates the GitHub release. Never delete and reuse a
published version. Correct a defective release with a new version.

## 9. Routine maintenance

Dependabot opens grouped update pull requests. Review the upstream release and
its pinned commit before merging. A full-SHA change is expected; changing an
action back to `@vN` or `@main` is prohibited by repository policy.

Use **Actions → Extended validation → Run workflow** before a major release or
after changing filesystem semantics. Scheduled extended failures should block
release work until explained.
