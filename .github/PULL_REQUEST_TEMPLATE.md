<!-- SPDX-License-Identifier: CC-BY-4.0 -->

## Summary

<!-- One or two sentences. What does this PR change and why. -->

## Line and target

<!-- BRANCHES.md is the source of truth: this repository ships one product
line (native Windows) and `main` carries it. There is no second line or
base branch to choose. -->

- **Base branch:** `main`
- **Spec section(s) touched:** <!-- e.g., §8.2 civiccast-stream, or "none" -->
- **ADR(s) created or updated:** <!-- e.g., ADR 0021, or "none" -->
- **Closed architectural decisions touched:** <!-- "none" or list with justification per CLAUDE.md -->

## Type of change

- [ ] `feat` — new capability
- [ ] `fix` — bug fix
- [ ] `docs` — documentation only
- [ ] `refactor` — internal change, no behavior change
- [ ] `test` — tests only
- [ ] `chore` — build / tooling / dependencies
- [ ] `perf` — performance improvement
- [ ] Breaking change (`!` in commit type, `BREAKING CHANGE:` footer)

## 5-lens self-audit

<!--
Optional guidance, not a push gate (CLAUDE.md, "Verification and
release"); full checklist at docs/process/5-lens-self-audit.md. When you
use it, paste the fixed-format report — each lens is hostile: the diff
is wrong until grep/tests/runtime evidence proves otherwise.
-->

```
5-lens self-audit:
- Engineering: [pass | findings: ...]
- UX:          [pass | findings: ...]
- Tests:       [pass | findings: ...]
- Docs:        [pass | findings: ...]
- QA:          [pass | findings: ...]
Artifact-state: [pass | findings: ...]
```

## Verification evidence

<!--
What was actually run, not what should work. Commands, output, UI states
walked, console state. Point at durable evidence, not descriptions:

- Native-Windows Program slices: commit-bound evidence under
  `.agent-runs/native-windows/<slice-id>/evidence/`.
- Claims-evidence-governed docs (see docs/claims/claims.yaml): the
  registered evidence ids the changed claims bind to.
- Release-candidate-facing changes: the clean-box / sandbox e2e run
  (workflow run id or evidence directory).
- Everything else: the test commands run and their results, plus a
  runtime walkthrough of any user-visible change.
-->

## Pre-merge checklist

- [ ] `ruff check .` and `ruff format --check .` pass
- [ ] `mypy` passes for changed modules
- [ ] `pytest` passes (scoped to the changed area at minimum; full suite before claiming release-candidate readiness)
- [ ] `pre-commit run --all-files` passes
- [ ] Every commit is signed off (DCO — `Signed-off-by:` trailer)
- [ ] Every commit follows Conventional Commits
- [ ] CHANGELOG entry added (under `[Unreleased]`)
- [ ] Module README and module CHANGELOG updated where applicable
- [ ] No secrets, credentials, or PII in the diff
- [ ] No closed architectural decisions reopened without an RFC
- [ ] Native-line PRs: review conversations resolved; do not assume main branch protection was verified

## Related issues / PRs

<!-- Closes #123, refs #456 -->
