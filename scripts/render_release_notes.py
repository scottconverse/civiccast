#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright (c) The CivicCast Authors
"""Render the GitHub release body from the release-artifacts manifest.

Root-cause fix for a hand-pasted-checksum release body going stale the moment
an asset is rebuilt or re-uploaded (see the rc.2 release-body audit finding):
the tag/commit and every SHA-256 in the release notes now come directly from
the same manifest the assets were built with, never typed by hand.

Usage::

    python scripts/render_release_notes.py \\
        --manifest artifacts/release/civiccast-0.1.0-rc6-release-artifacts-manifest.json \\
        --tag v0.1.0-rc6

Prints the release-body markdown to stdout. The "Verification run before
publishing" section is intentionally not generated here -- it depends on a
test run that happens after this script would be invoked -- so the caller
appends that section (and the "Known boundary" section, which is static
prose) before publishing.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

# Filenames that belong in the release-body checksum list, in display order.
# Matches what a clean-machine tester is told to verify in
# docs/install/windows-release-trust.md.
_BODY_ASSET_SUFFIXES = (
    "-windows-setup.exe",
    "-windows-setup.exe.sidecar.json",
    "-clean-windows-proof-kit.zip",
    "-release-artifacts-manifest.json",
    "-windows-tester-package.zip",
)


def _select_body_assets(artifacts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_filename = {Path(a["filename"]).name: a for a in artifacts}
    selected = []
    for suffix in _BODY_ASSET_SUFFIXES:
        match = next((a for name, a in by_filename.items() if name.endswith(suffix)), None)
        if match is not None:
            selected.append(match)
    return selected


#: GitHub refuses a release body over 125,000 characters (HTTP 422 "body is
#: too long", measured live on the v1.0.0-beta.5 publish 2026-09-10 when the
#: [Unreleased] CHANGELOG section alone was ~130 KB). The "What changed"
#: section is bounded to this many characters at a line boundary; the full
#: text lives in CHANGELOG.md at the tagged commit and the body says so.
CHANGELOG_BODY_MAX_CHARS = 90_000


def candidate_kind(tag: str) -> str:
    """'release candidate' for vX.Y.Z-rc.N tags; 'beta candidate' for every other tag."""

    return "release candidate" if re.fullmatch(r"v\d+\.\d+\.\d+-rc\.\d+", tag) else "beta candidate"


def bound_changelog_section(text: str, *, max_chars: int = CHANGELOG_BODY_MAX_CHARS) -> str:
    """Return ``text`` unchanged if it fits, else the longest prefix that ends
    at a line boundary within ``max_chars`` plus a pointer to the full text."""

    stripped = text.strip()
    if len(stripped) <= max_chars:
        return stripped
    cut = stripped.rfind("\n", 0, max_chars)
    if cut <= 0:
        cut = max_chars
    head = stripped[:cut].rstrip()
    return (
        head + "\n\n_The CHANGELOG entry for this release is longer than a GitHub release "
        "body allows; this is the first part. The complete entry is `CHANGELOG.md` "
        "at the tagged commit._"
    )


def render_release_notes(manifest: dict[str, Any], tag: str) -> str:
    commit = manifest.get("source_state", {}).get("head", "")
    if not commit:
        raise ValueError("manifest source_state.head is empty; cannot render Source identity.")

    assets = _select_body_assets(manifest.get("artifacts", []))
    if not assets:
        raise ValueError("no release-body assets found in manifest['artifacts'].")

    lines = [
        f"# CivicCast {tag.lstrip('v')} Test Release",
        "",
        "This is the RC clean-Windows test release for CivicCast 4.0.",
        "",
        "Source identity:",
        "",
        f"- Commit: {commit}",
        f"- Tag: {tag}",
        "",
        "Clean-machine tester rule:",
        "",
        "Start by reading the docs before launching the installer:",
        "",
        "1. docs/tester/START-HERE.md",
        "2. INSTALL-WINDOWS.md",
        "3. docs/install/windows-release-trust.md",
        f"4. docs/releases/{tag}-verification.md",
        "",
        "If docs, release assets, manifest, setup sidecar, proof kit, or installer "
        "UI disagree about version, filename, checksum, or next step, stop before "
        "installing and report the mismatch.",
        "",
        "SHA-256:",
        "",
    ]
    for artifact in assets:
        lines.append(f"- {Path(artifact['filename']).name}: {artifact['sha256']}")
    lines.append("")
    return "\n".join(lines)


def render_native_beta_candidate_notes(
    *,
    tag: str,
    source_sha: str,
    build_run_url: str,
    gate_a_run_url: str | None,
    lane_verdicts: dict[str, str] | None,
    changelog_unreleased: str,
    assets: list[dict[str, Any]],
    smartscreen_note: str,
    artifact_source_sha: str | None = None,
    direct_verification: dict[str, str] | None = None,
) -> str:
    """Render the GitHub release body for a native-Windows beta-candidate.

    Separate from :func:`render_release_notes` (the WSL2-line renderer) on
    purpose -- the native-beta-candidate publish path
    (``scripts/release/publish_beta_candidate.py``) ships a different asset
    set (an installer whose name does not carry the old
    ``-windows-setup.exe`` suffix convention, plus per-component ``.ccpack``
    runtime packs, never a proof kit or tester-package zip) and a Gate A
    three-lane verdict, neither of which the WSL2-line renderer's manifest
    shape can express. Reusing that function's asset-suffix matching would
    either silently drop every native asset or require faking filenames to
    match a naming convention this line does not use -- so this is an
    additive function in the same module (not a duplicate implementation)
    that the publish script imports and calls.

    ``lane_verdicts`` maps Gate A lane name (``clean``, ``dirty``,
    ``download-only``) to its verdict string (expected ``PASS`` -- the
    caller is responsible for having already refused to publish on anything
    else; this function only renders what it is given).

    ``assets`` is a list of ``{"filename": str, "bytes": int, "sha256":
    str}`` dicts, one per uploaded release asset, in upload order.
    """

    if not source_sha:
        raise ValueError("source_sha is empty; cannot render tag target identity.")
    if not assets:
        raise ValueError("no release assets given; cannot render an asset table.")

    if direct_verification is not None:
        if not artifact_source_sha:
            raise ValueError("artifact_source_sha is required for direct-consumer notes.")
        consumer_mode = direct_verification.get("consumer_mode", "sandbox")
        if consumer_mode == "sandbox":
            legacy_sandbox_proofs = {
                "fresh_install",
                "failed_install_repair",
                "repair_preservation",
                "beta10_to_beta11_upgrade",
                "preservation",
                "runtime",
            }
            beta12_sandbox_proofs = {
                "fresh_install",
                "beta11_baseline_install",
                "beta11_to_beta12_interrupted_upgrade",
                "preservation",
                "runtime",
            }
            beta12_separate_sandbox_proofs = {
                "fresh_install",
                "beta11_baseline_install",
                "beta11_to_beta12_upgrade",
                "beta12_interrupted_install_repair",
                "preservation",
                "repair_preservation",
                "runtime",
            }
            legacy_expected = legacy_sandbox_proofs | {"consumer_mode"}
            beta12_expected = beta12_sandbox_proofs | {"consumer_mode"}
            beta12_separate_expected = beta12_separate_sandbox_proofs | {"consumer_mode"}
            beta12_advisory_expected = beta12_expected | {"caption_performance_advisory"}
            beta12_separate_advisory_expected = beta12_separate_expected | {
                "caption_performance_advisory"
            }
            if set(direct_verification) not in (
                legacy_sandbox_proofs,
                legacy_expected,
                beta12_sandbox_proofs,
                beta12_expected,
                beta12_advisory_expected,
                beta12_separate_sandbox_proofs,
                beta12_separate_expected,
                beta12_separate_advisory_expected,
            ):
                raise ValueError(
                    "direct_verification must contain the validated direct Sandbox proof summary"
                )
            beta12_upgrade = "beta11_to_beta12_interrupted_upgrade" in direct_verification
            beta12_separate_upgrade_repair = "beta11_to_beta12_upgrade" in direct_verification
            runtime_label = (
                "One-channel install-smoke observation"
                if "one-channel install-smoke" in direct_verification["runtime"].casefold()
                else "Three-channel Whistle/HLS/caption observation"
            )
            lines = [
                f"# CivicCast {tag.lstrip('v')} ({candidate_kind(tag).title()})",
                "",
                f"> **This is a {candidate_kind(tag)}, not a production release.**Direct Sandbox consumer checks "
                "passed as listed below. Gate A was not run for this route, and no human acceptance "
                "pass has been recorded.",
                "",
                "## Source identity",
                "",
                f"- Artifact producer commit: {artifact_source_sha}",
                f"- Tag target commit: {source_sha}",
                f"- Tag: {tag}",
                f"- Build run: {build_run_url}",
                "",
                "## Direct Sandbox consumer verification",
                "",
                f"- Fresh {tag.removeprefix('v')} install: {direct_verification['fresh_install']}",
                *(
                    [
                        f"- Beta 11 baseline install: {direct_verification['beta11_baseline_install']}",
                        f"- Beta 11 to Beta 12 interrupted-upgrade recovery: {direct_verification['beta11_to_beta12_interrupted_upgrade']}",
                        f"- Existing Beta 11 account, asset, and three schedules after upgrade: {direct_verification['preservation']}",
                    ]
                    if beta12_upgrade
                    else (
                        [
                            f"- Beta 11 baseline install: {direct_verification['beta11_baseline_install']}",
                            f"- Beta 11 to Beta 12 normal in-place upgrade: {direct_verification['beta11_to_beta12_upgrade']}",
                            f"- Separate Beta 12 D4 interrupted-install repair: {direct_verification['beta12_interrupted_install_repair']}",
                            f"- Existing Beta 11 account, asset, and three schedules after upgrade: {direct_verification['preservation']}",
                            f"- Existing account, asset, and three schedules after repair: {direct_verification['repair_preservation']}",
                        ]
                        if beta12_separate_upgrade_repair
                        else [
                            f"- Failed fresh-install repair: {direct_verification['failed_install_repair']}",
                            f"- Existing account data after repair: {direct_verification['repair_preservation']}",
                            f"- Beta 10 to {tag.removeprefix('v')} setup-only upgrade: {direct_verification['beta10_to_beta11_upgrade']}",
                            f"- Existing account, asset, and three schedules after upgrade: {direct_verification['preservation']}",
                        ]
                    )
                ),
                f"- {runtime_label}: {direct_verification['runtime']}",
                *(
                    [
                        f"- Caption-performance observations: {direct_verification['caption_performance_advisory']}"
                    ]
                    if "caption_performance_advisory" in direct_verification
                    else []
                ),
                "- Gate A workflow lanes: not run.",
                "- Download-only network route: not tested.",
            ]
        elif consumer_mode == "physical-host":
            expected_proofs = {"consumer_mode", "host_install", "host_preservation", "host_runtime"}
            if set(direct_verification) not in (
                expected_proofs,
                expected_proofs | {"caption_performance_advisory"},
            ):
                raise ValueError(
                    "direct_verification must contain the validated physical-host proof summary"
                )
            lines = [
                f"# CivicCast {tag.lstrip('v')} ({candidate_kind(tag).title()})",
                "",
                f"> **This is a {candidate_kind(tag)}.** The exact signed installer was checked on an existing "
                "physical host as listed below.",
                "",
                "## Source identity",
                "",
                f"- Artifact producer commit: {artifact_source_sha}",
                f"- Tag target commit: {source_sha}",
                f"- Tag: {tag}",
                f"- Build run: {build_run_url}",
                "",
                "## Physical-host consumer verification",
                "",
                f"- Exact {tag.removeprefix('v')} installer and installed payload: {direct_verification['host_install']}",
                f"- Existing host configuration and schema preservation: {direct_verification['host_preservation']}",
                f"- Three-channel host output: {direct_verification['host_runtime']}",
                *(
                    [
                        f"- Caption-performance observations: {direct_verification['caption_performance_advisory']}"
                    ]
                    if "caption_performance_advisory" in direct_verification
                    else []
                ),
                "- This was an in-place update on an existing host; clean Sandbox install and repair checks are separate.",
                "- The accepted development-station soak is reported separately from this install check.",
            ]
        else:
            raise ValueError(f"unsupported direct-consumer notes mode: {consumer_mode!r}")
    else:
        if gate_a_run_url is None or lane_verdicts is None:
            raise ValueError(
                "Gate A run URL and lane verdicts are required for workflow-backed notes."
            )
        if artifact_source_sha is not None and artifact_source_sha != source_sha:
            lines = [
                f"# CivicCast {tag.lstrip('v')} ({candidate_kind(tag).title()})",
                "",
                f"> **This is a {candidate_kind(tag)}, not a production release.**It has "
                "passed automated Gate A station-acceptance (clean install, "
                "cross-version upgrade, and download-only upgrade lanes) but has "
                "NOT had a human acceptance pass. Treat findings as expected; report "
                "them rather than assuming the release is broken.",
                "",
                "## Source identity",
                "",
                f"- Artifact producer commit: {artifact_source_sha}",
                f"- Tag target commit: {source_sha}",
                f"- Tag: {tag}",
                f"- Build run: {build_run_url}",
                f"- Gate A run: {gate_a_run_url}",
                "",
                "## Gate A verdict (all three lanes required PASS)",
                "",
                "| lane | verdict |",
                "| --- | --- |",
            ]
        else:
            lines = [
                f"# CivicCast {tag.lstrip('v')} ({candidate_kind(tag).title()})",
                "",
                f"> **This is a {candidate_kind(tag)}, not a production release.**It has "
                "passed automated Gate A station-acceptance (clean install, "
                "cross-version upgrade, and download-only upgrade lanes) but has "
                "NOT had a human acceptance pass. Treat findings as expected; report "
                "them rather than assuming the release is broken.",
                "",
                "## Source identity",
                "",
                f"- Commit: {source_sha}",
                f"- Tag: {tag}",
                f"- Build run: {build_run_url}",
                f"- Gate A run: {gate_a_run_url}",
                "",
                "## Gate A verdict (all three lanes required PASS)",
                "",
                "| lane | verdict |",
                "| --- | --- |",
            ]
        for lane in ("clean", "dirty", "download-only"):
            if lane in lane_verdicts:
                lines.append(f"| {lane} | {lane_verdicts[lane]} |")
    lines += [
        "",
        "## What changed",
        "",
        bound_changelog_section(changelog_unreleased) or "(no [Unreleased] CHANGELOG entry found)",
        "",
        "## Install / upgrade",
        "",
        "Use the release-matched `scripts/download_windows_release_artifacts.ps1` "
        "with `-Tag` and `-Version` set to this release and `-Install`. It "
        "downloads and verifies the signed `setup.exe`, runtime packs, "
        "multipart station-model bundle, checksums and sidecar, then runs setup "
        "with `/S`. Do not install from `setup.exe` alone. Package availability "
        "does not establish fresh-install, repair, upgrade-preservation or "
        "sustained-operation acceptance; follow the current owner-authorized "
        "handoff and INSTALL-WINDOWS.md.",
        "",
        "## SmartScreen note",
        "",
        smartscreen_note.strip(),
        "",
        "## Assets",
        "",
        "| asset | size | SHA-256 |",
        "| --- | --- | --- |",
    ]
    for asset in assets:
        size = asset.get("bytes")
        size_display = f"{int(size):,} bytes" if isinstance(size, int) else "?"
        lines.append(f"| {asset['filename']} | {size_display} | {asset['sha256']} |")
    lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--tag", required=True)
    args = parser.parse_args(argv)

    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    sys.stdout.write(render_release_notes(manifest, args.tag))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
