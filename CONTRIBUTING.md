# Contributing to CivicCast

Thanks for considering a contribution. CivicCast is an open-source public-good project; the standards below exist to keep it that way.

## Before you start

1. Read [CLAUDE.md](CLAUDE.md) — the project's orientation document. It explains the verification that actually gates this repo, role posture, and what is and isn't in scope.
2. Read [BRANCHES.md](BRANCHES.md) for the native Windows product scope and branch target. This repository has one product line, carried on `main`.
3. Read the canonical spec, [`docs/spec/3.0/civiccast-3.0-station-in-a-box-MASTER.md`](docs/spec/3.0/civiccast-3.0-station-in-a-box-MASTER.md), for what the product is and how it's structured. (`docs/spec/spec.md` self-declares historical — do not implement against it.)
4. Read [ARCHITECTURE.md](ARCHITECTURE.md) for the system map, module boundaries, deployment posture, and open gates. Use the ADRs at [docs/adr/](docs/adr/) as the deeper contract once the overview gives you the shape.

## Developer Certificate of Origin (DCO)

Every commit must be signed off under the [Developer Certificate of Origin 1.1](https://developercertificate.org/). There is **no Contributor License Agreement (CLA)**.

Add `Signed-off-by: Your Name <your.email@example.com>` to every commit message. Use `git commit -s` to add it automatically. The pre-commit hook enforces this.

## Commit messages — Conventional Commits

This project uses [Conventional Commits 1.0.0](https://www.conventionalcommits.org/en/v1.0.0/):

```
feat: add HLS adaptive bitrate ladder to civiccast-stream
fix(captions): handle empty audio frames without crashing
docs: clarify three-tier publish in spec §2.6
refactor(schedule): extract conflict-detection into a dedicated module
test(archive): cover IA upload retry path
chore: bump pre-commit-hooks to 4.6.0
```

Allowed types: `feat`, `fix`, `docs`, `refactor`, `test`, `chore`, `perf`, `style`, `ci`, `build`, `revert`. Breaking changes use `!` after the type/scope and a `BREAKING CHANGE:` footer.

## Local development

CivicCast targets Python 3.12+ and uses [uv](https://docs.astral.sh/uv/) as
the canonical workspace tool (per [ADR 0004](docs/adr/0004-python-workspace-tool.md)).
Plain `pip` works but the dev-extras incantation is wider; prefer `uv` if you
have it.

### First-time setup

```bash
# With uv (recommended):
uv sync --all-extras --group dev
uv run pre-commit install --hook-type pre-commit --hook-type commit-msg

# With pip:
pip install -e ".[dev]"
pre-commit install --hook-type pre-commit --hook-type commit-msg
```

Tests assume the dev group's deps. A bare `pip install -e .` will skip
`pynvml`, `testcontainers`, etc. and will surface odd test failures. Use
`pip install -e ".[dev]"` (or `uv sync --all-extras --group dev`) any time
you intend to run the test suite.

### Prerequisite-specific CI coverage

The existing `ci-test` Windows job runs the native media and installer cases
that cannot execute in the Linux unit job. It stages only the pinned GStreamer
closure, TSDuck runtime and small Tauri NSIS compiler, uses a fixture FFmpeg
with `libx264` and SRT, and
bootstraps bundled GI before pytest imports. Tests run sequentially; the caption
pressure test retains its three concurrent workers and three attempts. Windows
PowerShell subprocesses receive the standard Windows PowerShell module paths.

The same workflow runs caption persistence against a disposable loopback-only
PostgreSQL container through `CIVICCAST_POSTGRES_TEST_URL`; the existing harness
creates and drops a unique test database. Never point this URL at a station DB.
Its separate external-integration job installs `agenda-js-import` for real
package-import checks and enables the agenda-source and TSDuck network proofs.
Those results depend on remote services and are separate from offline units;
import checks do not claim to run a browser crawl. The `ci-docs` Pandoc/TeX job
runs the four real manual-render cases when its documented paths change.

The explicit prerequisite-specific rosters reject skipped JUnit cases. A missing
runtime or tool must fail that coverage rather than silently count as a pass.
The workflow files contain the exact node selections and bootstrap commands.

### Windows: line endings

CivicCast stores LF in the repo; CI on Linux validates `ruff format` against
LF. Windows checkouts with `core.autocrlf=true` swap to CRLF in the working
tree, which produces local-only `ruff format --check` failures even though CI
is fine.

The `.editorconfig` at the repo root pins editors to LF, but `git` itself
needs to be told. Either of these works:

```bash
# Per-repo: tell git to use LF in the working tree (recommended)
git config core.autocrlf input
git add --renormalize .

# Or globally:
git config --global core.autocrlf input
```

### HTTPS-only manifest URLs

`AssetMetadata.manifest_url` and `AssetMetadata.poster_url` reject plain
`http://` URLs by default — the public portal must not load civic video
over an insecure transport (spec §4.1, §15). For local development
against a plain-HTTP origin (the dev portal-public Vite server, a local
nginx stub, or a test fixture), set the escape-hatch env var:

```bash
export CIVICCAST_ALLOW_INSECURE_MANIFEST=1
```

Do not set this in production. The escape hatch is named verbosely on
purpose so it does not get copied into a deployment script unintentionally.

### Running the operator portal locally

The standalone operator portal development app can use installer-managed local
SQLite storage. The native Windows service uses its installer-provisioned local
PostgreSQL database. Start the standalone backend with the normal app factory,
open the Setup screen (`/operator/`, from the same computer), and choose **Prepare
storage** before creating the first admin. Use Postgres only when you are
testing Postgres-specific migration or deployment behavior; see
[`civiccast/apps/portal-operator/README.md`](civiccast/apps/portal-operator/README.md)
for both paths.

## Pull requests

1. **Target `main`.** This repository ships one product line (the native
   Windows station) and `main` carries it — there is no second line to
   branch or target. See [BRANCHES.md](BRANCHES.md), which also lists the
   extra Windows-only gate (`native-beta-pack-contract` +
   `native-beta-installer`) that runs on installer PRs.
2. **Branch naming:** `fix/short-description` or `feat/short-description`.
3. **Pre-flight checks before opening the PR:**
   - `ruff check .` and `ruff format --check .` pass
   - `mypy` passes for changed modules
   - `pytest` passes (scoped to the changed area at minimum; full suite before claiming release-candidate readiness)
   - Pre-commit hooks installed and green (`pre-commit run --all-files`)
   - DCO sign-off on every commit
4. **PR description:** Use the [PR template](.github/PULL_REQUEST_TEMPLATE.md). Name the branch/line and target, the spec section(s) touched, the 5-lens self-audit result (see below), and what was actually run to verify the change.
5. **Conversation resolution.** On the native line, GitHub branch protection blocks merge while any PR review thread is unresolved (`required_conversation_resolution`), independent of approval count. Resolve every thread, don't just reply to it.
6. **Merge and release authority follows the owner's current instructions.** Historical beta.5 delegation notes are not current release authorization. Do not merge, tag, or publish without the applicable owner authorization and repository policy.

## The verification that actually gates this repo

Per [CLAUDE.md](CLAUDE.md), every contribution runs the same verification CLAUDE.md itself is held to:

- **Per-change careful-coding** ([docs/templates/careful-coding.md](docs/templates/careful-coding.md)) — read callers, trace runtime context, fan-out grep, name the data contract and blast radius before editing; re-read end-to-end and prove the render/data path after.
- **Hostile 5-lens self-audit before every push** ([docs/process/5-lens-self-audit.md](docs/process/5-lens-self-audit.md)) — engineering, UX, tests, docs, QA, reported in the fixed format that doc defines. Optional guidance, not a push gate (CLAUDE.md, "Verification and release").
- **Cross-agent review on the PR** — a reviewer that did not write the change checks the implementation and relevant proofs. Report findings and verification limits against the reviewed commit.
- **Claims-evidence binding** ([docs/claims/](docs/claims/), enforced by [scripts/policy/check_claims_evidence.py](scripts/policy/check_claims_evidence.py)) for any change touching the governed doc/claim set — a capability claim needs bound, executed evidence, not prose.
- **Windows installation proof** — the signed kit is installed, walked and soaked on the owner's real test PCs, and the result is written in the release's verification record (see the [release-candidate runbook](docs/ops/release-candidates.md)). Gate A is not required (owner decision 2026-11-14). Separate runtime soaks and station-device acceptance establish operational evidence beyond installation.

Each layer's trigger is different — careful-coding and the 5-lens self-audit are per-change/per-push discipline the contributor runs themselves (not CI jobs); claims-evidence and the cleanroom workflows are CI-enforced but scoped to the diff and event type above; cross-agent review and the clean-box VM proof are protocol-driven, not automatic. There is no version-number cadence (no "runs every rung" or "runs every 1.0"), but "runs on every change" is not true of every layer either — check each one's actual trigger before citing it as having run.

## Code style

- **Python 3.12+,** type hints throughout, `mypy --strict` for service modules.
- **Linter / formatter:** [ruff](https://docs.astral.sh/ruff/) (replaces black, isort, flake8). Configuration in [pyproject.toml](pyproject.toml).
- **Tests:** [pytest](https://docs.pytest.org/) + [hypothesis](https://hypothesis.readthedocs.io/). Coverage targets per [CLAUDE.md](CLAUDE.md): 80% service modules, 90% platform substrate, 95% streaming origin and syndication.
- **License header:** Every source file starts with `# SPDX-License-Identifier: Apache-2.0` and `# Copyright (c) The CivicCast Authors`.
- **Frontend (React):** Run the lint, build, API-contract, Playwright, and accessibility scripts from the relevant app subdirectory before opening a PR.

## Scope discipline

- **Stay in scope.** Adjacent issues that you notice get reported (open an issue, label appropriately), not fixed in the same PR.
- **Don't reopen closed architectural decisions.** See [CLAUDE.md](CLAUDE.md) for the closed list. If you believe a closed decision needs revisiting, open an RFC issue first.
- **Don't silently expand a PR's scope.** If a finding emerges mid-PR, explain its impact and whether it belongs in the current change or separate work.

## Binary artifacts never get committed

Installers, tester packages, proof kits, soak outputs, model weights, and media renders do **not** belong in this repository — not directly, and not through Git LFS.

- **Build artifacts that must be public** → GitHub **Release** assets.
- **Test, soak, and proof artifacts** → local disk, referenced from a committed text log by path and SHA-256. Commit the log, never the bytes.

This is enforced mechanically by [`ci-blob-size-guard`](.github/workflows/ci-blob-size-guard.yml), which runs on pull requests to every branch and fails on any added or modified file over 5 MiB, any Git LFS pointer file, and any `.gitattributes` change that introduces a `filter=lfs` rule. If a large file genuinely belongs in the tree, add it to [`.github/large-blob-allowlist.txt`](.github/large-blob-allowlist.txt) in the same PR with a dated reason, so the exception is reviewed rather than assumed.

The rule is mechanical because the consequence is permanent: objects pushed to GitHub LFS are **not** reclaimed by deleting the files later, only by a GitHub Support purge. Release-proof automation in this repo once committed the Windows tester package, installer, and proof kit on nearly every heartbeat commit, parking 106 objects / 17.25 GB in LFS storage before anyone noticed.

## Security

Do not file security reports as public issues. See [SECURITY.md](SECURITY.md) for private disclosure.

## License of contributions

By contributing, you agree your code is licensed under [Apache License 2.0](LICENSE-CODE) and your documentation under [Creative Commons Attribution 4.0 International](LICENSE-DOCS), per the DCO sign-off on each commit.

## Questions

Open a discussion or issue. For coordination on native-line work, reference the relevant slice or keystone (K1, K2, K3, …) and link its tracking issue.
