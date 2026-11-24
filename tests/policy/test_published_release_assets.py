# SPDX-License-Identifier: Apache-2.0
"""Guard the published-release asset-consistency check (gate-civiccast C2/M7).

Proves the check passes on the real v1.0.0-rc.8 asset set and still fails when
a required asset is missing. Pure logic, no network.
"""

from __future__ import annotations

import json

from scripts.policy.check_published_release_assets import (
    REQUIRED_RELEASE_ASSETS,
    find_asset_problems,
    find_candidate_problems,
    find_public_beta_problems,
    find_release_problems,
    main,
)

#: The exact 26 assets published for v1.0.0-rc.7 and v1.0.0-rc.8.
REAL_RC8_ASSETS = [
    "candidate-manual-receipt.json",
    "download_windows_release_artifacts.ps1",
    "native-app-payload.ccpack",
    "native-cuda-runtime.ccpack",
    "native-ffmpeg-runtime.ccpack",
    "native-ollama-runtime.ccpack.zip",
    "native-server-binaries.ccpack",
    "setup.exe",
    "setup.exe.sidecar.json",
    "SHA256SUMS.txt",
    "station-models.json",
    "station-models.zip.001",
    "station-models.zip.002",
    "station-models.zip.003",
    "station-models.zip.004",
    "station-models.zip.005",
    "station-models.zip.006",
    "station-models.zip.007",
    "station-models.zip.008",
    "station-models.zip.009",
    "station-models.zip.010",
    "station-models.zip.011",
    "station-models.zip.012",
    "USER-MANUAL.docx",
    "USER-MANUAL.pdf",
    "USER-MANUAL.render.json",
]


def test_real_rc8_asset_set_passes():
    assert len(REAL_RC8_ASSETS) == 26
    assert find_asset_problems(REAL_RC8_ASSETS) == []


def test_missing_setup_exe_is_flagged():
    assets = [a for a in REAL_RC8_ASSETS if a != "setup.exe"]
    assert any("setup.exe" in p for p in find_asset_problems(assets))


def test_missing_station_zip_part_is_flagged():
    assets = [a for a in REAL_RC8_ASSETS if a != "station-models.zip.006"]
    problems = find_asset_problems(assets)
    assert any("station-models.zip.NNN" in p and "006" in p for p in problems)


def test_missing_sha256sums_is_flagged():
    assets = [a for a in REAL_RC8_ASSETS if a != "SHA256SUMS.txt"]
    assert any("SHA256SUMS.txt" in p for p in find_asset_problems(assets))


def test_every_required_name_is_enforced():
    for required in REQUIRED_RELEASE_ASSETS:
        assets = [a for a in REAL_RC8_ASSETS if a != required]
        assert any(required in p for p in find_asset_problems(assets)), required


def test_orphan_sigstore_is_flagged():
    assets = [*REAL_RC8_ASSETS, "setup.exe.sigstore.json"]
    # setup.exe is present, so this attestation is not orphaned.
    assert not any("orphaned attestation" in p for p in find_asset_problems(assets))
    assets = [*REAL_RC8_ASSETS, "civiccast-1.0.0-rc9-release-artifacts-manifest.json.sigstore.json"]
    assert any("orphaned attestation" in p for p in find_asset_problems(assets))


def test_draft_release_is_not_accepted_as_published():
    payload = {"tagName": "v1.0.0-rc11", "isDraft": True, "assets": []}
    assert any("draft" in p for p in find_release_problems(payload, "v1.0.0-rc11"))


def test_release_tag_must_match_requested_tag():
    payload = {"tagName": "v1.0.0-rc9", "isDraft": False, "assets": []}
    assert any("tag" in p for p in find_release_problems(payload, "v1.0.0-rc11"))


def test_matching_nondraft_release_posture_passes():
    payload = {"tagName": "v1.0.0-rc11", "isDraft": False, "assets": []}
    assert find_release_problems(payload, "v1.0.0-rc11") == []


def test_candidate_posture_requires_matching_draft():
    matching_draft = {"tagName": "v1.0.0-rc11", "isDraft": True, "assets": []}
    public_release = {"tagName": "v1.0.0-rc11", "isDraft": False, "assets": []}
    assert find_candidate_problems(matching_draft, "v1.0.0-rc11") == []
    assert any("not a draft" in p for p in find_candidate_problems(public_release, "v1.0.0-rc11"))


def test_public_beta_is_an_explicit_cli_phase(monkeypatch):
    class Result:
        stdout = json.dumps(
            {
                "tagName": "v1.0.0-rc14",
                "isDraft": False,
                "isPrerelease": True,
                "assets": [{"name": name} for name in REAL_RC8_ASSETS],
            }
        )

    monkeypatch.setattr(
        "scripts.policy.check_published_release_assets.subprocess.run", lambda *a, **k: Result()
    )
    assert main(["check", "v1.0.0-rc14", "--public-beta"]) == 0


def test_public_beta_posture_requires_published_prerelease():
    public_beta = {
        "tagName": "v1.0.0-rc14",
        "isDraft": False,
        "isPrerelease": True,
        "assets": [],
    }
    draft = {**public_beta, "isDraft": True}
    final_release = {**public_beta, "isPrerelease": False}
    assert find_public_beta_problems(public_beta, "v1.0.0-rc14") == []
    assert any("draft" in p for p in find_public_beta_problems(draft, "v1.0.0-rc14"))
    assert any("prerelease" in p for p in find_public_beta_problems(final_release, "v1.0.0-rc14"))


def test_require_published_is_an_explicit_cli_phase(monkeypatch):
    class Result:
        stdout = json.dumps(
            {
                "tagName": "v1.0.0-rc11",
                "isDraft": True,
                "assets": [{"name": name} for name in REAL_RC8_ASSETS],
            }
        )

    monkeypatch.setattr(
        "scripts.policy.check_published_release_assets.subprocess.run", lambda *a, **k: Result()
    )
    assert main(["check", "v1.0.0-rc11"]) == 2  # phase selection is fail-closed
    assert main(["check", "v1.0.0-rc11", "--candidate"]) == 0
    assert main(["check", "v1.0.0-rc11", "--require-published"]) == 1
