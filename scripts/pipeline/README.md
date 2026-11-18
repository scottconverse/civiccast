# CivicCast agentic pipeline runner

> **Optional historical tooling.** This describes the existing Claude pipeline mechanism, not the current authorized work sequence. Scott's standing no-autonomous-fix/test/rebuild-loop instruction takes precedence. Do not invoke or repeat a pipeline without his authorization.

Two Claude Code custom commands orchestrate the agentic pipelines defined in [`.pipelines/`](../../.pipelines/). Both run inside a Claude Code session — there is no external service, no daemon, and no API key to manage. The orchestrator IS Claude, using the `Agent` tool to spawn isolated subagents for agent stages, `Bash` for the policy stage, and `AskUserQuestion` for human-approval gates.

## Commands

### `/new-run <pipeline> <slug>` — initialize a run

Initializes a new run directory and writes a manifest skeleton. Does NOT start the pipeline.

```
/new-run feature auth-timeout
/new-run bugfix slate-bandwidth-regression
```

What it does:

1. Validates `<pipeline>` matches a YAML in `.pipelines/` (`feature` or `bugfix`).
2. Validates `<slug>` is kebab-case (lowercase ASCII + hyphens only).
3. Generates `run_id = "<YYYY-MM-DD>-<slug>"` from today's date.
4. Creates `.agent-runs/<run_id>/`.
5. Copies `.pipelines/manifest-template.yaml` into the run dir as `manifest.yaml`, pre-filling only the `id` and `type` fields. Every other field stays as the empty default for you to complete.
6. Displays the manifest contents and tells you to fill it in before starting the pipeline.

The slash command file: [`.claude/commands/new-run.md`](../../.claude/commands/new-run.md).

### `/run-pipeline <pipeline> <run-id>` — orchestrate the run

Reads the pipeline definition and the run's manifest, then walks every stage in order. Stops only at human-approval gates and on failure.

```
/run-pipeline feature 2026-05-09-auth-timeout
/run-pipeline bugfix 2026-05-09-slate-bandwidth-regression
```

What it does at each stage:

| Stage `role:` | Handler |
| :--- | :--- |
| `human` + `gate: human_approval` | Asks via `AskUserQuestion` — user types `APPROVE` or describes a block. |
| `pipeline` + `command: ...` | Runs the command via `Bash` (the only such stage today is `policy`, which executes `python scripts/policy/run_all.py --run <run-id>`). |
| Any agent role (`researcher`, `planner`, `test-writer`, `executor`, `verifier`, `manager`) | Spawns an isolated subagent via `Agent` with the role file as its prompt and every prior artifact as context. The subagent must produce its named artifact. |

Every stage outcome appends one line to `.agent-runs/<run-id>/run.log`:

```
2026-05-09T04:30:00Z | research | COMPLETE | research.md written
2026-05-09T04:32:11Z | plan     | COMPLETE | plan.md written
2026-05-09T04:32:30Z | plan     | BLOCKED  | needs scope clarification
```

The slash command file: [`.claude/commands/run-pipeline.md`](../../.claude/commands/run-pipeline.md).

## Resuming a partial run

Re-invoke `/run-pipeline <pipeline> <run-id>` with the same arguments. The runner reads `run.log`, identifies the first stage that does NOT have a `COMPLETE` entry, and starts there. `FAILED` and `BLOCKED` outcomes count as incomplete — the runner re-runs them.

This means:

- After a policy failure, fix the violation and re-run. Policy will re-execute and the rest of the pipeline picks up from there.
- After a verifier marks a criterion `NOT MET`, the manager will likely return `BLOCK` or `REPLAN` and you re-issue commits for the executor stage; re-running the pipeline will redo execute → policy → verify → manager.
- After a human gate `BLOCKED`, address the requested change in commits, then re-run; the gate question fires again.

## Where artifacts land

Every run writes only inside `.agent-runs/<run-id>/`. That directory is gitignored — runs are local-only by design, and the artifacts are reproducible from the run inputs (manifest + role files + repo state).

A typical feature run produces (in this order):

```
.agent-runs/<run-id>/
├── manifest.yaml
├── research.md
├── plan.md
├── failing-tests-report.md
├── implementation-report.md
├── policy-report.md
├── verifier-report.md
├── manager-decision.md
└── run.log
```

The bugfix pipeline drops the separate `failing-tests-report.md` (the executor's reproduction step covers it) and adds `reproduction-report.md` instead.

## Reading the result

Two files tell you everything:

1. **`run.log`** — every stage outcome with timestamps. Failures and blocks are obvious.
2. **`manager-decision.md`** — the manager's verdict, which is exactly one of:
   - `**Decision: PROMOTE**` — every exit criterion met, ready for the final human merge gate.
   - `**Decision: BLOCK**` — at least one Blocker exists; manager names the smallest fix set.
   - `**Decision: REPLAN**` — the manifest is wrong; manager states which field needs to change.

If `manager-decision.md` does not exist, the pipeline did not reach the manager stage — `run.log` will show why.

## The three human gates

The pipeline stops for human approval at three points (`feature.yaml`):

1. **Manifest gate** — before any agent runs. You confirm the manifest captures the work correctly.
2. **Plan gate** — before any test or code is written. You confirm the planner's approach.
3. **Manager gate** — after the manager produces a decision. You confirm `PROMOTE` (and merge the resulting PR) or you reject and the run halts.

(`bugfix.yaml` skips the plan gate — bugfixes are short enough that the executor's reproduction stage is the natural checkpoint.)

These gates are non-negotiable. The runner cannot promote work without them. Even if every other gate passes, the manifest-, plan-, and manager-stage human approvals must each fire and return `APPROVE`.

## Policy gate

Wired in as the `policy` stage. Runs `python scripts/policy/run_all.py --run <run-id>` from the repo root. The script's exit code (0 = pass, non-zero = fail) determines whether the pipeline advances. The combined output is captured into `policy-report.md` so the manager can quote it verbatim.

The checks registered in [`scripts/policy/run_all.py`](../policy/run_all.py) live in [`scripts/policy/`](../policy/):

- `check_allowed_paths.py` — every changed file must be inside the manifest's `allowed_paths` and outside its `forbidden_paths`.
- `check_ffmpeg_wrapper.py` — only `civiccast/stream/_ffmpeg.py` may invoke ffmpeg via subprocess (ADR 0007).
- `check_adr_gate.py` — ADRs are immutable once Accepted; new ADRs allowed, modifications block.
- `check_no_todos.py` — no TODO/FIXME/HACK markers in `civiccast/` source (CLAUDE.md hard rule).

## Adding a new role or pipeline

1. Write the role file: `.pipelines/roles/<new-role>.md`. Make it self-contained — see existing roles for the shape (Job / Inputs / What to produce / Hard rules / Output checklist).
2. Reference the new role in a pipeline YAML stage's `role:` field.
3. Re-run any in-flight runs from the `manifest` stage; the new pipeline definition takes effect at the next walk.

## What the runner does NOT do

- It does not run tests or builds — those are the executor's job.
- It does not enforce any policy outside `scripts/policy/run_all.py`. Adding a new policy means adding a new check script and (if it should run on every pipeline) wiring it into `run_all.py`.
- It does not catch the case where a subagent claims COMPLETE but writes a wrong artifact — the verifier role exists for that.
- It does not merge or push code. The final `human_approval_merge` gate happens outside the pipeline at the GitHub PR review step.
