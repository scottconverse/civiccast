# CivicCast — coder handoff (written 2026-10-02, evening Mountain time)

For the AI that takes over **coding** on this project. Written by the outgoing coordinator (Claude, Sonnet 5.5).
Read it top to bottom before touching anything. Everything it points to is on GitHub
(`scottconverse/civiccast-native`) or is marked **LOCAL-ONLY** with a reason.

All times in this repo's logs are **Mountain** (`-0600` in October, MDT). A trailing `Z` is UTC. Read the offset; never assume.

## Current checkpoint (2026-10-09)

The published release is Beta 11; Beta 12 is still unpublished. Work is on
`codex/beta12-reliability`; the failed hosted-CI checkpoint was
`4a44d8d7132deb07db0c3002bdf10b35735180ef`. Migration `0089` now qualifies
its `remote_guest_sessions` columns to PostgreSQL's `civiccast` schema. The
full migration-chain upgrade, downgrade and re-upgrade passed on an isolated
PostgreSQL 17 cluster (4 focused checks passed); this was not the host database.

Provisional signed build `38000097070` was cancelled before package acceptance
because CI still had unresolved failures. Randomized run `38000087246` reported
104 failures at this checkpoint (104 failed, 11,073 passed, 83 skipped).
The raw randomized log is at
`C:\Users\scott\AppData\Local\Temp\beta12-randomized-suite-38000087246\pytest-random.log`.
Follow-up diagnosis found 73 PostgreSQL
migration failures and one stale head assertion, now corrected and checked
against an isolated PostgreSQL 17 cluster. Five readiness-test failures came
from missing enabled egress fixtures; three CG assertions used an obsolete
synthetic-overlay request; two collection/floor expectations and one legacy
release-body assertion were stale. Their focused updates now pass 41 local
checks. The manual TOC count is now 638; the focused docsite suite passed 20
tests and rendered-manual currentness passed. Eighteen policy failures came
from eight current-source blob mismatches in two historical external-evidence
claims. Current-source-only rebinding covers 14 changed inputs (eight
role bindings: eight code/test bindings across four files and six bindings to
one workflow file. Direct D2 evaluation reports zero drift; the complete
claims suite passed 125 tests in 81.86 seconds. Historic findings and the
separate Session 0 pre-login boundary are unchanged. The old Unit run
`38000087166` was still running its
pytest step at the latest poll; these branch follow-ups have no fresh hosted-CI
result yet. Do not treat this source or the cancelled build as a qualified
Beta 12 installer. The historical Beta 10 status and plan below are retained
as context and are superseded by this dated checkpoint.

---

## 0. The 60-second version

- **v1.0.0-beta.10 is published** (GitHub pre-release, 2026-10-02 20:31 MDT). Tag `v1.0.0-beta.10` → commit
  `b652084707367d45913807ed2a525f15a9a1e4a4`. It is a *beta candidate*, not production. **Do not move that tag, do not
  rebuild it, do not "fix" it.** Scott's words: beta.10 is published *as is*.
- `origin/main` = beta.10 code + docs-only commits (public surfaces say beta.10 is current). The code on `main`
  equals the tag; every later commit on `main` so far is documentation.
- **You own code from here.** The outgoing coordinator writes **no more code**. Its remaining work is documentation only
  (user manual, landing page, in-app help *text* delivered as files for you to wire in). See §9 for the split.
- The single biggest open product problem: **live captions fall behind and drop audio under load** (§6, P1).
- The single biggest repo problem: **the repo and the proven product have diverged** — 24 tests pin features the tree no
  longer has, and ~113 tests are red (§6, P2, finding C-001). Fix the truth before adding features.
- Scott (owner) is a project manager, not an engineer. Engineering decisions are yours. He does **not** use a command line.
- The stakes are real: a PEG station and a town are waiting on this. Never report readiness above the evidence (§2, §7).

---

## 1. What the project is

CivicCast is an open-source (Apache-2.0 code), self-hostable civic broadcast platform for PEG / local-government stations:
record a meeting, generate offline captions, let an operator review/approve it, draft an AI summary linked to the transcript,
schedule it, publish it on a branded resident portal with captions — and play it out 24/7 on one or more channels. It runs on
commodity Windows hardware as a **native Windows station-in-a-box**: a signed installer that registers a Windows service
(`CivicCastSupervisor`) supervising the FastAPI control plane and Postgres; the control plane runs the in-process event bus (NATS was removed) and owns the GStreamer playout workers, ffmpeg/TSDuck
relays and a private Ollama. **No WSL, no Docker, no Linux target** (the old WSL2 line is a separate private repo).

Target hardware (Scott's ruling): a 32 GB AMD Ryzen 7-class box with an iGPU only; must also *run* (slowly) on 16 GB.
The lab machine (NVIDEABLACKWELL, RTX 5070 Ti) is the ideal tier — optimize for it, never require it.

**Settled owner decisions — do not re-ask or re-litigate** (Scott has had to re-explain these for months):
- **Installer:** small base (the bootstrap is ~233 MB; rule is < 300 MB — already met). It finds/downloads the big components
  during install; **only the big ones** get the explain-what/why/size/time + live-progress screen. Captions floor tier =
  `medium`; `large-v3` is an optional quality download; summary model pick = `gemma4-12b`. The installer inventories the machine
  and *recommends*. (Measured sizes and tiers: `docs/ops/`, the lock files at repo root, `CHANGELOG.md`.)
- **No GPL in the box, ever** (no x264/x265). OpenH264 is the software fallback; HEVC is hardware-only. Pinned, checksummed,
  minimal plugin closure; GStreamer 1.28.5 pinned. NVIDIA hardware H.264 encode is queued (§6 P6) with an open owner question
  about H.264 patent posture (default: keep OpenH264 unless Scott says otherwise).
- **Front door = the product.** No managed/silent/scripted-install beta, ever. Install + first run must be *excellent*
  (real hardware inventory, honest progress/ETA, working cancel/retry, no dead-end screens). Never drift toward "tag now with
  caveats".
- **Captions:** legally driven by ADA Title II / WCAG 2.1 AA (federal deadlines 2027/2028; Colorado enforceable now); no statute
  fixes a latency number. The "10-second real-time rule" is *not* a legal requirement; captions must exist, need not be
  real-time; the engine adapts to hardware. Do not claim an FCC "PEG mandate" (that was bad research, corrected 2026-07-30).
- **Loudness:** target −16 LUFS ±1 over 240 s windows. A window whose *source* is too quiet for the ride's +18 dB maximum
  lift is **listed, not failed** (reach floor ≈ −35 LUFS). This is settled; do not turn it into a failure.
- **Beta acceptance bar:** 3 channels (education, government, public) airing real programming at once, captions in the output,
  picture/sound/captions aligned, watched runs of 30 min / 2 h / 4 h / 8 h with evidence on disk, schedules loop, program changes clean.
- Beta tester: "LPM" (Sergio) checks GitHub daily for releases. The release page is public.

---

## 2. How to work with Scott (rules he has given, in his words or close to them)

1. **Record, recommend, then act.** When a decision is open: write the issue, the options with pros/cons and your recommendation
   in `ops/beta10-oversight/OVERSIGHT-LOG.md`, **take your recommendation**, keep working, so he can overrule later. Only
   **push / merge / tag / release**, and irreversible deletion of things that are not yours, wait for him. (A previous
   coordinator sat idle for two days asking questions. That is the failure to avoid.)
2. **Plain English reports.** TL;DR first; describe every item; explain every piece of jargon. He is a human PM, not a computer.
   While any long run is live: check every 10 minutes, write a **full report every 30 minutes** (pass *and* fail, with numbers,
   nothing hidden). He said: "I NEED REPORTS on progress. DO NOT skip that or hide info."
3. **No command lines for Scott.** If a step needs his hands, give one self-contained copy-paste *prompt for an agent*, not a shell
   command. Give absolute `C:\` paths for any file you deliver.
4. **No modal question dialogs** (don't use an AskUserQuestion-style tool); ask in plain chat text, sparingly.
5. **Files first, chat second.** Anything a future session needs goes in a durable file *before* you say it in chat. Chat dies at
   the session boundary; he has been bitten by this repeatedly.
6. **Never claim beyond the evidence.** "Wrote it" ≠ "ran it" ≠ "checked it is correct". If you were wrong, say so plainly and fix the
   record. Do not defend a challenged claim — re-verify it. Be most suspicious of the dramatic reading of a result.
   (The outgoing coordinator itself shipped one overstated sentence in the beta.10 release notes — "the installer downloads the
   AI components itself" — and had to correct it after publication; see §7.)
7. **Branches before main** for code. Feature branches and PRs; no unreviewed work straight onto `main`. (Documentation is the one
   exception Scott approved: docs-only commits may go straight to `main`.) `main` is branch-protected ("changes must be made through
   a pull request") but the owner account can bypass it — don't rely on that for code.
8. **File hygiene.** This project has produced hundreds of GB of throwaway files. Don't commit large or generated files; don't use
   LFS for scratch; everything needed to resume must be on GitHub; delete scratch you create.
9. **Every wait gets a deadline.** Any background wait needs an appearance deadline, a completion deadline, and an alarm. Silence is not
   progress. Confirm async things actually started. Expired Monitors leave orphan `while true` loops — give every poll loop its own end.
10. **No softeners in instructions you write** ("if convenient", "optionally", "if time allows"): every step is mandatory or omitted;
    conditionals state both branches.
11. **Primary sources for any claim that gates engineering/product/legal work**, with provenance recorded and independent corroboration.
12. **Docs first, then forums** for any model/config recommendation; never recommend from memory.
13. **The lab box is a lab.** You may restart the station, install candidates, and launch the sandbox freely; do not ask. Never leave the
    machine needing a reboot (§4).
14. If you use an LLM-based sub-agent for review, set its model explicitly; don't let it inherit silently. Avoid security-jargon trigger
    words in chat that can trip model safety downgrades (use "cold audit", "skeptical review").

---

## 3. Where everything is

### On GitHub (`scottconverse/civiccast-native`)
| What | Where |
|---|---|
| Release (assets, notes) | https://github.com/scottconverse/civiccast-native/releases/tag/v1.0.0-beta.10 |
| Tag | `v1.0.0-beta.10` → `b652084707367d45913807ed2a525f15a9a1e4a4` |
| Release line | branch `release/beta10` (same commit as the tag) |
| Current state of docs | `main` (landing page is `docs/index.html`, served by GitHub Pages from `main` `/docs` → https://scottconverse.github.io/civiccast/) |
| **Caption fix work (not in beta.10)** | branch `fix/caption-tap` @ `a37f1a40` (U68–U72; see §6 P1) |
| Gate A harness fix experiments | branch `gatea/t4-budget-600-beta10` @ `2dbccb40` (throwaway; do **not** merge as is; see §6 P3) |
| Release truth (single source for "what is current") | `docs/releases/release-truth.yaml`, checked by `scripts/policy/check_release_truth.py` |
| Verification record + evidence | `docs/releases/v1.0.0-beta.10-verification.md`, `docs/releases/evidence/v1.0.0-beta.10-gate-a/` |
| Whole-repo audit (82 findings) | `ops/beta10-oversight/audits/audit-lite-whole-repo-beta10-2026-10-02.md` + `audits/slices/slice-{A..E}-*.md` |
| **Oversight record (the coordinator's brain)** | `ops/beta10-oversight/` — `OVERSIGHT-LOG.md` (authoritative history, ~600 lines; read the last 80 lines first), `POST-BETA10-BACKLOG.md`, `RELEASE-BETA10-PLAN.md`, `PROJECT-BRIEF.md`, `briefs/` (U-unit briefs), `reports/` (coder reports), `verdicts/` (every long-run verdict + incident notes), `questions/`, `bin/` (all tooling, §8), `bin/release/` (publish wrapper, parallel downloader, Gate A launcher) |
| Earlier handoff (still accurate for station/rung/coder mechanics) | `ops/beta10-oversight/HANDOFF-2026-10-01.md` |
| Archive tags (recoverable old work) | `archive/*` tags, incl. `archive/wip-civiccast-native-dirty-main-2026-10-02` (see §3.2) |
| Branch-cleanup restore list | `ops/beta10-oversight/branch-cleanup-2026-10-02.tsv` (206 merged/closed branches were deleted on 2026-10-02; restore list inside) |

### Local on this machine (NVIDEABLACKWELL, Windows 11)
All under `C:\Users\scott\Documents\Codex\2026-09-16\re\` unless stated.

| What | Path | Status |
|---|---|---|
| Release worktree (branch work) | `civiccast-release\` | git worktree — everything in it is on GitHub or in this handoff's commit |
| Other worktrees | `civiccast-ds\` (beta10-ds), `civiccast-ds-u68`…`u72`, `civiccast-gatea-t4\`, `civiccast-captionfix\`, `civiccast-docs2\`, `civiccast-audit\` | all branches are on GitHub; worktrees are disposable |
| **Oversight folder (live working copy)** | `civiccast-ds-oversight\` | the repo's `ops/beta10-oversight/` is a text-only mirror of it; the local copy also holds big run evidence (`runs\` 2.6 GB, `evidence\` 612 MB, `scratch\`, `staging\`) that is **LOCAL-ONLY by design** (too large; summaries are in `verdicts/`) |
| Scratch kit | `beta10-kit\` (89 GB) | **LOCAL-ONLY scratch.** `kit2\` = the exact published installer + packs + the 19.5 GB model bundle `station\`. The installer/packs are on the release; **the `station\` bundle is NOT on GitHub** (too large; the Actions artifact that held it expires ~1 day after its build) — it is regenerable by the build workflow's job 1 (~30 min) from the pinned manifests. Delete `beta10-kit\` only after Scott agrees or after you no longer need a local first-install kit. |
| Old main checkout | `civiccast-native\` | **Dirty, 193 commits behind `origin/main`.** Never reset/clean/stash it. Its uncommitted state was snapshotted untouched to `archive/wip-civiccast-native-dirty-main-2026-10-02` and `archive/wip-civiccast-native-stash0-2026-10-02` on GitHub on 2026-10-02. Work in `civiccast-release\` or a fresh clone instead. |
| Installed station | `C:\Program Files\CivicCast (Native)\runtime\` (python 3.12), data/logs under `C:\ProgramData\CivicCast\` | **Not a fresh install of the published setup.exe** (see §4) |
| Elevated helper | `C:\dev\ClaudeElevatedHelper\` (queue\, logs\helper.jsonl); trial scripts `C:\dev\civiccast-trial\` | needed for installs/rollbacks |
| Memory of the outgoing session | `C:\Users\scott\.claude\projects\C--Users-scott-Desktop-CODE\memory\` | **Do not copy it wholesale** — one file contains remote-access credentials. The relevant facts are distilled in this handoff. |

### 3.2 Why the old `civiccast-native` checkout matters
It held 117 modified/deleted files and 14 untracked files (≈2.9k lines changed) from September work, plus one stash. They were never
committed anywhere. The snapshot tags preserve them; nothing in them was reviewed. Treat them as *archaeology*: look before assuming
they matter (some are early versions of caption diagnostics later redone as U69–U72).

---

## 4. Machine facts and gotchas (hard-won)

- **The lab station (installed on this box)** runs build **C17** = beta.10 line + U68 (prep ffmpeg below-normal priority) + U69 (always-on
  caption shed diagnostic), installed by hand-copying candidate files over an earlier install via the elevated helper. It has been airing
  3 channels continuously since 2026-10-02 03:02. Rollback: `C:\dev\civiccast-trial\Rollback-Candidate.ps1` with
  `civiccast-ds-oversight\backups\c17-install`. The published installer has only ever been installed in **Windows Sandbox**, never on this
  box's real install — remember that when someone says "it works on the lab station".
- **Ports.** `11434` belongs to CivicCast's own bundled Ollama 0.30.6 (its supervisor respawns it). Scott's own Ollama 0.34.1 is on
  **`11435`** (health: `curl http://127.0.0.1:11435/api/version`). Use `127.0.0.1`, not `localhost` (IPv6 doesn't answer). A Codex install's
  global `openai_base_url` setting hijacks all OpenAI traffic; an `os error 10061` on an OpenAI request means a hijacked base URL, not an outage.
- **DeepSeek coder.** Optional cheap worker the coordinator used for implementation units: `ops/beta10-oversight/bin/ds.ps1` runs a headless
  Claude Code against Ollama `11435`, model `deepseek-v4.1-flash:cloud`, sandboxed (secrets stripped, git push blocked, own `HOME`). Details and
  the brief format are in `HANDOFF-2026-10-01.md` §5. You are free to use it, replace it, or write code yourself — **every unit must be audited by
  you before accepting** (§7, §8).
- **Windows Sandbox (Gate A).** One instance only. Launch **once**; the VM shuts itself down. Never rapid stop→relaunch. A leftover
  `WindowsSandboxRemoteSession` client window can be closed by closing that window's *client* process only. Never kill `vmmem*`/Sandbox server
  processes (`vmmemCmZygote` is a permanent, unkillable protected process — normal). `wsb list` often prints nothing even when it is running.
  A diagnostic guest must **replay every prior installer step** (the vc_redist `/install /quiet` step!) or the repro is invalid.
- **PowerShell 5.1 corrupts UTF-8** in text pipelines. Edit files with an editor tool (Edit/Write); never `Set-Content` pipelines on source.
  A Python `str.replace` on a PowerShell script once injected control characters (`\v`, `\0`) — write such files by heredoc and parse-check them.
- **Claude's shell is an MSIX container** on this box: AppData writes/renames are redirected and registry views can lie. Verify via the
  elevated helper before asserting a registry/AppData fact.
- **Git Bash:** `start /b` (any `/x` switch) turns into `B:/` and pops a dialog on Scott's desktop — use `//`, `MSYS_NO_PATHCONV=1` or
  `Start-Process`. `SHA256SUMS.txt` has CRLF (`tr -d '\r'`). Foreground `sleep N; cmd` is blocked in the tool — use background commands/Monitor.
- **ffmpeg `movie=` filter:** git-bash/absolute paths silently yield zero results; `cd` + bare filename; control-test before trusting a 0.
- **pack build PATH overflow:** app-payload pack "input line is too long" = cmd's 8191-char PATH limit inside `vcvarsall`; build with a trimmed PATH.
- **Don't wedge the machine / don't burn the disk.** Never open `C:\ProgramData\CivicCast\data\egress\live-hls\` (huge, live). Heavy ffmpeg
  measurements run at idle priority (`bin/monitors/lowloud.ps1`; Git Bash `nice` does not lower Windows priority). No whole-disk `find`.
- **Tests on this box:** a machine-level `CIVICCAST_STAFF_TOKENS` environment variable makes ~254 tests fail falsely (audit finding C-005).
  Run the suite with it unset. Native `*_win.py` tests must not hard-code token tier (dev box = UAC-split non-admin, CI = full admin).
- **pwsh 7.6.6** (user-scope winget install) lives at `C:\Users\scott\AppData\Local\Microsoft\WindowsApps\pwsh.exe`; Gate A needs it.
- `gh` is authenticated as `scottconverse` (keyring) and the owner's PAT is agent-accessible by design — scope trust designs as process
  integrity, not credential boundaries.

---

## 5. How a release is built, proven and published (what actually worked on 2026-10-02)

1. **Build** — GitHub Actions `native-beta-candidate-artifacts.yml`, `workflow_dispatch` with `build_target=hosted`, on the release branch.
   Three jobs: (1) provision + sign the station model bundle (~30 min), (2) build + sign the installer and packs (~25–40 min), (3) assemble and
   upload the kit (~30 min). Signing uses Azure Trusted Signing (publisher CN=Scott Converse) and the pack-signing key from repo secrets.
   **Artifacts retain for ~1 day.** Download immediately. Two job-2 failures on the first build (`npm ci` exit 0xC0000409; pack-verify
   `dynamic_trace` flagging WindowsApps system paths) were runner flakes: `gh run rerun --failed` fixed them. If the *same* step fails twice
   for one cause, diagnose — do not blind-retry (and do not widen `dynamic_trace` without Scott).
2. **Download** the ~19.5 GB model bundle with the parallel ranged downloader `ops/beta10-oversight/bin/release/pdl.py`
   (`gh run download` managed 3 MB/s; `pdl.py` is ~10× faster; args `url_file out size artifact_id`). Unzip into `kit\station\` (flatten the
   double `station\station`), verify against the checksum file (strip CRs).
3. **Prove** — Gate A harness `sandbox-lab\Run-GateA.ps1 -KitDir <kit> -SourceSha <sha> -RunId <build-run-id> -SoakMinutes 20` run with pwsh 7 in the
   background (template: `bin/release/launch-gatea5.ps1`). The kit must contain `setup.exe`, `packs\*.ccpack`, `station\`. Install into the VSMB-mapped
   `C:\CivicCastHostStore\install` takes ~35 min; a full run is ~60–75 min. Judged by `scripts/gate_a_verdict.py`: 10 criteria (install, activation,
   runtime, t2_render, t3_loop, captions, t4_engine, t5_soak, install_progress, completion). `PASS_FFMPEG_FALLBACK` is a named FAIL; only
   `PASS_PRODUCT_ENGINE` passes. Evidence lands in `sandbox-lab\evidence\<sha>\<ts>\`; live guest output in `sandbox-lab\output\`.
4. **Publish** — `scripts/release/publish_beta_candidate.py` (fail-closed: gh auth, kit layout, version identity, Authenticode, hashes, 2 GiB cap,
   **draft → verify every asset name+size → un-draft, which creates the tag atomically**; never run `git tag` by hand). It requires **all three Gate A
   lanes (clean, dirty, download-only) as GitHub artifacts** and its notes template claims all three passed. For beta.10, Scott waived two lanes, so
   the coordinator ran it through `bin/release/publish_beta10_waived.py` (keeps every check; substitutes honest lane verdicts and rewrites the
   Gate A claims in the notes and in `release-truth.yaml`). **If you publish again, use the standard script with all three real lanes** or
   write an *honest*, owner-approved waiver mode — never fabricate verdict artifacts.
5. **After the tag**: update `docs/releases/release-truth.yaml` (the publish helper edits it; `current:` flips with `--truth-status current`),
   then the prose surfaces, then run `scripts/policy/check_release_truth.py`, `check_no_contradictory_release_banners.py`,
   `check_release_identity.py`, `check_release_candidate_boundary.py`, `check_beta_handoff_docs.py`.
6. The tag commit **must equal the built commit** — no commits to the release branch between build and publish.

---

## 6. Open work, in the order I would do it

Each item says what is known, the evidence, and what *not* to assume. IDs match the audit and the oversight log.

### P0 — first hour (verify the ground before building on it)
1. `git fetch`; confirm `origin/main` ⊇ `b6520847`, tag `v1.0.0-beta.10` → `b6520847`, and that `main` code == tag code (`git diff v1.0.0-beta.10 origin/main --stat -- . ':!docs' ':!ops' ':!*.md'` should show nothing outside docs/ops/markdown).
2. Read the last ~80 lines of `ops/beta10-oversight/OVERSIGHT-LOG.md`, then the audit TL;DR, then `docs/releases/v1.0.0-beta.10-verification.md` ("What is not proven" and "Known limits").
3. Run the Python test baseline with `CIVICCAST_STAFF_TOKENS` unset. **Documented red counts to compare against**: `tests/policy/test_claims_evidence.py` 18 failures (code-blob drift); `tests/egress` 74 failures; 113 total after removing the token (70 stale-test, 24 code-bug, 18 claim-drift, 1 environment). If the numbers differ, find out why before doing anything else.
4. Check the lab station: `CivicCastSupervisor` running, 3 channels `egress state -> ON_AIR` in `C:\ProgramData\CivicCast\logs\control_plane-app.log`, free disk (654 GB at handoff), Ollama `11435` up, no stray Sandbox processes.
5. Read §7. Then pick the first unit.

### P1 — Live captions fall behind and drop audio under load (the main product defect)
- **Facts.** On the 8-hour C16 run the caption tap logged 13 catch-up discard events (~160 s of audio on a quiet machine across two channels); C15 had 5/20/50 s. No verify check failed because of a discard. The tap is chronically about one segment behind its bound on three channels and drops the oldest audio when it falls ≥ 9 segments behind for a whole 15-scan window. Discards cluster around long cold preparations, but also occurred with CPU ~50% and no prep running.
- **What was tried.** U68 (cold-prep ffmpeg at BELOW_NORMAL priority): installed in C17, did **not** explain the slow decodes seen with GPU 0% and CPU ~10%. U69 (always-on bounded shed diagnostic) works and is installed. U70 (bound live decode to one pass, temperature fallback off by default) — benchmark showed the fallback walk only happens on VAD-stripped windows, 17/17 live-shaped windows never walk; so U70's hypothesis is **only partially supported**. U71 split `asr_s` into `transcribe_s` / `persist_s` / `duration_after_vad` / `max_segment_temperature` and fixed an env-spelling defect; U72 hardened U71 per an independent audit. All on `fix/caption-tap` @ `a37f1a40`, audited, **not installed** (planned C18 = C17 + U70 + U71 + U72) and **not in beta.10**.
- **Do next.** Install C18 on the lab station (stage → `mkjob.py` → helper; §8), let U71's timing split run for a few hours under normal load, and read where the 8–15 s per batch really goes (decode vs persist vs publish). Then fix the actual cause. The lead alternatives: ASR throughput shared by three channels (do captions need per-channel batching?), the live tap not using the GPU, publish/persist stalls. **Do not declare a root cause from one measurement** — the first two hypotheses were both wrong or partial.
- **Watch for.** The env-var spelling trap (§7 #9): new switches spelled `CIVICAST_…` (one C) are silently inert when the station's registry sets `CIVICCAST_…`. Always resolve via `env_vars.resolve_renamed_env` and test against the *station's* registry spelling.

### P2 — Make the repo tell the truth (audit C-001, Critical; needs owner decisions)
- Several "base the slice on the station's installed (LIVE) bytes" commits (e.g. `a4ee3941`, `ce71c40a`) reset egress files to what was installed on the lab station. As a result **24 tests fail because the features they pin are not in the tree** (loudness true-peak guard, orphan-cache reaping, a U62 round-2 fence, …), and 70 more fail because they are stale. The shipped behaviour is what the 8-hour run proved, so this is not a field failure — it is that tree and tests disagree about what the product does.
- **Do.** For each of the 24: re-apply the feature (new unit + proof run) *or* delete/xfail the test with a recorded reason (owner decision per item — record + recommend + act). Then bring the 70 stale tests into line. Slice report: `audits/slices/slice-C-tests-ci.md`. Also: no CI runs on `release/beta10`, `main` requires no checks, the Linux CI gate would be red on any PR (C-002/C-003/C-004), 18 claims-evidence tests are red (rebind the claims, don't loosen the verifier).

### P3 — Gate A harness and release tooling (so the next release isn't a fight)
- **Harness defect (proven, evidence in `docs/releases/evidence/v1.0.0-beta.10-gate-a/run4-…` and `run5-…`).** The T4 engine check opens its TSDuck capture the moment the channel reports its sink "connected". On a slating channel that flag is true *by design* (`civiccast/egress/health.py: build_default_sink_health`), so the capture opens at worker start, and the GStreamer worker's first packets on a fresh install arrive **more than 60 s later** — the default 8 s/60 s capture sees nothing and the check fails. Run 5 (a throwaway harness waiting longer and retrying) passed with 5,445 packets on the 2nd attempt. **Fix the harness properly** (capture must wait for real packets or use a ≥ 120–180 s budget; do not merge the throwaway branch as is) and consider whether first-start latency itself deserves a product fix (why >60 s on a cold install?).
- **Gate A lanes never run for beta.10:** dirty/upgrade over a previous release and download-only. The baseline kit was wiped and the self-hosted runner `blackwell-builder` is offline (the workflow auto-triggers onto it — C-008). Rebuild a baseline kit (the previous published release, beta.7, is on GitHub) and runner, or run the lanes by hand with the harness.
- **First install with neither a kit nor an earlier install is NOT proven for beta.10** (the clean lane used the full kit incl. the `station\` model bundle; the activation step fails closed if it cannot find model packs). `INSTALL-WINDOWS.md` and the release notes say so. This contradicts the "installer downloads what it needs" product promise and the D1 front-door ruling — it is a **top-three item**.
- **Installer defects found 2026-10-02:** (a) the activation self-test waited 60 s for its private Ollama (needs ~61 s from a VSMB-mapped disk) — fixed in beta.10 (300 s; `NATIVE_AI_SELF_TEST_READY_TIMEOUT` in `civiccast/apps/installer/src-tauri/src/main.rs`); same cold-disk risk applies on real machines. (b) After the activation failure path the installer process itself crashed with **0xC0000005** — separate defect in the failure-containment path, not investigated, not fixed. (c) audit E-003: `open_operator_console` / `open_installer_log` run `cmd /C start "" <value>` after only a prefix check — a URL containing `&` is command injection (`main.rs` ~3777–3796, ~2943).

### P4 — Security and supply chain (audit; mostly owner-visible)
- **B-001 Critical:** anonymous contributor upload (`civiccast/contribute/router.py:439-460`) spools the whole body before any limit runs; add a body-size cap in middleware (as done for analytics) + free-space guard.
- **E-002 Critical (owner settings, not code):** signing secrets are not environment-gated; the signing action is a mutable tag (`azure/artifact-signing-action@v2`); `main` is protected only by a PR rule the owner bypasses. Needs a protected `release` environment with the owner as required reviewer, secrets moved into it, actions pinned to SHAs, unused `id-token: write` dropped. **Scott must do the repo-settings part** — give him an agent-ready prompt, not steps.
- **E-001/B-002 webhook subscription SSRF** (signing secret derived from public inputs; any http(s) URL accepted) — only reachable if `CIVICCAST_PROVIDER_WEBHOOK=real`. **A-001** program-change watchdog restart rung is unreachable on an async decline (the code path of the 34-hour education black/silent stall). **B-003/B-004/B-005** caption review rows/evidence WAVs/raw tap chunks and `active.vtt` are never pruned (disk growth over weeks). **B-006** a local authenticated user can wedge the supervisor control pipe. **A-004** the loudness ride's timeout cannot fire while its decoder read blocks. **E-004/E-005** the pip-audit gate scans `uv.lock`, not the shipped requirements file (37 of 81 pins differ). **C-007/E-015** workflow-dispatch inputs are interpolated into `run:` scripts. Full list with IDs, severities and status: the audit report.
- **D-011 (owner):** `CLAUDE.md` / `AGENTS.md` in the repo carry a stale standing release authorization — ask Scott to reconfirm or remove.

### P5 — Known limits of the published build (don't promise otherwise)
Live-caption audio can be dropped under load (P1); two government-channel program-change first attempts aborted and recovered in ~3 s (daemon first-attempt short-tail abort — a tail < ~1 s at a multi-segment boundary makes the first reload abort and the retry drops the tail; the fix is a ~2 s threshold or re-check at worker hand-off; self-heals, no viewer-visible effect); one single-frame (0.033 s) video drop at a piece join; the education channel's 34-hour stuck program change was fixed by a watchdog whose own restart rung has the A-001 gap. **Evidence for what *does* work:** one lab installation (earlier internal build C15) kept the government and public channels ON_AIR for ~59 hours with no service restart (27/28 programs, 111/112 seamless reloads, no slate/filler/crash) — two channels on one station, not two stations; both had caption discards and government had one isolated caption-file `PermissionError`; not every second was decoded for A/V quality. That run was **not** on the published build. Eight-hour C16 run on the same engine: 41 verifies/40 OK (the 1 raw FAIL adjudicated as a sampling blip), loudness 16/16, 50 program changes with 0 holes.

### P6 — Post-beta.10 backlog (Scott: "Don't forget these")
`ops/beta10-oversight/POST-BETA10-BACKLOG.md` is authoritative. Headlines: (1) GStreamer `fallbackswitch` (MPL) instead of `input-selector`; (2) `hlssink3` instead of the ffmpeg HLS relay (**blocker**: the runtime closure lacks `gstgio`, so `gsthlssink3.dll` aborts at registration); (3) NVIDIA `nvh264enc` to free CPU (owner patent-posture question open); (4) skip the double encode at prep; (5) two-pass `loudnorm` at prep (may be pulled forward if a loudness check fails again). Also in the file: pipe-accept thread leak parks teardown in `CloseHandle`; outgoing programme's last ~0.57 s of video never reaches the selector; published HLS playlist can reference an already-deleted segment (seen once); fossil HLS segments never cleared at relay start; conform-cache budget (20 GB default vs ~11 GB per 4.4 h asset → eviction thrash; the lab station runs 60 GB) needs a sizing decision for real appliances.

### P7 — Housekeeping (not urgent, but Scott asked for it)
- Delete the local scratch (`beta10-kit\` 89 GB after Scott agrees, `civiccast-ds-oversight\scratch\U59-2`, stale worktrees `civiccast-ds-u68…u72`, `civiccast-docs2`, `civiccast-captionfix`, `civiccast-gatea-t4` once nothing needs them). Everything they hold is on GitHub; see §3.
- Six environment variables are spelled with one `C` (`CIVICAST_…`) in code and two elsewhere; the docs match the code; rename-or-alias is an owner decision.
- `tests/**` sleeps in 145 files; real-Postgres tests skip silently by default (B-016).

---

## 7. History — mistakes that were made; don't repeat them

Each is something that actually happened. The fix is in italics.

1. **Stopping to ask.** A coordinator ended a session with a list of questions and sat idle for two days. *Record + recommend + act (§2.1).*
2. **Basing work on "live" bytes and resetting the tree to match** → 24 lost features, 113 red tests, tests and code disagreeing about the product (C-001). *Branch from the repo, port the station delta forward, never reset egress files to "what is installed".*
3. **Reporting readiness above the evidence.** Earlier "almost ready" reports to the town's board damaged credibility. *"Proved" means a check was run at the layer of the claim; "wrote it" ≠ "ran it" ≠ "correct".* The beta.10 notes initially claimed the installer downloads its AI components itself — unproven; corrected after publication.
4. **Trusting an instrument's dramatic verdict.** A loudness FAIL (−21.9 LUFS) was a wrong log-derived position (3226 s vs true 7206 s; the source there was −44.7 LUFS = quiet-source, excluded). *Verify the inputs behind a verdict before telling Scott it's real; adjudicate with evidence.*
5. **Starting a rung before all channels were on air** (cost C15 a failure; verify #1 ran 28 s after the install restart). *Start only when all three channels show `egress state -> ON_AIR`.*
6. **A harness gate that can't fail.** The Gate A engine check's "sink connected" trigger is true on slate by design; three sandbox runs failed for a *test* reason that looked like a product regression for ~6 hours. *Before blaming the product, ask whether the gate can distinguish success from failure; instrument the timeline (when does each side actually start?).*
7. **60 s hard timeouts on cold paths.** The installer's self-test limit (60 s) was missed by 1 s. *Cold disk / AV scan / VSMB mapping inflate first starts; size timeouts for the worst real machine, and put the constant in a documented name with a test.*
8. **An invalid diagnostic.** The first diagnostic guest skipped the installer's `vc_redist` step (postgres `0xC0000135`), producing a misleading repro. *Replay every prior installer step in a diagnostic guest.*
9. **Env-var spelling bug class.** `CIVICAST_` vs `CIVICCAST_` — new switches in U68/U69/U70 were silently inert. *Use `env_vars.resolve_renamed_env`; test against the registry the station really sets; check the audit watch-item.*
10. **Over-broad ignore rules** (`*.ts` ignore hid MPEG-TS fixtures *and* TypeScript) and committed bulk (242 tracked files / 13.1 MiB had to be removed). *Check what a rule matches; keep generated/large files out.*
11. **Hundreds of GB of throwaway files** (kits, rung evidence, worktrees, conform caches). *Delete what you create; never commit it; keep summaries not blobs.*
12. **Duplicate dispatch.** A long session lost an earlier dispatch to summarization and re-launched the same work. *Before dispatching a chain, list worktrees and running agents; write dispatches to the oversight log immediately.*
13. **Moving a branch a downstream job pinned.** *Never move a branch/SHA another job depends on; estimate high (UI/installer work is 2–3×).*
14. **Wedged machine.** Rapid sandbox stop→relaunch and killing `vmmem*` required reboots. *Launch once; let the VM self-exit.*
15. **A blind retry of a CI flake that was really two different failures.** *Same failure twice = diagnose; different failures = look at both.*
16. **Missing audit after each slice.** Six 20-minute sandbox runs each found one defect where one 2-minute static audit found 21. *Audit after every slice and before any expensive live run.*
17. **Claim registry rot.** 18 claims-evidence tests are red because bound code moved. *Re-prove or re-bind claims with evidence, never loosen the verifier.*
18. **Local-only "only copy" state.** An old checkout held ≈2.9k uncommitted lines; scratch held the model bundle. *If a future agent needs it, it must be on GitHub (or regenerable from GitHub) before you stop.*
19. **PowerShell 5.1 / MSIX / Git Bash environment traps** (§4) cost hours more than once.
20. **Releasing from an unreviewed night.** A full night of unreviewed commits landed on a main. *Feature branches, then review, then main.*

---

## 8. Engineering method that worked

- **One small unit at a time.** Brief (`briefs/Uxx.md`: rules block → what happened with evidence paths → numbered work items → deliverables), implement in its own worktree off the **repo** line, RED test that fails *for the behaviour reason*, GREEN fix, run the whole affected test file at both revisions and compare failing test *names*, confirm `git status` clean. Report in plain English first (`reports/Uxx.md`).
- **Audit every unit yourself before accepting** (re-run RED/GREEN on the exact bytes you'll install; hash-compare staged files). For anything riskier than a localized change, get an independent review by a different model with its model set explicitly.
- **Install a candidate on the lab station:** stage `staging\<NAME>\` = candidate files + `installed-base.sha256` (rows only for files being replaced — the installer refuses on base mismatch) → `python bin\monitors\mkjob.py <job> <NAME>` → verify the queued JSON → `Start-ScheduledTask -TaskName ClaudeElevatedDevHelper` (it does not poll) → read `C:\dev\ClaudeElevatedHelper\logs\helper.jsonl`. Rollback with `Rollback-Candidate.ps1` and the same backup dir.
- **Run a rung (watched long run):** `bin\rung.ps1 -Name <name> -Minutes 480` (starts `segment_keeper.py`, verifies every ~11 min, 240 s loudness captures every ~30 min). Watches in `bin\monitors\`. Details, reading verdicts, `VERDICT-NOTE.md` format: `HANDOFF-2026-10-01.md` §7.
- **Logging:** `powershell -NoProfile -ExecutionPolicy Bypass -File bin\log.ps1 -Kind <UNIT|AUDIT|DECISION|BASELINE|NOTE|LIVE|FINDING|VERDICT> -Text "…"`. Every decision gets issue/options/recommendation/what you did. Keep scripts in `bin\`; session scratch files disappear.
- **Tooling index (`ops/beta10-oversight/bin/`):** `ds.ps1` (DeepSeek unit launcher), `log.ps1`, `rung.ps1`, `rung_check.py`, `loudness_window_adjudicate.py`, `changeover_hole_watch.py`, `segment_keeper.py`, `peek.ps1`, `channel-state.ps1`, `monitors\*`, `release\*` (publish wrapper, downloader, Gate A launcher, diagnostic guest script).

---

## 9. What the outgoing coordinator is doing next (so you don't collide)

> **Update 2026-10-03:** the manual, landing page and the 63 in-app help specs are finished and on `main`. The single starting point for the built-in help work is [`CODER-HANDOFF-INAPP-HELP-2026-10-03.md`](CODER-HANDOFF-INAPP-HELP-2026-10-03.md). The list below is the original plan.

**Documentation only, no code.** Scott's plan, pushed straight to `main` in stages (no PR/CI; not part of the installer or beta.10):
1. A real **User Manual** (`docs/USER-MANUAL.md` + PDF + DOCX): a non-technical section (public-access volunteers, camera operators, editors, PEG staff) and a technical section for a small-city IT team, with Mermaid architecture diagrams, real screenshots, an appendix (commands, API, settings, ports, files, errors, glossary, checklists) and a measured-evidence section.
2. A rebuilt **landing page** (`docs/index.html`, GitHub Pages).
3. **In-app help text** — written as *files for you*, in `docs/in-app-help/` (one file per screen: current text, proposed text, source file path, and a mismatch list from auditing ~25 screen components under `civiccast/apps/portal-operator/src/screens/` and the installer). You implement it in code later (a beta.11 task); the coordinator will not touch code.

**Collision rules.** Don't edit `docs/USER-MANUAL.*`, `docs/index.html`, `docs/install-windows.html`, `README.md`, `docs/tester/*` or `docs/in-app-help/` while the sprint is running without telling Scott; if you change behaviour the docs describe, add a line to `ops/beta10-oversight/OVERSIGHT-LOG.md` so the docs can follow. The in-app docsite `civiccast/docsite/manual.json` is generated/bundled with the product — regenerate it as part of your code release, not by hand.

---

## 10. Glossary of internal names

- **C-numbers (C15, C16, C17, C18)** — lab-station candidate builds; **C16** = what the 8-hour acceptance run proved (≈ beta.10 engine); **C17** = C16 + U68 + U69 (installed); **C18** = C17 + U70 + U71 + U72 (staged on `fix/caption-tap`, not installed).
- **U-numbers (U1…U72)** — coder units; brief in `briefs/`, report in `reports/`.
- **Rung** — a watched long proving run of the live station (30 min / 2 h / 4 h / 8 h) with evidence and a `VERDICT-NOTE.md`.
- **Gate A** — automated station acceptance in Windows Sandbox: lanes *clean* (fresh install), *dirty* (upgrade over a previous release), *download-only* (reuse models already on the machine).
- **Kit** — `setup.exe` + `packs\*.ccpack` + `station\` (the ~21 GB AI-model bundle).
- **Slate / filler / FALLBACK_SLATE** — the generated "CivicCast" holding picture a channel airs when it has no valid source plan; filler = placeholder content. Neither should appear after startup on a healthy channel.
- **Conform / prep / preparer** — the pre-air step that normalizes a source asset to the playout format (cached in the conform cache); beta.10 prepares media *before* putting it on air (15–390 s cold).
- **Changeover / program change / reload** — switching a channel from one program to the next without a black/silent gap; "hole" = a measured gap; "seamless reload" = committed switch.
- **Caption tap / shed / discard** — the live ASR worker reading the channel's audio; "shed" = it fell behind and dropped audio to catch up.
- **ON_AIR / TRANSITIONING / STARTING / STOPPED / ERROR** — channel states in the egress daemon.
- **Ride** — the loudness leveling stage (max lift +18 dB). **LUFS / LU** — loudness units.
- **PEG** — public, educational, government access channels. **LPM** — the beta tester's organization.
