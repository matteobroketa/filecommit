## Change

Describe the behavior changed and why the package needs it.

## Failure semantics

State what remains true before commit, at commit, and after any reported failure.

## Validation

- [ ] Unit or adversarial tests cover the change.
- [ ] `python tools/ci.py validate` passes.
- [ ] `python tools/ci.py test` passes.
- [ ] Public API and compatibility impact are documented.
- [ ] Runtime dependencies remain empty.
- [ ] No requested guarantee is silently weakened.
