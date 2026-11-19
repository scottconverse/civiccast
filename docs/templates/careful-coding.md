# Per-Commit Careful-Coding Checklist

> **Owner instruction (current, October 11, 2026):** Follow only the sequence
> Scott authorizes for the task. This legacy checklist does not independently
> require a test, push, or another fix/rebuild cycle, and it does not authorize
> autonomous continuation. Perform only the proportional checks explicitly
> requested for the current work.

> **When moving this file into the project repo, place it at `docs/templates/careful-coding.md`.** This is the per-change layer of the verification described in the "Verification that actually gates this repo" section of `CONTRIBUTING.md` (see also `CLAUDE.md`'s "Verification and release" section). Time budget: **5–10 minutes per non-trivial commit**. Skipping steps because the change "feels small" is the failure mode this checklist exists to prevent.

---

## When to use this checklist

Every non-trivial commit. "Trivial" means: typo fix, dependency lockfile bump, doc edit. Anything else — feature work, refactors, tests, schema changes, new modules, config changes — runs the loop.

If you're unsure whether a commit is trivial, run the loop. The cost is 5 minutes; the cost of skipping is a class of bug that ships.

---

## The 9 steps

### Before the edit (steps 1–5)

**1. Read the callers.** Grep or find references for the function, class, module, or table you're about to change. Open at least the top 3 callers and skim what they expect. If you're adding a new symbol, this step is "where will this be called from, and what will those call sites need."

**2. Trace the runtime context.** Where does this code actually run? Foreground request? Background worker? Bus event handler? Live broadcast loop? AI inference? Knowing the runtime context tells you the latency budget, the failure-mode posture, and which threads of state are visible.

**3. Fan-out grep.** Search for related strings: the symbol you're about to change, the column name, the event name, the module name, the URL pattern. The goal is to find every place in the codebase that will be affected — not just the obvious one. If the grep returns more hits than you expected, slow down.

**4. Identify the data contract.** What does this code promise to its callers, and what does it expect from its dependencies? If the change modifies a public API, an event payload, a database column, or an HTTP response shape, name what's promised and what callers/consumers will see. Breaking a contract without flagging it is the most common cause of cross-module regressions.

**5. State the blast radius.** In one or two sentences, what is the worst that can break if this change is wrong? Which other modules, pages, or pipelines could it affect? Write the answer down (in the commit message draft, in a comment, or in the PR description). If you can't articulate the blast radius before the edit, you don't understand the change well enough to make it.

### After the edit (steps 6–8)

**6. Re-read end-to-end.** Open the file you changed and read from top to bottom. Not just the diff. Make sure imports are sane, the new code fits the surrounding style, no commented-out scratch is left, no `print()` / `console.log()` / `// TODO: hack` debris.

**7. Narrate one full code path.** Pick the primary code path through the change and narrate it to yourself in plain language: "When the operator clicks Approve, the request hits `vod.publish()`, which calls `archive.upload_to_ia()` and `syndicate.fan_out()`, which emit events on the bus, which the dashboard subscribes to and renders…" If the narration breaks down at any step, the code doesn't work yet — keep editing.

**8. Prove the render/data path for new state.** For frontend changes: open the affected page in the dev environment and confirm the new state actually renders with real data. Loading state, success-with-data, success-empty, error, partial — name which states this change touches and verify them visually. For backend changes: hit the affected endpoint with a real request (curl, httpie, the dev UI) and confirm the data path returns what you claimed. Reading the source is not proof.

### Before the commit (step 9)

**9. 5-lens self-audit.** Quick mental sweep, ~30 seconds per lens:

- **Engineering.** Is the pattern right? Edge cases handled? Errors named? Imports clean?
- **UX.** Are user-visible strings clear? States all designed? Mobile and desktop both work?
- **QA.** Does the test suite cover what changed? What does it NOT cover?
- **Tests.** Did I add or update at least one test for the new behavior?
- **Docs.** Did inline comments stay accurate? CHANGELOG entry needed? Public API change documented?

### Bonus check — when the change adds a dependency

If this commit adds, removes, pins, or upgrades any dependency in
`pyproject.toml` or any `package.json`:

- Run the suite **against a fresh install** before claiming it passes —
  `uv sync --all-extras --group dev && uv run pytest` (Python) or
  `rm -rf node_modules && npm ci && npm run build` (frontend). A stale local
  venv can mask a missing transitive dep that CI's fresh venv will trip on.
- Regenerate the relevant lockfile (`uv lock` for Python, `npm install` for
  Node) and commit the lockfile change in the same commit. CI runs against
  the lockfile; a forgotten regen turns into a CI failure on next push.
- Observed twice in Sprint 0.3 task 1b: a stale `psycopg2-binary` masked a
  missing `psycopg` v3 default; an un-regenerated `uv.lock` would have
  caused the next CI run to fail.

If any lens fails, fix it before the commit. If all pass, commit with a Conventional Commit message and DCO sign-off.

---

## Time budget enforcement

5–10 minutes per commit. If you're consistently exceeding it:

- The commits are too big. Break them down.
- The blast radius is genuinely bigger than you thought. Surface to the human director and consider whether this is still in scope for the PR you're building toward.
- You're auditing instead of careful-coding. Stop. The hostile 5-lens self-audit before push (`docs/process/5-lens-self-audit.md`) and cross-agent PR review handle cross-file consistency. This checklist is just "did I break what I touched."

If you're consistently under-running it (≤2 min per commit), you're not actually doing the steps. Do them.

---

## What this checklist does NOT do

- **Cross-file consistency.** That's the QA lens of the hostile 5-lens self-audit, run before every push (`docs/process/5-lens-self-audit.md`).
- **Doc-currency drift.** That's the Docs lens of the same 5-lens self-audit, plus cross-agent PR review.
- **Release-candidate readiness.** Independent engineering review and actual Windows installation/runtime evidence are release checks, not per-commit obligations. The current automated clean-install, cross-version upgrade and download-only lanes are documented in `docs/ops/release-candidates.md`; a separate tester soak proves sustained operation. The old Docker/Linux cleanroom lane is retired.
- **Release notes / migration guides.** That's PR-description and push-report time, not commit time.

Stay at the per-commit level. Trying to do push-time or release-candidacy work at every commit is exactly the runaway cycle this checklist exists to prevent.

---

*This checklist is the per-change layer of the verification CLAUDE.md describes. When in doubt about whether a step applies, run it anyway.*
