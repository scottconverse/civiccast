# Branches

One product line. `main` carries it.

CivicCast is a **native Windows** station-in-a-box: a signed installer, a
Windows service registered through the SCM, and a bundled runtime. No WSL, no
Docker, no Linux install target.

| | `main` |
|---|---|
| Product | Native Windows line |
| Install shape | Signed installer registers a session-0 Windows service that supervises the control plane, Postgres, and the media workers from a bundled runtime |
| Governing decision | [ADR 0021](docs/adr/0021-native-windows-runtime.md) introduced the native runtime; the owner retired the WSL2 lane on 2026-08-19 |

Branch from `main`, target `main`. There is no second line to choose between.

## Where the old line went

This repository was created on 2026-08-19 by copying the native product out of
[`scottconverse/civiccast-wsl-legacy`](https://github.com/scottconverse/civiccast-wsl-legacy) with
**fresh history**. That repository (named `scottconverse/civiccast` until
2026-11-08, when it was renamed and archived; this repository then took the
`scottconverse/civiccast` name) is now private and still holds:

* the WSL2/Ubuntu install path and its `docker/`, `deploy/systemd/` and
  Linux-GStreamer tooling,
* the published `v1.0.0-rc18` WSL-line release,
* every dated audit, release-verification log and tester handoff from
  2026-05 onward,
* the full commit history of both lines.

Nothing was deleted there. What did not come across is still there to be read.

## What is deliberately not here

Carried over only if it belongs to a native Windows product:

* `docker/`, `deploy/systemd/`, `civiccast/egress/{service_unit,recovery,soak}.py`
* the Linux GStreamer container build (1.28.4) — native Windows uses the
  pinned `gstreamer-*` **1.28.5** PyPI wheels instead
* the installer's WSL2 bootstrap lane
* `docs/audits/`, `docs/research/`, `tester-handoff/`, the retired line's
  dated handoff memos, and its scratch runs. (This repository does have its
  own `docs/releases/` -- the native beta verification records plus some
  earlier evidence notes -- and a committed `.agent-runs/native-windows/`
  audit-evidence tree; those are native-line records, not carried-over
  scratch.)

The six design specs that lived under `.agent-runs/native-windows/specs/` were
hand-carried into [`docs/design/`](docs/design/) — they are real design records,
not scratch.

## Release identity

v1.0.0-beta.20 is the current public release, published 2026-10-21 (MDT) as a
GitHub pre-release (not a production release). Its signed installer was built from source
ea8fb798; PR #259 subsequently merged as a1744918. See the [Beta 20 verification
record](docs/releases/v1.0.0-beta.20-verification.md) for the exact package and
its scoped checks.

Beta 19 and earlier releases are superseded; Beta 8 and Beta 9 were never
published, and Beta 2 exists only as an internal upgrade-baseline kit. See
[`docs/releases/release-truth.yaml`](docs/releases/release-truth.yaml) for the
authored state of each tag and [`docs/releases/`](docs/releases/) for
release-specific history. Tag movement and publication require current owner
authorization; older release-process notes do not grant blanket approval for
a new publication.
