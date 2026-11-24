# Sprint 2 — unattended station operation

> Historical implementation record. The checkpoints below describe work at that time.
> These features subsequently shipped in Beta 16 and remain in the current
> release; use the [RC8 verification record](../releases/v1.0.0-rc.8-verification.md)
> for that package identity and its results. Earlier unreleased/pending statements
> are historical, not current outstanding tasks.

Status: implementation and focused checks complete; CI and merge pending; unreleased.
Branch: `codex/sprint2-unattended`.
Starting source: `2c77a448d2cab6d98302dc456f947593492a5d94` (Sprint 1, PR #245).

This finite sprint contains the owner's three selected goals:

1. Use the station time zone consistently for scheduling entry, recurring
   programmes, previews and display, with explicit handling of daylight-saving
   clock gaps and repeated times.
2. Bound operational worker logs and disposable egress command history, expose
   their storage use and handle low disk space gracefully. Never automatically
   delete recordings or reinstate live-caption evidence retention.
3. Recover automatically after repeated recoverable output failures through a
   deliberate fallback, cooldown and retry path. Preserve restart-storm
   protection and intentional operator stops.

Completion requires integrated source changes, affected operator documentation,
focused checks, push and green-path merge. Sprint 1's existing GitHub checks are
monitored at work checkpoints. No new installer, publication, production cutover,
soak, generalized testing framework or outside hardware qualification is included.

## Verification and disposition

- Sprint 1 PR #245 merged on October 13 at
  `bcb76fd0e350294dcfe87ef58b9ee3f76885adce`: 24 completed checks passed,
  three packaging checks were intentionally skipped, and the informational
  mutation report was still running at merge. No tag or release was created.
- Storage: 99 egress store/router checks, two real-child log checks, two shared
  drainer checks, 28 storage/health UI checks and five migration-head checks
  passed. The broader schema-check selection printed six passes but exited
  unsuccessfully on an unclosed SQLite connection warning at teardown; this
  warning is not counted as a clean pass. The focused current-head checks passed.
- Recovery: 342 checks across daemon, automation, freeze escalation and A/V
  guard files passed. Independent storage-worker review found no product defects
  in the bounded recovery and intentional-stop paths.
- Scheduling: initial affected checks passed (107 backend, 116 frontend).
  Review corrections for the UTC planning-date boundary, preserved future
  horizons, projected calendar dates and overlapping API/background compiles
  passed 115 focused backend checks. The final drawer correction passed nine
  component checks. The independent bounded re-review found both corrections
  resolved.
- Independent storage review found a role-gating mismatch in the new health
  panel. Its query and panel now use the endpoint's permitted staff roles;
  the 28-test UI check passed after correction.
- Consolidation: TypeScript and frontend ESLint passed. Python typing passed
  for 19 source files; changed Python lint and formatting passed. Generated
  OpenAPI artifacts matched their source. Bundled Help, PDF and Word renderers
  confirmed their outputs match the assembled manual.

Detailed focused receipts are under
`.agent-runs/native-windows/sprint2-unattended/evidence/`.
Push, Sprint 2 CI and merge remain pending.

## Scope and limitations

- PR review corrections: scheduling roles read only the timezone through a
  dedicated endpoint; full-profile permissions remain unchanged. Recurring
  programme slots persist a local clock/calendar anchor (migration 0091), and
  the guide displays it separately from the first airing instant. Legacy slots
  adopt their current station-local interpretation before a timezone edit or
  on first materialization; earlier timezone history cannot be reconstructed.
- Correction checks: 47 focused programlog/schema tests passed, followed by
  the API anchor-exposure check; two timezone permission/profile-hook tests
  and one guide-display browser regression passed. TypeScript, portal build,
  targeted lint and generated OpenAPI checks passed. The old recovery log
  assertion was aligned with cooldown behavior; its exact process/restart
  bounds remain unchanged and its single focused test passed.

- Existing generated timestamps remain fixed when the station timezone or
  recurrence changes. Extension begins after the greatest preserved logical
  calendar day or existing start's calendar day in the current zone. Cancelled
  occurrences remain accounted for rather than being silently recreated.
- Programlog and autoschedule locks serialize API/background work within one
  application process, including timezone resolution and commits. They do not
  provide cross-process coordination for multiple application instances.
- The migration check used SQLite. A separate PostgreSQL integration run for
  the new storage-size query and index was not performed locally. No new
  installer or soak result is claimed.

## Behavior decisions

- A recurring programme keeps its station-local wall-clock time across daylight
  saving changes. A nonexistent local time is skipped for a recurrence and
  rejected with an explanation for a one-time booking. A repeated local time
  runs once, at its earlier occurrence. Form help must state this policy.
- Worker output must remain drained even when its diagnostic file cannot be
  written. Cleanup is confined to disposable logs and consumed command history;
  pending commands and original media are preserved.
- Exhausted freeze/synchronization restart budgets use the existing fallback
  slate path, wait for the restart window to clear, then retry. An intentional
  stop or drain cancels pending recovery and suppresses automatic starts for
  that service session. An explicit Start clears that suppression; after a
  service restart the configured auto-start behavior applies again.
