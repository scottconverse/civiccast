# SPDX-License-Identifier: Apache-2.0
# Copyright (c) The CivicCast Authors
"""Tests for scripts/render_release_notes.py.

Guards the root-cause fix for the rc.2 release-body defect: the commit and
every checksum in the release notes must come from the manifest, never be
hand-typed, and must never silently omit or duplicate an asset.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import pytest
from render_release_notes import candidate_kind, render_release_notes

_MANIFEST = {
    "source_state": {"head": "deadbeef" * 5},
    "artifacts": [
        {
            "filename": "civiccast-0.1.0-rc1-windows-setup.exe",
            "sha256": "aaa1",
        },
        {
            "filename": "civiccast-0.1.0-rc1-windows-setup.exe.sidecar.json",
            "sha256": "bbb2",
        },
        {
            "filename": "civiccast-0.1.0-rc1-clean-windows-proof-kit.zip",
            "sha256": "ccc3",
        },
        {
            "filename": "civiccast-0.1.0-rc1-release-artifacts-manifest.json",
            "sha256": "ddd4",
        },
        {
            "filename": "civiccast-0.1.0-rc1-windows-tester-package.zip",
            "sha256": "eee5",
        },
        # Not part of the release-body checksum list -- must be excluded.
        {
            "filename": "wheelhouse/WHEELHOUSE-MANIFEST.json",
            "sha256": "shouldnotappear",
        },
    ],
}


def test_render_release_notes_includes_commit_and_tag() -> None:
    body = render_release_notes(_MANIFEST, "v0.1.0-rc1")
    assert f"Commit: {'deadbeef' * 5}" in body
    assert "Tag: v0.1.0-rc1" in body


def test_render_release_notes_includes_every_body_asset_checksum() -> None:
    body = render_release_notes(_MANIFEST, "v0.1.0-rc1")
    assert "civiccast-0.1.0-rc1-windows-setup.exe: aaa1" in body
    assert "civiccast-0.1.0-rc1-windows-setup.exe.sidecar.json: bbb2" in body
    assert "civiccast-0.1.0-rc1-clean-windows-proof-kit.zip: ccc3" in body
    assert "civiccast-0.1.0-rc1-release-artifacts-manifest.json: ddd4" in body
    assert "civiccast-0.1.0-rc1-windows-tester-package.zip: eee5" in body


def test_render_release_notes_excludes_non_body_assets() -> None:
    body = render_release_notes(_MANIFEST, "v0.1.0-rc1")
    assert "shouldnotappear" not in body
    assert "WHEELHOUSE-MANIFEST" not in body


def test_render_release_notes_references_matching_verification_doc() -> None:
    body = render_release_notes(_MANIFEST, "v0.1.0-rc1")
    assert "docs/releases/v0.1.0-rc1-verification.md" in body


def test_render_release_notes_requires_commit() -> None:
    manifest = {"source_state": {"head": ""}, "artifacts": _MANIFEST["artifacts"]}
    with pytest.raises(ValueError, match=r"source_state\.head"):
        render_release_notes(manifest, "v0.1.0-rc1")


def test_render_release_notes_requires_at_least_one_asset() -> None:
    manifest = {"source_state": {"head": "a" * 40}, "artifacts": []}
    with pytest.raises(ValueError, match="no release-body assets"):
        render_release_notes(manifest, "v0.1.0-rc1")


@pytest.mark.parametrize(
    ("tag", "kind"),
    [
        ("v1.0.0-rc.4", "release candidate"),
        ("v1.0.0-beta.20", "beta candidate"),
        ("v1.0.0-rc4", "beta candidate"),
        ("v1.0.0", "beta candidate"),
    ],
)
def test_candidate_kind_names_rc_tags_release_candidates(tag: str, kind: str) -> None:
    assert candidate_kind(tag) == kind
