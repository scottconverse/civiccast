#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Release-asset gate for an owner-held candidate or a published release.

Catches the class of release-engineering defect that shipped in v1.0.0-rc8
(gate-civiccast C1/C2): a required release asset silently missing, or a
``*.sigstore.json`` attestation left orphaned next to no base file.

The required names are the ones the live v1.0.0-rc.7 / v1.0.0-rc.8 releases
actually publish -- ``setup.exe`` plus its sidecar, ``SHA256SUMS.txt``, the
five ``native-*`` runtime/payload packs, the station bundle manifest and its
complete multipart archive, and the manual assets. The old WSL-era names
(``*-release-artifacts-manifest.json``, ``*-windows-setup.exe``) are gone.

Usage:
    python scripts/policy/check_published_release_assets.py <tag> --candidate
    python scripts/policy/check_published_release_assets.py <tag> --public-beta
    python scripts/policy/check_published_release_assets.py <tag> --require-published

Reads the live release via ``gh release view <tag>`` and exits
non-zero (printing every problem) if the asset set is incomplete or inconsistent.
The phase flag is mandatory. Candidate mode requires an owner-held draft;
public-beta mode requires a published prerelease; published mode accepts any
non-draft release. The rule logic is pure and unit-tested; only ``main`` touches
the network.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys

#: Every asset a release must publish, by exact name. This is the single place
#: the real release name list lives; the multipart station archive is handled
#: separately because its part count varies.
REQUIRED_RELEASE_ASSETS: tuple[str, ...] = (
    "setup.exe",
    "setup.exe.sidecar.json",
    "SHA256SUMS.txt",
    "native-app-payload.ccpack",
    "native-cuda-runtime.ccpack",
    "native-ffmpeg-runtime.ccpack",
    "native-ollama-runtime.ccpack.zip",
    "native-server-binaries.ccpack",
    "station-models.json",
    "USER-MANUAL.pdf",
    "USER-MANUAL.docx",
    "USER-MANUAL.render.json",
)

#: ``station-models.zip.001`` .. ``station-models.zip.NNN`` (three-digit part).
STATION_MODELS_PART_PATTERN = re.compile(r"^station-models\.zip\.(\d{3})$")


def find_asset_problems(asset_names: list[str]) -> list[str]:
    """Return a list of human-readable problems with the asset name set.

    Empty list == the release is complete and self-consistent.
    """
    names = set(asset_names)
    problems: list[str] = []

    # 1. Every required asset, by exact name, must be present.
    for required in REQUIRED_RELEASE_ASSETS:
        if required not in names:
            problems.append(f"missing required release asset '{required}'")

    # 2. The multipart station archive must be a complete series: 001..N with
    #    no gap and no duplicate index.
    part_indexes = sorted(
        int(match.group(1))
        for name in names
        if (match := STATION_MODELS_PART_PATTERN.match(name))
    )
    if not part_indexes:
        problems.append(
            "missing the multipart station archive 'station-models.zip.001'..NNN"
        )
    else:
        expected = list(range(1, part_indexes[-1] + 1))
        if part_indexes != expected:
            missing = sorted(set(expected) - set(part_indexes))
            duplicate = sorted(
                {i for i in part_indexes if part_indexes.count(i) > 1}
            )
            detail = []
            if missing:
                detail.append(
                    "missing part(s) " + ", ".join(f"{i:03d}" for i in missing)
                )
            if duplicate:
                detail.append(
                    "duplicate part(s) " + ", ".join(f"{i:03d}" for i in duplicate)
                )
            problems.append(
                "station-models.zip.NNN series is incomplete: " + "; ".join(detail)
            )

    # 3. No orphaned attestation: every '<x>.sigstore.json' needs '<x>' present.
    for n in sorted(names):
        if n.endswith(".sigstore.json"):
            base = n[: -len(".sigstore.json")]
            if base not in names:
                problems.append(
                    f"orphaned attestation '{n}' has no base artifact '{base}' "
                    "(stale/dangling predicate — a verifier would fail)"
                )

    return problems


def find_release_problems(payload: dict[str, object], expected_tag: str) -> list[str]:
    """Return publication-posture problems for a GitHub Release payload."""
    problems: list[str] = []
    actual_tag = payload.get("tagName")
    if actual_tag != expected_tag:
        problems.append(f"release tag mismatch: expected {expected_tag!r}, got {actual_tag!r}")
    if payload.get("isDraft") is not False:
        problems.append("release is still a draft and is not publicly published")
    return problems


def find_candidate_problems(payload: dict[str, object], expected_tag: str) -> list[str]:
    """Return owner-held candidate posture problems for a GitHub Release payload."""
    problems: list[str] = []
    actual_tag = payload.get("tagName")
    if actual_tag != expected_tag:
        problems.append(f"release tag mismatch: expected {expected_tag!r}, got {actual_tag!r}")
    if payload.get("isDraft") is not True:
        problems.append("candidate release is not a draft; owner-held posture was lost")
    return problems


def find_public_beta_problems(payload: dict[str, object], expected_tag: str) -> list[str]:
    """Return authorized public-prerelease posture problems."""
    problems = find_release_problems(payload, expected_tag)
    if payload.get("isPrerelease") is not True:
        problems.append("public beta is not marked as a prerelease")
    return problems


def main(argv: list[str]) -> int:
    phases = {"--candidate", "--public-beta", "--require-published"}
    if len(argv) != 3 or argv[2] not in phases:
        print(
            "usage: check_published_release_assets.py <tag> "
            "(--candidate | --public-beta | --require-published)",
            file=sys.stderr,
        )
        return 2
    tag = argv[1]
    requested_phase = argv[2]
    out = subprocess.run(
        [
            "gh",
            "release",
            "view",
            tag,
            "--json",
            "assets,isDraft,isPrerelease,tagName,url",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(out.stdout)
    assets = payload.get("assets", [])
    names = [a["name"] for a in assets]
    if requested_phase == "--candidate":
        phase = "candidate"
        posture_problems = find_candidate_problems(payload, tag)
    elif requested_phase == "--public-beta":
        phase = "public-beta"
        posture_problems = find_public_beta_problems(payload, tag)
    else:
        phase = "published"
        posture_problems = find_release_problems(payload, tag)
    problems = [*posture_problems, *find_asset_problems(names)]
    if problems:
        print(f"check_published_release_assets: FAIL ({phase}) for {tag} ({len(names)} assets):")
        for p in problems:
            print(f"  - {p}")
        return 1
    print(
        f"check_published_release_assets: PASS ({phase}) for {tag} "
        f"({len(names)} assets, self-consistent)."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
