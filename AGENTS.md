# CivicCast - Instructions for coding agents

Read `CLAUDE.md` for project architecture, commands, product constraints, and the
owner-approved completion workflow. Its 2026-10-19 workflow replaces the historical
generic audit ladder for both Codex and Claude; it does not waive product acceptance.

Start with `docs/handoff/CURRENT.md` for the portable project state, exact release
identity, remaining work, and build-asset inventory. Use your checkout's root;
the previous machine's absolute paths are not prerequisites.
v1.0.0-rc.7 is the current published release (2026-11-19); Beta20 and earlier
releases are history. This handoff does not authorize a new release,
installer rebuild, or reopening completed acceptance. Scott's latest assignment
defines what to do next. Do not modify frozen candidate worktrees.

Use one current candidate and work serially. Do not create subagents automatically.
Complete authorized work through the requested outcome, preserve valid evidence,
and stop when delivered. A remaining blocker means incomplete. DCO commits, required
CI, protected-branch rules, recovery, and installed acceptance remain required.

No Git LFS transfers or spending through October 2026; no LFS pull/fetch/push.
No automatic tests, audits, rebuilds, subagents, or continuation jobs on startup.
Reuse existing valid evidence; mutation testing is not a requirement.
Keep Windows command/helper windows hidden. Use invisible ConPTY where available
(`tty=true`, `login=false`); background children need Hidden/CreateNoWindow.
No host reboot. Preserve data and recovery safeguards. Test hardware through the
repository's documented API simulators/mocks; do not seek physical Roku devices.
Never pause an active goal without a current owner request. A newer explicit resume
or goal-start action supersedes an older pause; never mark incomplete work complete.

## Explicit auditor assignments

Only act as an auditor when explicitly assigned that role. Audit the supplied SHA
and named claims, in an isolated worktree when execution requires it; do not edit
implementation. Run the relevant named proofs and report material findings, results,
skips, and limitations. Reading a diff is not runtime proof. Do not expand a scoped
review into a fresh project-wide investigation. Use private handling for security
findings. A review result does not authorize release or production cutover.

Scott's current assignment determines authority. Historical model-role pairings,
per-slice audit ceremonies, five-lens reports, and old exact-filename approval gates
are not automatic requirements. Preserve existing task-specific authorization;
do not resurrect an old beta assignment as permission to resume stopped work.
