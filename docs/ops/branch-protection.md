# Branch Protection on `main`

GitHub branch protection for `scottconverse/civiccast-native`.

## Why this exists

Day 0 prompt called for branch protection on `main`. A single-developer +
autonomous-agent workflow makes "require 1 reviewer" inapplicable, but the
status-check requirements are still load-bearing:

- **No direct pushes** — every change goes through a PR. The agentic
  pipeline already enforces this on the executor side; protection makes
  it impossible to bypass at the git layer.
- **Required CI status checks** - `main` cannot advance until the release-gate
  checks are green: unit tests, lint/type check, public and operator
  accessibility, docs render, cleanroom full install gate, and operator portal
  lint/build.
- **No force pushes; no branch deletion** — protects against catastrophic
  accidental rewrites.

## When this can be applied

GitHub gates branch protection and repository rulesets behind **either**
GitHub Pro **or** a public repo. CivicCast remains at
`scottconverse/civiccast-wsl-legacy`; do not assume an org transfer as the hardening path.
Attempting to read or apply protection on the current private repo returns:

```
403 Upgrade to GitHub Pro or make this repository public to enable this feature.
```

Two paths to unblock:

- **Path A:** Upgrade the current `scottconverse/civiccast-wsl-legacy` private repo to a
  GitHub plan that supports branch protection/rulesets.
- **Path B:** Make the current repo public when the project is ready for that
  exposure, then apply protection in place.

Confirmed 2026-05-15: branch-protection and ruleset API reads both returned
the quoted plan-gate error.

## How to apply (as applied 2026-06-16)

Run as Scott (repo admin). Requires `gh` CLI authenticated against the account
with admin on the repo. The branch-protection API wants a nested JSON body, so
pass it via `--input` (the older `-F 'a.b[]=...'` dotted/bracket form does NOT
reliably build the nested object). Payload as applied:

```json
{
  "required_status_checks": {
    "strict": true,
    "contexts": [
      "Unit tests (3.12)",
      "Lint and type check",
      "Accessibility (axe-core) — public portal",
      "Accessibility (axe-core) — operator console",
      "Operator portal (lint + build)"
    ]
  },
  "enforce_admins": false,
  "required_pull_request_reviews": {"required_approving_review_count": 0},
  "restrictions": null,
  "required_linear_history": false,
  "allow_force_pushes": false,
  "allow_deletions": false
}
```

```bash
gh api -X PUT repos/scottconverse/civiccast-native/branches/main/protection --input payload.json
```

Notes:

- `enforce_admins=false` keeps the human director's emergency-override
  posture: when a CI gate is genuinely broken, the admin can land the fix
  directly without the protection blocking them.
- `required_pull_request_reviews.required_approving_review_count=0` requires a
  PR before merging but no approving reviewers - matches the solo-developer +
  agent reality. When the Steering Committee phase begins (post-Phase 0),
  tighten this.
- **Only the five CI checks that run on EVERY PR to `main` are required.** A
  GitHub required status check that never reports (because its workflow is
  skipped by a `paths:` filter) leaves the PR blocked in a permanent
  "Expected — waiting" state. The pre-staged list of seven (2026-05) included
  two path-filtered workflows that would deadlock code-only PRs:
  - `Pandoc PDF/DOCX render` (`ci-docs.yml`, `paths: docs/** …`) — runs + still
    blocks on doc-touching PRs, but is NOT a hard required-context.
  - `Cleanroom (Docker, full install gate)` (`ci-cleanroom-e2e.yml`) — GONE.
    It was the Docker/Linux full-install gate and is not in this repository.
    Nothing replaced it: the native line currently has no automated
    full-install gate, so there is no heavy install context to require or
    exempt here.
  To make either a hard required-context safely, de-path-filter the workflow
  (so it always reports) first — accepting the per-PR cost — then add its name
  to `contexts`. If a required workflow is renamed, update `contexts`.

## How to verify

```bash
gh api repos/scottconverse/civiccast-native/branches/main/protection | jq
```

The response should show every status check listed above as required.

## When to revisit

- When a new CI workflow is added: add its job name to the contexts list.
- When the repo becomes public or the account plan supports protection:
  apply the command above against `scottconverse/civiccast` (this repository; renamed from
  `scottconverse/civiccast-native` on 2026-11-08).
- When the Steering Committee phase begins: tighten the
  `required_pull_request_reviews` field.

## History

- 2026-05-09: protection not yet applied. Day 0 finding tracked in
  `next-cleanup.md`. Doc shipped as part of cleanup batch A; Scott to
  apply when convenient.
- 2026-05-15: Scott decided to keep the repo at `scottconverse/civiccast-wsl-legacy`.
  Protection remains approved but blocked by GitHub plan/private-repo limits.
- 2026-06-16: **APPLIED.** The repo is now public (the plan gate lifted), and
  Scott gave the go. Verify-then-apply found that two of the seven pre-staged
  contexts (`Pandoc PDF/DOCX render`, `Cleanroom (Docker, full install gate)`)
  are path-filtered and would deadlock code-only PRs, so the required set is the
  five always-on checks above. `strict=true`, PR required (0 reviewers),
  `enforce_admins=false`, no force-push, no deletion. Verified via read-back.
- 2026-07-03/04: `enforce_admins=false` did its documented job (emergency
  override for a broken gate) but also had a side effect worth naming plainly.
  A stage-rework wave (Jul 3) merged direct-to-`main` via the admin PAT,
  including a change (`4934af33`) that added the first `<Link>` import to
  `ControlRoomReadinessPanel.tsx`. Because that push was admin-authored, the
  five required checks never ran on it, and a real regression in the operator
  console's test harness (not production — `main.tsx` always wraps the app in
  `<HashRouter>`, so real users never hit it) landed undetected. It sat for two
  days until the next actual PR (#184, unrelated rc.3 evidence docs) surfaced
  it: `Operator portal (lint + build)` failed, meaning **branch protection had
  been unsatisfiable by any PR since 2026-07-03** until PR #185 fixed the test
  harness (wrapped both affected test files' render calls in `MemoryRouter`).
  No policy change — `enforce_admins=false` remains correct for genuine
  emergencies. The lesson is operational, not structural: prefer a PR (even a
  fast admin-approved one) over a direct push for anything touching
  application code, specifically so the required checks get a chance to catch
  exactly this class of regression before it accumulates. Releases (tag
  pushes) are a different, already-reviewed path and are unaffected by this.
