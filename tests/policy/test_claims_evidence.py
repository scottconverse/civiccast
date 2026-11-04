# SPDX-License-Identifier: Apache-2.0
# Copyright (c) The CivicCast Authors
"""D8 falsification suite for the claims-evidence verifier
(scripts/policy/check_claims_evidence.py, spec-claims-evidence-rule.md).

Every numbered D8 group is represented, each expected-red case as its own
test function, plus the AC1-AC4 positives. All fixtures are local tmp_path
git repos — no Docker, no network.

2026-08-07: D8 groups 9/10/11/14/15, the D5 integration tests, and the
ws3r2-002/ws3r3-001/ws3r3-002/ws3r4-006/ws3r4-007 suites were removed along
with the external-evidence/authority-record verifier code they exercised
(see scripts/policy/check_claims_evidence.py's module docstring and
CHANGELOG.md). D1/D2 (marker scan, blob drift) and D3/D4 (same-run
producer/test evidence) coverage — groups 1-8, 12, 13 (minus the removed
authority-format case) — is unaffected and stays in force below.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from scripts.policy import check_claims_evidence as cce

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "policy" / "check_claims_evidence.py"


# ---------------------------------------------------------------------------
# Shared git/fixture helpers
# ---------------------------------------------------------------------------


def _git(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    )


def init_git_repo(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    _git(["init", "-q", "-b", "main"], cwd=path)
    _git(["config", "user.email", "test@example.com"], cwd=path)
    _git(["config", "user.name", "Test"], cwd=path)


def commit_all(path: Path, message: str) -> str:
    _git(["add", "-A"], cwd=path)
    _git(["commit", "-q", "--allow-empty", "-m", message], cwd=path)
    return _git(["rev-parse", "HEAD"], cwd=path).stdout.strip()


def blob(root: Path, rel_path: str) -> str:
    return cce.git_hash_object(root, rel_path)


def junit_xml(cases: list[tuple[str, str, str]]) -> str:
    """cases: (classname, name, status) with status in passed/skipped/failed."""
    parts = ['<?xml version="1.0" encoding="utf-8"?>', '<testsuites><testsuite name="t">']
    for classname, name, status in cases:
        if status == "passed":
            parts.append(f'<testcase classname="{classname}" name="{name}" time="0.01"/>')
        elif status == "skipped":
            parts.append(
                f'<testcase classname="{classname}" name="{name}" time="0.01"><skipped/></testcase>'
            )
        else:
            parts.append(
                f'<testcase classname="{classname}" name="{name}" time="0.01">'
                '<failure message="x">boom</failure></testcase>'
            )
    parts.append("</testsuite></testsuites>")
    return "\n".join(parts)


def n_passed_cases(prefix: str, count: int) -> list[tuple[str, str, str]]:
    return [(f"{prefix}.filler", f"case_{i}", "passed") for i in range(count)]


SOURCE_SHA_A = "a" * 40
SOURCE_SHA_B = "b" * 40

# CC-WS3-006 (round-4 audit): a single default (role, path, blob) triple used
# by tests that exercise fields OTHER than 'inputs' itself and therefore just
# need `valid_evidence_body`'s body and `validate_evidence_record_body`'s
# `expected_inputs` argument to agree with each other — not a claim about any
# real registered blob (see `make_bound_claim_and_control`/`evidence_inputs_for`
# below for the real, git-hash-object-derived fixtures the round-4 auditor
# required for the *positive*-path and input-binding tests specifically).
DEFAULT_INPUT_ROLE = "code"
DEFAULT_INPUT_PATH = "code.py"
DEFAULT_INPUT_BLOB = "c" * 40
DEFAULT_EXPECTED_INPUTS: list[tuple[str, str, str]] = [
    (DEFAULT_INPUT_ROLE, DEFAULT_INPUT_PATH, DEFAULT_INPUT_BLOB)
]


# ---------------------------------------------------------------------------
# Repo-root fixture for D1/D2/D3-level (registry, marker, blob-drift) tests
# ---------------------------------------------------------------------------


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    (root / "docs.md").write_text("Nothing strong-claimy here.\n", encoding="utf-8")
    (root / "governed.py").write_text('"""Nothing strong-claimy here."""\n', encoding="utf-8")
    (root / "code.py").write_text("# code\n", encoding="utf-8")
    (root / "test_x.py").write_text("# test\n", encoding="utf-8")
    (root / "verifier.py").write_text("# verifier\n", encoding="utf-8")
    (root / "schema.json").write_text("{}\n", encoding="utf-8")
    (root / "workflow.yml").write_text("name: w\n", encoding="utf-8")
    (root / "contract.yaml").write_text("schema_version: 1\n", encoding="utf-8")
    (root / "trust.yaml").write_text("owner_accepted: false\n", encoding="utf-8")
    (root / "generator.py").write_text("# gen\n", encoding="utf-8")
    (root / "fixture1.json").write_text("{}\n", encoding="utf-8")
    return root


def full_inputs(root: Path, prose_path: str = "docs.md") -> dict:
    return {
        "prose": {"path": prose_path, "blob": blob(root, prose_path)},
        "code": {"path": "code.py", "blob": blob(root, "code.py")},
        "test": {
            "path": "test_x.py",
            "blob": blob(root, "test_x.py"),
            "node_id": "test_x.py::test_thing",
        },
        "verifier": [
            {"path": "verifier.py", "blob": blob(root, "verifier.py")},
            {"path": "schema.json", "blob": blob(root, "schema.json")},
        ],
        "workflow": {"path": "workflow.yml", "blob": blob(root, "workflow.yml")},
        "workflow_contract": {"path": "contract.yaml", "blob": blob(root, "contract.yaml")},
        "trust_root": {"path": "trust.yaml", "blob": blob(root, "trust.yaml")},
        "generator": {"path": "generator.py", "blob": blob(root, "generator.py")},
        "fixtures": [{"path": "fixture1.json", "blob": blob(root, "fixture1.json")}],
    }


def make_entry_dict(
    root: Path,
    claim_id: str = "demo-claim",
    where_file: str = "docs.md",
    where_anchor: str = "Nothing strong-claimy here.",
    resolution: str = "same_run",
    controls: list | None = None,
) -> dict:
    return {
        "id": claim_id,
        "claim": "a demo claim",
        "where": {"file": where_file, "anchor": where_anchor},
        "resolution": resolution,
        # CC-WS3-003: inputs.prose.path must equal where.file — every
        # caller's where_file (default or overridden) drives the prose path.
        "inputs": full_inputs(root, prose_path=where_file),
        "controls": controls or [],
    }


def make_registry_dict(root: Path, entries: list[dict], governed: list[dict] | None = None) -> dict:
    return {
        "schema_version": 1,
        "governed_doc_set": governed
        if governed is not None
        else [
            {"path": "governed.py", "format": "python", "enforced": True},
            {"path": "docs.md", "format": "markdown", "enforced": True},
        ],
        "strong_claim_tokens": list(cce.STRONG_TOKENS_DEFAULT),
        "entries": entries,
    }


def write_yaml(path: Path, data: dict) -> Path:
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    return path


# ===========================================================================
# D8 group 1: seeded false "implemented" claim
# ===========================================================================


def test_group1_seeded_false_claim_test_did_not_pass(repo: Path) -> None:
    entry = make_entry_dict(repo)
    registry = cce.load_registry(
        write_yaml(repo / "claims.yaml", make_registry_dict(repo, [entry]))
    )
    evidence = {
        "test": cce.ProducerEvidence(
            junit_cases=[cce.JunitCase("test_x", "test_thing", "failed")],
            meta={"sha": SOURCE_SHA_A},
        )
    }
    violations = cce.claim_test_violations(registry, evidence)
    assert violations, "a false 'implemented' claim whose test failed must be red"
    assert "did not PASS" in violations[0]


# ===========================================================================
# D8 group 2: wrong-SHA junit; junit-meta from another run; missing/malformed meta
# ===========================================================================


def _base_contract() -> dict:
    return {
        "workflow_job_inventory": ["test", "verifier"],
        "expected_producers": {
            "test": {
                "junit_artifact": "test-junit",
                "junit_file": "junit.xml",
                "meta_artifact": "test-meta",
                "meta_file": "test-meta.json",
                "requires_checkout_attestation": True,
                "junit_collection_floor": 1,
            }
        },
        "verifier_job": "verifier",
    }


def _write_producer_artifacts(
    artifacts_dir: Path, meta: dict, cases: list[tuple[str, str, str]]
) -> None:
    junit_dir = artifacts_dir / "test-junit"
    meta_dir = artifacts_dir / "test-meta"
    junit_dir.mkdir(parents=True)
    meta_dir.mkdir(parents=True)
    (junit_dir / "junit.xml").write_text(junit_xml(cases), encoding="utf-8")
    (meta_dir / "test-meta.json").write_text(json.dumps(meta), encoding="utf-8")


def test_group2_wrong_sha_junit(tmp_path: Path) -> None:
    artifacts = tmp_path / "artifacts"
    _write_producer_artifacts(
        artifacts, {"sha": SOURCE_SHA_B, "run_id": "1"}, n_passed_cases("t", 1)
    )
    violations, _ = cce.producer_evidence_violations(
        _base_contract(), artifacts, {"test": "success"}, SOURCE_SHA_A
    )
    assert any("!= resolved source SHA" in v for v in violations)


def test_group2_junit_meta_from_another_run(tmp_path: Path) -> None:
    artifacts = tmp_path / "artifacts"
    _write_producer_artifacts(
        artifacts, {"sha": SOURCE_SHA_A, "run_id": "999"}, n_passed_cases("t", 1)
    )
    violations, _ = cce.producer_evidence_violations(
        _base_contract(), artifacts, {"test": "success"}, SOURCE_SHA_A, expected_run_id="123"
    )
    assert any("ANOTHER run" in v for v in violations)


def test_group2_missing_junit_meta(tmp_path: Path) -> None:
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    violations, _ = cce.producer_evidence_violations(
        _base_contract(), artifacts, {"test": "success"}, SOURCE_SHA_A
    )
    assert any("junit artifact" in v and "missing" in v for v in violations)
    assert any("meta artifact" in v and "missing" in v for v in violations)


def test_group2_malformed_junit_meta(tmp_path: Path) -> None:
    artifacts = tmp_path / "artifacts"
    junit_dir = artifacts / "test-junit"
    meta_dir = artifacts / "test-meta"
    junit_dir.mkdir(parents=True)
    meta_dir.mkdir(parents=True)
    (junit_dir / "junit.xml").write_text(junit_xml(n_passed_cases("t", 1)), encoding="utf-8")
    (meta_dir / "test-meta.json").write_text("{not json", encoding="utf-8")
    violations, _ = cce.producer_evidence_violations(
        _base_contract(), artifacts, {"test": "success"}, SOURCE_SHA_A
    )
    assert any("malformed meta JSON" in v for v in violations)


# ===========================================================================
# D8 group 3: mutated input file (blob drift) for EACH typed role in turn
# ===========================================================================


@pytest.mark.parametrize(
    "role, rel_path",
    [
        ("prose", "docs.md"),
        ("code", "code.py"),
        ("test", "test_x.py"),
        ("workflow", "workflow.yml"),
        ("workflow_contract", "contract.yaml"),
        ("trust_root", "trust.yaml"),
        ("generator", "generator.py"),
    ],
)
def test_group3_blob_drift_single_role_files(repo: Path, role: str, rel_path: str) -> None:
    entry = make_entry_dict(repo)
    registry = cce.load_registry(
        write_yaml(repo / "claims.yaml", make_registry_dict(repo, [entry]))
    )
    (repo / rel_path).write_text("MUTATED CONTENT\n", encoding="utf-8")
    violations = cce.blob_drift_violations(repo, registry)
    assert any(f"input role {role} " in v and "blob drift" in v for v in violations), violations


def test_group3_blob_drift_verifier_script(repo: Path) -> None:
    entry = make_entry_dict(repo)
    registry = cce.load_registry(
        write_yaml(repo / "claims.yaml", make_registry_dict(repo, [entry]))
    )
    (repo / "verifier.py").write_text("# MUTATED verifier\n", encoding="utf-8")
    violations = cce.blob_drift_violations(repo, registry)
    assert any("verifier[0]" in v and "blob drift" in v for v in violations), violations


def test_group3_blob_drift_registry_schema(repo: Path) -> None:
    entry = make_entry_dict(repo)
    registry = cce.load_registry(
        write_yaml(repo / "claims.yaml", make_registry_dict(repo, [entry]))
    )
    (repo / "schema.json").write_text('{"mutated": true}\n', encoding="utf-8")
    violations = cce.blob_drift_violations(repo, registry)
    assert any("verifier[1]" in v and "blob drift" in v for v in violations), violations


def test_group3_blob_drift_fixture(repo: Path) -> None:
    entry = make_entry_dict(repo)
    registry = cce.load_registry(
        write_yaml(repo / "claims.yaml", make_registry_dict(repo, [entry]))
    )
    (repo / "fixture1.json").write_text('{"mutated": 1}\n', encoding="utf-8")
    violations = cce.blob_drift_violations(repo, registry)
    assert any("fixtures[0]" in v and "blob drift" in v for v in violations), violations


# ===========================================================================
# D8 group 4: skipped test node presented as proof; test mutated after junit run
# ===========================================================================


def test_group4_skipped_test_node_presented_as_proof(repo: Path) -> None:
    entry = make_entry_dict(repo)
    registry = cce.load_registry(
        write_yaml(repo / "claims.yaml", make_registry_dict(repo, [entry]))
    )
    evidence = {
        "test": cce.ProducerEvidence(
            junit_cases=[cce.JunitCase("test_x", "test_thing", "skipped")],
            meta={"sha": SOURCE_SHA_A},
        )
    }
    violations = cce.claim_test_violations(registry, evidence)
    assert any("did not PASS" in v and "skipped" in v for v in violations)


def test_group4_test_mutated_after_its_junit_run(repo: Path) -> None:
    # junit shows a pass, but the test file's committed blob no longer
    # matches what's on disk today — the proof no longer binds to current code.
    entry = make_entry_dict(repo)
    registry = cce.load_registry(
        write_yaml(repo / "claims.yaml", make_registry_dict(repo, [entry]))
    )
    (repo / "test_x.py").write_text(
        "# mutated after the junit run captured a pass\n", encoding="utf-8"
    )
    violations = cce.blob_drift_violations(repo, registry)
    assert any("input role test " in v and "blob drift" in v for v in violations)


# ===========================================================================
# D8 group 5: unmarked token; stale anchor; duplicate claim ID; malformed registry
# ===========================================================================


def test_group5_unmarked_strong_token_in_governed_doc(repo: Path) -> None:
    (repo / "governed.py").write_text(
        '"""This capability is fully implemented."""\n', encoding="utf-8"
    )
    registry = cce.load_registry(write_yaml(repo / "claims.yaml", make_registry_dict(repo, [])))
    violations = cce.scan_marker_violations(repo, registry)
    assert any("unmarked strong-claim token" in v for v in violations)


def test_group5_marked_strong_token_passes(repo: Path) -> None:
    entry = make_entry_dict(repo, where_file="governed.py", where_anchor="is fully implemented")
    (repo / "governed.py").write_text(
        '"""This capability is fully implemented. # claim:demo-claim"""\n', encoding="utf-8"
    )
    registry = cce.load_registry(
        write_yaml(repo / "claims.yaml", make_registry_dict(repo, [entry]))
    )
    violations = cce.scan_marker_violations(repo, registry)
    assert violations == []


def test_group5_stale_where_anchor(repo: Path) -> None:
    entry = make_entry_dict(repo, where_file="docs.md", where_anchor="an anchor that will vanish")
    registry = cce.load_registry(
        write_yaml(repo / "claims.yaml", make_registry_dict(repo, [entry]))
    )
    violations = cce.scan_marker_violations(repo, registry)
    assert any("stale anchor" in v for v in violations)


def test_group5_duplicate_claim_id(repo: Path) -> None:
    entry1 = make_entry_dict(repo, claim_id="dup-claim")
    entry2 = make_entry_dict(repo, claim_id="dup-claim")
    with pytest.raises(cce.MalformedRegistryError, match="duplicate claim id"):
        cce.load_registry(
            write_yaml(repo / "claims.yaml", make_registry_dict(repo, [entry1, entry2]))
        )


def test_group5_malformed_registry_missing_role(repo: Path) -> None:
    entry = make_entry_dict(repo)
    del entry["inputs"]["generator"]
    with pytest.raises(cce.MalformedRegistryError, match="missing mandatory input role"):
        cce.load_registry(write_yaml(repo / "claims.yaml", make_registry_dict(repo, [entry])))


def test_group5_malformed_registry_not_a_mapping(tmp_path: Path) -> None:
    path = tmp_path / "claims.yaml"
    path.write_text("- just\n- a\n- list\n", encoding="utf-8")
    with pytest.raises(cce.MalformedRegistryError, match="mapping"):
        cce.load_registry(path)


# ===========================================================================
# D8 group 6: synthetic merge SHA (GITHUB_SHA) substituted for head SHA
# ===========================================================================


def test_group6_synthetic_merge_sha_substituted_for_head(tmp_path: Path) -> None:
    repo_dir = tmp_path / "checkout"
    init_git_repo(repo_dir)
    (repo_dir / "f.txt").write_text("x\n", encoding="utf-8")
    actual_head = commit_all(repo_dir, "commit")  # this is what got checked out
    identity = cce.SourceIdentity(
        event_name="pull_request", pr_head_sha=SOURCE_SHA_A, github_sha="deadbeef" * 5
    )
    source_sha = cce.resolve_source_sha(identity)
    assert source_sha == SOURCE_SHA_A
    with pytest.raises(cce.ViolationError, match="synthetic merge commit"):
        cce.assert_checkout_matches_source(repo_dir, source_sha)
    assert actual_head != source_sha


def test_group6_missing_pr_head_sha_is_cannot_check() -> None:
    identity = cce.SourceIdentity(event_name="pull_request", pr_head_sha=None, github_sha="x" * 40)
    with pytest.raises(cce.CannotCheckError):
        cce.resolve_source_sha(identity)


# ===========================================================================
# D8 group 7: job-inventory drift; verifier in expected_producers; needs drift
# ===========================================================================


def test_group7_live_job_unlisted_in_contract() -> None:
    jobs = {"test": {}, "verifier": {"needs": ["test"]}, "surprise": {}}
    contract = {
        "workflow_job_inventory": ["test", "verifier"],
        "expected_producers": {"test": {}},
        "verifier_job": "verifier",
    }
    violations = cce.workflow_contract_violations(jobs, contract)
    assert any("unlisted in contract" in v for v in violations)


def test_group7_listed_job_dead() -> None:
    jobs = {"test": {}, "verifier": {"needs": ["test"]}}
    contract = {
        "workflow_job_inventory": ["test", "verifier", "ghost"],
        "expected_producers": {"test": {}},
        "verifier_job": "verifier",
    }
    violations = cce.workflow_contract_violations(jobs, contract)
    assert any("lists dead job" in v for v in violations)


def test_group7_verifier_listed_in_expected_producers() -> None:
    jobs = {"test": {}, "verifier": {"needs": ["test", "verifier"]}}
    contract = {
        "workflow_job_inventory": ["test", "verifier"],
        "expected_producers": {"test": {}, "verifier": {}},
        "verifier_job": "verifier",
    }
    violations = cce.workflow_contract_violations(jobs, contract)
    assert any("must never appear in expected_producers" in v for v in violations)


def test_group7_needs_diverging_from_producer_keys() -> None:
    jobs = {"test": {}, "engine": {}, "verifier": {"needs": ["test"]}}
    contract = {
        "workflow_job_inventory": ["test", "engine", "verifier"],
        "expected_producers": {"test": {}, "engine": {}},
        "verifier_job": "verifier",
    }
    violations = cce.workflow_contract_violations(jobs, contract)
    assert any("must be exactly equal" in v for v in violations)


# ===========================================================================
# D8 group 8: fail-closed producer result; missing junit/meta artifact
# ===========================================================================


def test_group8_producer_skipped_verifier_still_runs_red(tmp_path: Path) -> None:
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    violations, _ = cce.producer_evidence_violations(
        _base_contract(), artifacts, {"test": "skipped"}, SOURCE_SHA_A
    )
    assert any("not 'success'" in v for v in violations)


def test_group8_producer_failed_verifier_still_runs_red(tmp_path: Path) -> None:
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    violations, _ = cce.producer_evidence_violations(
        _base_contract(), artifacts, {"test": "failure"}, SOURCE_SHA_A
    )
    assert any("not 'success'" in v for v in violations)


def test_group8_producer_missing_junit_only(tmp_path: Path) -> None:
    artifacts = tmp_path / "artifacts"
    meta_dir = artifacts / "test-meta"
    meta_dir.mkdir(parents=True)
    (meta_dir / "test-meta.json").write_text(json.dumps({"sha": SOURCE_SHA_A}), encoding="utf-8")
    violations, _ = cce.producer_evidence_violations(
        _base_contract(), artifacts, {"test": "success"}, SOURCE_SHA_A
    )
    assert any("junit artifact" in v and "missing" in v for v in violations)


def test_group8_producer_missing_meta_only(tmp_path: Path) -> None:
    artifacts = tmp_path / "artifacts"
    junit_dir = artifacts / "test-junit"
    junit_dir.mkdir(parents=True)
    (junit_dir / "junit.xml").write_text(junit_xml(n_passed_cases("t", 1)), encoding="utf-8")
    violations, _ = cce.producer_evidence_violations(
        _base_contract(), artifacts, {"test": "success"}, SOURCE_SHA_A
    )
    assert any("meta artifact" in v and "missing" in v for v in violations)


# ===========================================================================
# D5/audit-control fixture builders and D8 groups 9-11 (evidence-record
# path/create-only/blob problems; signer pinning; tampered/unaccepted trust
# root) lived here. Removed 2026-08-07 with the external-evidence /
# authority-record verifier code -- see this file's module docstring.
# ===========================================================================


@pytest.mark.parametrize(
    "field", ["run_id", "run_date", "executed_at", "result", "commit_sha", "source_sha"]
)
def test_group12_self_reference_probe_banned_field(repo: Path, field: str) -> None:
    entry = make_entry_dict(repo)
    entry[field] = "2026-07-17"
    with pytest.raises(cce.MalformedRegistryError, match="banned result/run field"):
        cce.load_registry(write_yaml(repo / "claims.yaml", make_registry_dict(repo, [entry])))


# ===========================================================================
# D8 group 13: empty-but-green producer; positive-below-floor; duplicate
# control id; authority format mismatch
# ===========================================================================


def test_group13_successful_producer_collects_zero_tests(tmp_path: Path) -> None:
    artifacts = tmp_path / "artifacts"
    _write_producer_artifacts(artifacts, {"sha": SOURCE_SHA_A, "run_id": "1"}, [])
    violations, _ = cce.producer_evidence_violations(
        _base_contract(), artifacts, {"test": "success"}, SOURCE_SHA_A
    )
    assert any("below its per-producer floor" in v for v in violations)


def test_group13_positive_below_floor_while_another_exceeds(tmp_path: Path) -> None:
    contract = {
        "workflow_job_inventory": ["test", "engine", "verifier"],
        "expected_producers": {
            "test": {
                "junit_artifact": "test-junit",
                "junit_file": "junit.xml",
                "meta_artifact": "test-meta",
                "meta_file": "test-meta.json",
                "requires_checkout_attestation": True,
                "junit_collection_floor": 5,
            },
            "engine": {
                "junit_artifact": "engine-junit",
                "junit_file": "engine-junit.xml",
                "meta_artifact": "engine-meta",
                "meta_file": "engine-meta.json",
                "requires_checkout_attestation": True,
                "junit_collection_floor": 1,
            },
        },
        "verifier_job": "verifier",
    }
    artifacts = tmp_path / "artifacts"
    (artifacts / "test-junit").mkdir(parents=True)
    (artifacts / "test-meta").mkdir(parents=True)
    (artifacts / "test-junit" / "junit.xml").write_text(
        junit_xml(n_passed_cases("t", 2)), encoding="utf-8"
    )
    (artifacts / "test-meta" / "test-meta.json").write_text(
        json.dumps({"sha": SOURCE_SHA_A}), encoding="utf-8"
    )
    (artifacts / "engine-junit").mkdir(parents=True)
    (artifacts / "engine-meta").mkdir(parents=True)
    (artifacts / "engine-junit" / "engine-junit.xml").write_text(
        junit_xml(n_passed_cases("e", 20)), encoding="utf-8"
    )
    (artifacts / "engine-meta" / "engine-meta.json").write_text(
        json.dumps({"sha": SOURCE_SHA_A}), encoding="utf-8"
    )
    violations, _ = cce.producer_evidence_violations(
        contract, artifacts, {"test": "success", "engine": "success"}, SOURCE_SHA_A
    )
    assert any("producer 'test'" in v and "below its per-producer floor" in v for v in violations)
    assert not any(
        "producer 'engine'" in v and "below its per-producer floor" in v for v in violations
    )


def test_group13_duplicate_control_id_within_one_claim(repo: Path) -> None:
    control = {"id": "same-id", "command": "n/a", "expected_red_when": "n/a", "ci_safe": False}
    entry = make_entry_dict(repo, controls=[dict(control), dict(control)])
    with pytest.raises(cce.MalformedRegistryError, match="duplicate control id"):
        cce.load_registry(write_yaml(repo / "claims.yaml", make_registry_dict(repo, [entry])))


# ===========================================================================
# D8 groups 9-11, 14-15, and the D5 positive-path / real-SSH-integration
# tests formerly lived here (evidence-record canonical path, signer
# pinning, tampered/unaccepted trust root, governance-binding + slug
# grammar, introducing-commit/A-B-A/retroactive authority, real SSH
# verification). Removed 2026-08-07 along with the external-evidence /
# authority-record verifier code they exercised -- see this file's module
# docstring, scripts/policy/check_claims_evidence.py's module docstring,
# and CHANGELOG.md. D8 group 12 (self-reference probe) and group 13
# (minus its removed authority-format case) are unaffected and stay above.
# ===========================================================================

# ===========================================================================
# AC1-AC4
# ===========================================================================


def _run_cli(
    args: list[str], cwd: Path | None = None, env: dict | None = None
) -> subprocess.CompletedProcess[str]:
    import os

    full_env = dict(os.environ)
    if env:
        full_env.update(env)
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        cwd=cwd or REPO_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=full_env,
        timeout=120,
    )


def _copy_real_workflow_contract(dest: Path) -> None:
    shutil.copy(REPO_ROOT / "docs" / "claims" / "workflow-contract.yaml", dest)


def _copy_registry_referenced_files(dest_root: Path, registry: cce.Registry) -> None:
    """Copy only the specific files a registry references (not the whole
    worktree, which includes .venv and other paths shutil.copytree chokes
    on) into dest_root, preserving relative paths."""
    rel_paths: set[str] = set()
    for doc in registry.governed_doc_set:
        rel_paths.add(doc.path)
    for entry in registry.entries:
        rel_paths.add(entry.where_file)
        for _label, role_file in entry.role_files():
            rel_paths.add(role_file.path)
    rel_paths.add("docs/claims/claims.yaml")
    rel_paths.add("docs/claims/claims-schema.json")
    for rel in rel_paths:
        src = REPO_ROOT / rel
        if not src.is_file():
            continue
        dst = dest_root / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)


def _real_registered_claims() -> list:
    registry = cce.load_registry(REPO_ROOT / "docs" / "claims" / "claims.yaml")
    return [entry for entry in registry.entries if entry.resolution == "same_run"]


def _prepare_ac1_synthetic_artifacts(tmp_path: Path, meta_run_attempt: str) -> tuple[Path, str]:
    """Shared AC1 fixture builder: synthetic-but-otherwise-real-shaped CI
    artifacts for every real producer in workflow-contract.yaml, standing in
    for a real GitHub Actions run (no Docker/GitHub available locally).
    `meta_run_attempt` is the run_attempt value written into every
    producer's meta JSON — parameterized so the positive case
    (test_ac1_verifier_green_on_registered_claims_at_head) and its negative
    twin (test_ac1_verifier_red_when_meta_run_attempt_mismatches_env) below
    can share this setup while asserting opposite outcomes."""
    contract = yaml.safe_load(
        (REPO_ROOT / "docs" / "claims" / "workflow-contract.yaml").read_text()
    )
    producers = contract["expected_producers"]

    artifacts = tmp_path / "artifacts"
    real_repo_head = _git(["rev-parse", "HEAD"], cwd=REPO_ROOT).stdout.strip()

    claims = _real_registered_claims()
    node_ids = [entry.inputs["test"].node_id for entry in claims]  # type: ignore[union-attr]

    for producer, spec in producers.items():
        junit_dir = artifacts / spec["junit_artifact"]
        meta_dir = artifacts / spec["meta_artifact"]
        junit_dir.mkdir(parents=True)
        meta_dir.mkdir(parents=True)
        floor = spec["junit_collection_floor"]
        cases = [(f"synthetic.{producer}", f"filler_{i}", "passed") for i in range(floor)]
        for node_id in node_ids:
            path_part, _, func_part = node_id.partition("::")
            dotted = path_part.removesuffix(".py").replace("/", ".")
            if dotted.startswith(f"tests.{producer.replace('-', '_')}") or producer == "test":
                cases.append((dotted, func_part, "passed"))
        (junit_dir / spec["junit_file"]).write_text(junit_xml(cases), encoding="utf-8")
        (meta_dir / spec["meta_file"]).write_text(
            json.dumps(
                {
                    "job_id": producer,
                    "sha": real_repo_head,
                    "run_id": "999",
                    "run_attempt": meta_run_attempt,
                }
            ),
            encoding="utf-8",
        )

    producer_results = dict.fromkeys(producers, "success")
    (tmp_path / "producer-results.json").write_text(json.dumps(producer_results), encoding="utf-8")

    return artifacts, real_repo_head


def _run_ac1_cli(
    artifacts: Path, real_repo_head: str, tmp_path: Path
) -> subprocess.CompletedProcess[str]:
    return _run_cli(
        [
            "--mode",
            "same-run",
            "--registry",
            str(REPO_ROOT / "docs" / "claims" / "claims.yaml"),
            "--workflow-contract",
            str(REPO_ROOT / "docs" / "claims" / "workflow-contract.yaml"),
            "--workflow-file",
            str(REPO_ROOT / ".github" / "workflows" / "ci-test.yml"),
            "--artifacts-dir",
            str(artifacts),
            "--producer-results",
            str(tmp_path / "producer-results.json"),
            "--event-name",
            "pull_request",
            "--pr-head-sha",
            real_repo_head,
            "--run-id",
            "999",
            "--repo-root",
            str(REPO_ROOT),
        ]
    )


def test_ac1_verifier_green_on_registered_claims_at_head(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AC1: exercised against the REAL committed registry/workflow-contract/
    trust-root/ci-test.yml in this worktree (real blobs must match — this is
    the load-bearing proof that docs/claims/claims.yaml is not aspirational),
    with synthetic-but-otherwise-real-shaped CI artifacts standing in for a
    real GitHub Actions run (no Docker/GitHub available locally).

    Hermetic against the job's OWN run_attempt: `--run-attempt` is never
    passed on the CLI below, so the verifier falls back to its own process's
    `GITHUB_RUN_ATTEMPT` env var (`main`'s `if args.run_attempt is None:
    args.run_attempt = os.environ.get("GITHUB_RUN_ATTEMPT")`), and `_run_cli`
    passes this test's own inherited `os.environ` through to the subprocess.
    Left ambient, a CI re-run attempt (GitHub Actions sets a real
    `GITHUB_RUN_ATTEMPT=2` on attempt 2) would mismatch the "1" hardcoded
    into the synthetic meta below and fail CC-WS3-004 for a reason that has
    nothing to do with what this test actually proves — seen on
    randomized-suite job 100290734871, run 33641309663 attempt 2 (seed
    1070036697). `monkeypatch.setenv` pins `GITHUB_RUN_ATTEMPT` to match the
    meta so the outcome never depends on the job's real attempt number.
    `GITHUB_RUN_ID` is pinned too for the same reason, belt-and-suspenders —
    `--run-id 999` is already passed explicitly below, so `args.run_id` is
    never `None` and the env fallback is never actually consulted, but a
    future edit that drops `--run-id` should not silently regain this
    dependency."""
    artifacts, real_repo_head = _prepare_ac1_synthetic_artifacts(tmp_path, meta_run_attempt="1")
    monkeypatch.setenv("GITHUB_RUN_ATTEMPT", "1")
    monkeypatch.setenv("GITHUB_RUN_ID", "999")

    result = _run_ac1_cli(artifacts, real_repo_head, tmp_path)
    assert result.returncode == 0, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    assert "PASS" in result.stdout


def test_ac1_verifier_red_when_meta_run_attempt_mismatches_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Negative twin of test_ac1_verifier_green_on_registered_claims_at_head,
    at the same real-registry CLI entry point: proves the hermeticity fix
    above (pinning GITHUB_RUN_ATTEMPT to match the meta) did not weaken the
    CC-WS3-004 run_attempt guard it works around — only removed that guard's
    dependence on the job's own ambient attempt number. Meta says
    run_attempt "1" (same synthetic fixture); the environment says this is
    attempt "2" — a genuine mismatch the verifier must still reject."""
    artifacts, real_repo_head = _prepare_ac1_synthetic_artifacts(tmp_path, meta_run_attempt="1")
    monkeypatch.setenv("GITHUB_RUN_ATTEMPT", "2")
    monkeypatch.setenv("GITHUB_RUN_ID", "999")

    result = _run_ac1_cli(artifacts, real_repo_head, tmp_path)
    assert result.returncode == 1, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    assert "run_attempt" in result.stdout and "CC-WS3-004" in result.stdout, result.stdout


def test_ac2_every_d8_group_is_represented_by_its_own_red_test() -> None:
    """A structural check on the suite itself: every D8 group this verifier
    still enforces has at least one test function whose name says so.

    2026-08-07: groups 9-11 and 14-15 (evidence-record path/create-only/
    blob problems, signer pinning, tampered/unaccepted trust root,
    governance-binding + slug grammar, introducing-commit/A-B-A/retroactive
    authority) covered the external-evidence/authority-record verifier
    machinery that was removed outright -- see this file's module
    docstring. The remaining groups (D1-D4 same-run enforcement, D2 blob
    drift, D8's self-reference probe and producer-floor checks) are
    unaffected and still required to have coverage."""
    module = sys.modules[__name__]
    names = [name for name in dir(module) if name.startswith("test_group")]
    covered = {int(name.split("_")[1].removeprefix("group")) for name in names}
    remaining_groups = {1, 2, 3, 4, 5, 6, 7, 8, 12, 13}
    assert covered == remaining_groups, covered
    for group in remaining_groups:
        assert any(f"group{group}_" in name for name in names), f"D8 group {group} has no test"


def test_ac3_editing_bound_input_without_rebinding_exits_naming_blob_drift(tmp_path: Path) -> None:
    """AC3, exercised against a real registered claim's REAL prose file: a
    live edit that is NOT re-bound in the registry must exit 1 naming drift."""
    real_root = REPO_ROOT
    registry = cce.load_registry(real_root / "docs" / "claims" / "claims.yaml")
    target_entry = next(e for e in registry.entries if e.resolution == "same_run")
    prose_role = target_entry.inputs["prose"]
    assert isinstance(prose_role, cce.RoleFile)

    # Copy only the specific files the registry references — not the whole
    # worktree — into an isolated tmp git-less directory (git hash-object
    # works without a repo, as used throughout this module).
    work = tmp_path / "work"
    _copy_registry_referenced_files(work, registry)
    target_path = work / prose_role.path
    target_path.write_text(
        target_path.read_text(encoding="utf-8") + "\nUNBOUND EDIT.\n", encoding="utf-8"
    )
    registry_copy = cce.load_registry(work / "docs" / "claims" / "claims.yaml")
    violations = cce.blob_drift_violations(work, registry_copy)
    assert any("blob drift" in v for v in violations)


def test_ac4_removing_a_marker_in_a_governed_doc_exits_red(tmp_path: Path) -> None:
    """AC4, exercised against the REAL civiccast/dr/__init__.py: stripping
    its claim marker while the strong token remains must be red."""
    real_root = REPO_ROOT
    real_registry = cce.load_registry(real_root / "docs" / "claims" / "claims.yaml")
    work = tmp_path / "work"
    _copy_registry_referenced_files(work, real_registry)
    target = work / "civiccast" / "dr" / "__init__.py"
    text = target.read_text(encoding="utf-8")
    # All three markers (round 5 added ws2-postgres-restore-drill) share one
    # bullet/paragraph block in the real file; strip ALL THREE so the block
    # truly carries zero markers (stripping only some still leaves the
    # block "marked" by its neighbor(s), which is correctly not red).
    stripped = (
        text.replace("(# claim:ws2-postgres-restore-drill)", "(marker removed)")
        .replace("(# claim:ws2-postgres-backup-capture)", "(marker removed)")
        .replace("(# claim:ws2-sqlite-restore-falsification)", "(marker removed)")
    )
    assert stripped != text, "fixture assumption broken: marker text not found to strip"
    target.write_text(stripped, encoding="utf-8")

    registry = cce.load_registry(work / "docs" / "claims" / "claims.yaml")
    violations = cce.scan_marker_violations(work, registry)
    assert any("unmarked strong-claim token" in v for v in violations)


# ===========================================================================
# Round 2 (WS3 audit fixes, CC-WS3-001..005)
# ===========================================================================


# ---------------------------------------------------------------------------
# CC-WS3-001 (Critical): claim-text capability-token lexical tripwire
# ---------------------------------------------------------------------------


def test_ws3r2_001_capability_token_guard_flags_restore_claim_on_backup_only_test(
    repo: Path,
) -> None:
    """The exact ws2-postgres-restore-drill defect, reproduced as a fixture:
    claim text says "restore", the bound test's path/node id says only
    "test_x" (no backup/restore/dump anywhere) — must be flagged."""
    entry = make_entry_dict(repo)
    entry["claim"] = "The backup drill's restore path is exercised for real."
    registry = cce.load_registry(
        write_yaml(repo / "claims.yaml", make_registry_dict(repo, [entry]))
    )
    violations = cce.claim_capability_token_violations(registry)
    assert any("capability token 'restore'" in v and entry["id"] in v for v in violations), (
        violations
    )
    assert any("capability token 'backup'" in v and entry["id"] in v for v in violations), (
        violations
    )


def test_ws3r2_001_capability_token_guard_silent_when_test_name_matches(repo: Path) -> None:
    entry = make_entry_dict(repo)
    entry["claim"] = "The backup drill captures a backup for real."
    entry["inputs"]["test"]["path"] = "test_backup_thing.py"
    entry["inputs"]["test"]["node_id"] = "test_backup_thing.py::test_thing"
    registry = cce.load_registry(
        write_yaml(repo / "claims.yaml", make_registry_dict(repo, [entry]))
    )
    assert cce.claim_capability_token_violations(registry) == []


def test_ws3r2_001_real_registry_has_zero_capability_token_violations() -> None:
    """The rescoped ws2-postgres-backup-capture entry (and every other real
    entry) must never trip its own lexical tripwire at HEAD."""
    registry = cce.load_registry(REPO_ROOT / "docs" / "claims" / "claims.yaml")
    assert cce.claim_capability_token_violations(registry) == []


def test_ws3r2_001_renamed_entry_no_longer_present(repo: Path) -> None:
    """Documents the round-2 rename itself: the round-1 OVERCLAIMING entry
    (claiming "restore" while its bound test only ran pg_dump) is gone.
    Round 5 (CC-WS3-008) reuses this exact id for a NEW, real entry bound
    to the actual end-to-end restore-drill test that entered this branch's
    ancestry with WS2 -- a legitimate re-registration, not the round-1
    defect returning. `ws2-postgres-backup-capture` (its round-2 rename
    target) also remains, as its own separate, still-capture-only entry."""
    registry = cce.load_registry(REPO_ROOT / "docs" / "claims" / "claims.yaml")
    ids = {entry.id for entry in registry.entries}
    assert "ws2-postgres-backup-capture" in ids
    restore_entry = next(e for e in registry.entries if e.id == "ws2-postgres-restore-drill")
    assert restore_entry.inputs["test"].node_id == (
        "tests/dr/test_postgres_restore.py::test_run_full_drill_postgres_end_to_end"
    ), "must be the real end-to-end restore test, not the round-1 backup-only overclaim"


# ---------------------------------------------------------------------------
# CC-WS3-002 (Critical): fail-closed external mode
# ---------------------------------------------------------------------------


def test_ws3r2_002a_parser_empty_controls_external_evidence_is_malformed(repo: Path) -> None:
    entry = make_entry_dict(repo, resolution="external_evidence", controls=[])
    with pytest.raises(cce.MalformedRegistryError, match="ci_safe: false"):
        cce.load_registry(write_yaml(repo / "claims.yaml", make_registry_dict(repo, [entry])))


def test_ws3r2_002a_parser_all_ci_safe_controls_external_evidence_is_malformed(repo: Path) -> None:
    control = {"id": "ci-safe-only", "command": "n/a", "expected_red_when": "n/a", "ci_safe": True}
    entry = make_entry_dict(repo, resolution="external_evidence", controls=[control])
    with pytest.raises(cce.MalformedRegistryError, match="ci_safe: false"):
        cce.load_registry(write_yaml(repo / "claims.yaml", make_registry_dict(repo, [entry])))


def test_ws3r2_002a_parser_one_ci_safe_false_control_is_accepted(repo: Path) -> None:
    control = {
        "id": "one-non-ci-safe",
        "command": "n/a",
        "expected_red_when": "n/a",
        "ci_safe": False,
    }
    entry = make_entry_dict(repo, resolution="external_evidence", controls=[control])
    registry = cce.load_registry(
        write_yaml(repo / "claims.yaml", make_registry_dict(repo, [entry]))
    )
    assert registry.entries[0].resolution == "external_evidence"


# ---------------------------------------------------------------------------
# CC-WS3-003 (Major): identity tying
# ---------------------------------------------------------------------------


def test_ws3r2_003_prose_path_mismatch_where_file_is_malformed(repo: Path) -> None:
    entry = make_entry_dict(repo)
    entry["inputs"]["prose"] = {"path": "governed.py", "blob": blob(repo, "governed.py")}
    # where.file is still "docs.md" (make_entry_dict's default) -> mismatch.
    with pytest.raises(cce.MalformedRegistryError, match="identity tying"):
        cce.load_registry(write_yaml(repo / "claims.yaml", make_registry_dict(repo, [entry])))


def test_ws3r2_003_test_node_id_file_mismatch_test_path_is_malformed(repo: Path) -> None:
    entry = make_entry_dict(repo)
    entry["inputs"]["test"]["node_id"] = "some_other_file.py::test_thing"
    with pytest.raises(cce.MalformedRegistryError, match="identity tying"):
        cce.load_registry(write_yaml(repo / "claims.yaml", make_registry_dict(repo, [entry])))


def test_ws3r2_003_matching_prose_and_test_identity_is_accepted(repo: Path) -> None:
    entry = make_entry_dict(repo)
    registry = cce.load_registry(
        write_yaml(repo / "claims.yaml", make_registry_dict(repo, [entry]))
    )
    assert len(registry.entries) == 1


def test_ws3r2_003_cli_test_identity_mismatch_exits_2_malformed(tmp_path: Path, repo: Path) -> None:
    entry = make_entry_dict(repo)
    entry["inputs"]["test"]["node_id"] = "some_other_file.py::test_thing"
    registry_path = write_yaml(repo / "claims.yaml", make_registry_dict(repo, [entry]))
    result = _run_cli(
        [
            "--mode",
            "same-run",
            "--registry",
            str(registry_path),
            "--workflow-contract",
            str(repo / "contract.yaml"),
            "--workflow-file",
            str(repo / "workflow.yml"),
            "--artifacts-dir",
            str(tmp_path / "artifacts"),
            "--repo-root",
            str(repo),
        ]
    )
    assert result.returncode == 2, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    assert "identity tying" in result.stderr


# ---------------------------------------------------------------------------
# CC-WS3-004 (Major): exact artifact routing
# ---------------------------------------------------------------------------


def test_ws3r2_004_wrong_artifact_directory_is_never_picked_up(tmp_path: Path) -> None:
    artifacts = tmp_path / "artifacts"
    decoy_dir = artifacts / "some-other-producer-junit"
    decoy_dir.mkdir(parents=True)
    (decoy_dir / "junit.xml").write_text(junit_xml(n_passed_cases("t", 1)), encoding="utf-8")
    # No file at the contract's EXACT "test-junit/junit.xml" path.
    assert cce.find_producer_artifact(artifacts, "test-junit", "junit.xml") is None
    violations, _ = cce.producer_evidence_violations(
        _base_contract(), artifacts, {"test": "success"}, SOURCE_SHA_A
    )
    assert any("junit artifact" in v and "missing" in v for v in violations)


def test_ws3r2_004_duplicate_filename_outside_canonical_dir_is_ignored(tmp_path: Path) -> None:
    artifacts = tmp_path / "artifacts"
    decoy_dir = artifacts / "decoy"
    decoy_dir.mkdir(parents=True)
    (decoy_dir / "junit.xml").write_text(junit_xml(n_passed_cases("decoy", 99)), encoding="utf-8")
    real_dir = artifacts / "test-junit"
    real_dir.mkdir(parents=True)
    (real_dir / "junit.xml").write_text(junit_xml(n_passed_cases("real", 1)), encoding="utf-8")
    found = cce.find_producer_artifact(artifacts, "test-junit", "junit.xml")
    assert found == real_dir / "junit.xml", "must resolve the exact contract path, never the decoy"


def test_flat_layout_fallback_resolves_when_no_artifact_name_subdirectory_exists(
    tmp_path: Path,
) -> None:
    """2026-08-24 (nats-boundary removal, PR #23): `actions/download-artifact`
    (confirmed on v7.0.0) places a `pattern`-matched download directly in
    `path`, with no `<artifact-name>/` subdirectory, whenever the pattern
    matches exactly one artifact in the run. `docs/claims/workflow-
    contract.yaml`'s producers are downloaded by pattern in ci-test.yml, so
    losing a second `*-meta`/`*-junit` producer (nats-boundary) flipped the
    real on-disk layout from nested to flat for the sole remaining
    producer, even though nothing about that producer's own upload step
    changed. The resolver must accept the flat layout too.
    """
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir(parents=True)
    (artifacts / "test-meta.json").write_text(json.dumps({"sha": SOURCE_SHA_A}), encoding="utf-8")
    found = cce.find_producer_artifact(artifacts, "test-meta", "test-meta.json")
    assert found == artifacts / "test-meta.json"


def test_nested_layout_is_preferred_over_a_same_named_flat_file(tmp_path: Path) -> None:
    """If both layouts happen to be present, the exact contract path
    (`<artifacts_dir>/<artifact_name>/<file_name>`) wins — the flat
    fallback is a fallback, not a competing source of truth."""
    artifacts = tmp_path / "artifacts"
    nested_dir = artifacts / "test-meta"
    nested_dir.mkdir(parents=True)
    (nested_dir / "test-meta.json").write_text(
        json.dumps({"sha": SOURCE_SHA_A, "job_id": "nested"}), encoding="utf-8"
    )
    (artifacts / "test-meta.json").write_text(
        json.dumps({"sha": SOURCE_SHA_A, "job_id": "flat"}), encoding="utf-8"
    )
    found = cce.find_producer_artifact(artifacts, "test-meta", "test-meta.json")
    assert found == nested_dir / "test-meta.json"


def test_flat_layout_end_to_end_through_producer_evidence_violations(tmp_path: Path) -> None:
    """Same scenario as the PR #23 CI failure, exercised through the real
    entry point (`producer_evidence_violations`), not just the path
    resolver directly: a flat-layout meta artifact must NOT be reported
    missing."""
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir(parents=True)
    (artifacts / "test-meta.json").write_text(
        json.dumps({"sha": SOURCE_SHA_A, "run_id": "1", "job_id": "test"}), encoding="utf-8"
    )
    junit_dir = artifacts / "test-junit"
    junit_dir.mkdir(parents=True)
    (junit_dir / "junit.xml").write_text(junit_xml(n_passed_cases("t", 1)), encoding="utf-8")
    violations, evidence = cce.producer_evidence_violations(
        _base_contract(), artifacts, {"test": "success"}, SOURCE_SHA_A, expected_run_id="1"
    )
    assert violations == [], violations
    assert "test" in evidence


def test_ws3r2_004_wrong_job_id_in_meta_is_a_violation(tmp_path: Path) -> None:
    artifacts = tmp_path / "artifacts"
    _write_producer_artifacts(
        artifacts,
        {"sha": SOURCE_SHA_A, "run_id": "1", "job_id": "not-test"},
        n_passed_cases("t", 1),
    )
    violations, _ = cce.producer_evidence_violations(
        _base_contract(), artifacts, {"test": "success"}, SOURCE_SHA_A
    )
    assert any("meta job_id" in v and "CC-WS3-004" in v for v in violations), violations


def test_ws3r2_004_correct_job_id_in_meta_is_accepted(tmp_path: Path) -> None:
    artifacts = tmp_path / "artifacts"
    _write_producer_artifacts(
        artifacts, {"sha": SOURCE_SHA_A, "run_id": "1", "job_id": "test"}, n_passed_cases("t", 1)
    )
    violations, _ = cce.producer_evidence_violations(
        _base_contract(), artifacts, {"test": "success"}, SOURCE_SHA_A
    )
    assert not any("meta job_id" in v for v in violations)


def test_ws3r2_004_prior_attempt_metadata_is_a_violation(tmp_path: Path) -> None:
    artifacts = tmp_path / "artifacts"
    _write_producer_artifacts(
        artifacts,
        {"sha": SOURCE_SHA_A, "run_id": "1", "job_id": "test", "run_attempt": "1"},
        n_passed_cases("t", 1),
    )
    violations, _ = cce.producer_evidence_violations(
        _base_contract(), artifacts, {"test": "success"}, SOURCE_SHA_A, expected_run_attempt="2"
    )
    assert any("run_attempt" in v and "CC-WS3-004" in v for v in violations), violations


def test_ws3r2_004_matching_run_attempt_is_accepted(tmp_path: Path) -> None:
    artifacts = tmp_path / "artifacts"
    _write_producer_artifacts(
        artifacts,
        {"sha": SOURCE_SHA_A, "run_id": "1", "job_id": "test", "run_attempt": "2"},
        n_passed_cases("t", 1),
    )
    violations, _ = cce.producer_evidence_violations(
        _base_contract(), artifacts, {"test": "success"}, SOURCE_SHA_A, expected_run_attempt="2"
    )
    assert not any("run_attempt" in v for v in violations)


# ---------------------------------------------------------------------------
# CC-WS3-005 (Major): the three missing D9 program claims
# ---------------------------------------------------------------------------


def test_ws3r2_005_three_new_registry_entries_present_and_clean() -> None:
    registry = cce.load_registry(REPO_ROOT / "docs" / "claims" / "claims.yaml")
    ids = {entry.id for entry in registry.entries}
    assert {
        "ws1-release-truth-checker",
        "native-decision-gate",
        "session0-service-broadcast",
    } <= ids
    by_id = {entry.id: entry for entry in registry.entries}
    assert by_id["ws1-release-truth-checker"].resolution == "same_run"
    assert by_id["native-decision-gate"].resolution == "external_evidence"
    assert by_id["session0-service-broadcast"].resolution == "external_evidence"
    for claim_id in ("native-decision-gate", "session0-service-broadcast"):
        non_ci_safe = [c for c in by_id[claim_id].controls if not c.ci_safe]
        assert non_ci_safe, f"{claim_id} must carry >=1 ci_safe:false control"
    assert cce.scan_marker_violations(REPO_ROOT, registry) == []
    assert cce.blob_drift_violations(REPO_ROOT, registry) == []
    assert cce.claim_capability_token_violations(registry) == []


def test_ws3r2_005_release_truth_checker_test_node_is_bound_and_real() -> None:
    registry = cce.load_registry(REPO_ROOT / "docs" / "claims" / "claims.yaml")
    entry = next(e for e in registry.entries if e.id == "ws1-release-truth-checker")
    test_role = entry.inputs["test"]
    assert isinstance(test_role, cce.RoleFile)
    assert (
        test_role.node_id
        == "tests/policy/test_release_truth.py::test_unknown_live_release_is_drift"
    )


# ===========================================================================
# CC-WS3-001/002 (D5 evidence-record body schema; PENDING_OWNER_ONLY
# sole-blocker semantics) lived here. Removed 2026-08-07 with the
# external-evidence/authority-record verifier code -- see this file's
# module docstring.
# ===========================================================================


def test_ws3r3_005_code_role_accepts_single_mapping(repo: Path) -> None:
    entry = make_entry_dict(repo)
    registry = cce.load_registry(
        write_yaml(repo / "claims.yaml", make_registry_dict(repo, [entry]))
    )
    code_role = registry.entries[0].inputs["code"]
    assert isinstance(code_role, cce.RoleFile)


def test_ws3r3_005_code_role_accepts_list_of_modules(repo: Path) -> None:
    entry = make_entry_dict(repo)
    entry["inputs"]["code"] = [
        {"path": "code.py", "blob": blob(repo, "code.py")},
        {"path": "generator.py", "blob": blob(repo, "generator.py")},
    ]
    registry = cce.load_registry(
        write_yaml(repo / "claims.yaml", make_registry_dict(repo, [entry]))
    )
    code_role = registry.entries[0].inputs["code"]
    assert isinstance(code_role, list) and len(code_role) == 2
    labels = [label for label, _ in registry.entries[0].role_files()]
    assert "code[0]" in labels and "code[1]" in labels


def test_ws3r3_005_code_role_empty_list_is_malformed(repo: Path) -> None:
    entry = make_entry_dict(repo)
    entry["inputs"]["code"] = []
    with pytest.raises(cce.MalformedRegistryError, match="non-empty list"):
        cce.load_registry(write_yaml(repo / "claims.yaml", make_registry_dict(repo, [entry])))


def test_ws3r3_005_blob_drift_flags_each_code_module_independently(repo: Path) -> None:
    extra = repo / "code2.py"
    extra.write_text("# second module\n", encoding="utf-8")
    entry = make_entry_dict(repo)
    entry["inputs"]["code"] = [
        {"path": "code.py", "blob": blob(repo, "code.py")},
        {"path": "code2.py", "blob": blob(repo, "code2.py")},
    ]
    registry = cce.load_registry(
        write_yaml(repo / "claims.yaml", make_registry_dict(repo, [entry]))
    )
    # Drift ONLY code2.py; code.py must NOT be flagged, code2.py MUST be.
    extra.write_text("# mutated second module\n", encoding="utf-8")
    violations = cce.blob_drift_violations(repo, registry)
    assert any("code[1]" in v and "blob drift" in v for v in violations), violations
    assert not any("code[0]" in v for v in violations), violations


#: Every module the GStreamer playout worker process actually loads, and therefore
#: every module whose bytes an engine-behavior claim is bound to. Adding an import to
#: ``engine.py``/``worker.py`` MUST land here and in both claims' ``code`` roles --
#: that coupling is the point of these two tests.
#:
#: ``decode_policy.py`` joined the set 2026-09-03 (fix/gst-worker-runtime-death): the
#: CPU-decode policy moved out of ``engine.py`` into a gi-free sibling that
#: ``engine.py`` imports at module scope and ``worker.py`` publishes under its package
#: name (see ``tests/egress/test_gst_worker_module_identity.py``).
_CURRENT_ENGINE_DEPENDENCY_MODULES = {
    "civiccast/egress/gst/worker.py",
    "civiccast/egress/gst/engine.py",
    "civiccast/egress/gst/caption_flow.py",
    "civiccast/egress/gst/decode_policy.py",
    "civiccast/egress/gst/optional_props.py",
    "civiccast/egress/gst/graph.py",
    "civiccast/egress/gst/control.py",
    "civiccast/egress/gst/audio_tap.py",
}


def test_ws3r3_005_native_decision_gate_binds_current_engine_dependencies() -> None:
    registry = cce.load_registry(REPO_ROOT / "docs" / "claims" / "claims.yaml")
    entry = next(e for e in registry.entries if e.id == "native-decision-gate")
    code_role = entry.inputs["code"]
    assert isinstance(code_role, list)
    paths = {rf.path for rf in code_role}
    assert paths == _CURRENT_ENGINE_DEPENDENCY_MODULES
    for rf in code_role:
        assert cce.git_hash_object(REPO_ROOT, rf.path) == rf.blob


def test_ws3r3_005_session0_binds_current_engine_dependencies() -> None:
    registry = cce.load_registry(REPO_ROOT / "docs" / "claims" / "claims.yaml")
    entry = next(e for e in registry.entries if e.id == "session0-service-broadcast")
    code_role = entry.inputs["code"]
    assert isinstance(code_role, list)
    paths = {rf.path for rf in code_role}
    assert paths == _CURRENT_ENGINE_DEPENDENCY_MODULES
    for rf in code_role:
        assert cce.git_hash_object(REPO_ROOT, rf.path) == rf.blob


def test_current_external_claims_do_not_describe_changed_files_as_unmodified_or_owner_pending() -> (
    None
):
    registry = cce.load_registry(REPO_ROOT / "docs" / "claims" / "claims.yaml")
    by_id = {entry.id: entry for entry in registry.entries}
    current_claims = (
        by_id["native-decision-gate"].claim,
        by_id["session0-service-broadcast"].claim,
    )
    for claim in current_claims:
        assert "remain unmodified" not in claim
        assert "pending owner trust-root acceptance" not in claim
        assert "current-source external evidence" in claim
        assert "unmodified-since-narrowing" not in claim
        assert "historical observation" in claim
        assert "current-source review tripwire" in claim
        assert "not boot/pre-login or installed-service evidence" in claim
        assert "acceptance results remain pending" in claim


def test_ws3r3_005_native_decision_gate_no_longer_binds_uncommitted_exact_config() -> None:
    """Narrowing proof: native-decision-gate's fixtures no longer point at
    spike-session0's demo-graph.json as if it were the exact config this
    spike ran — native-engine-gate.md's own CORRECTION note says no graph
    JSON was committed with THIS evidence. It binds graph.py itself
    instead (role-path aliasing with the code role is explicitly fine,
    CC-WS3-003)."""
    registry = cce.load_registry(REPO_ROOT / "docs" / "claims" / "claims.yaml")
    entry = next(e for e in registry.entries if e.id == "native-decision-gate")
    fixtures = entry.inputs["fixtures"]
    assert isinstance(fixtures, list)
    fixture_paths = {rf.path for rf in fixtures}
    assert fixture_paths == {"civiccast/egress/gst/graph.py"}


def test_ws3r3_005_session0_still_binds_its_committed_demo_graph() -> None:
    """session0-service-broadcast's evidence doc DOES identify demo-graph.json
    as part of the run's own committed identity, so (unlike
    native-decision-gate) no narrowing was needed here."""
    registry = cce.load_registry(REPO_ROOT / "docs" / "claims" / "claims.yaml")
    entry = next(e for e in registry.entries if e.id == "session0-service-broadcast")
    fixtures = entry.inputs["fixtures"]
    assert isinstance(fixtures, list)
    fixture_paths = {rf.path for rf in fixtures}
    assert fixture_paths == {"docs/evidence/session0-demo-graph.json"}


@pytest.mark.parametrize(
    "claim_id, module_path",
    [
        (claim_id, module_path)
        for claim_id in ("native-decision-gate", "session0-service-broadcast")
        for module_path in sorted(_CURRENT_ENGINE_DEPENDENCY_MODULES)
    ],
)
def test_ws3r3_005_drifting_one_bound_code_module_independently_invalidates_the_claim(
    tmp_path: Path, claim_id: str, module_path: str
) -> None:
    """CC-WS3-005's own acceptance criterion: each additionally-bound code
    module, drifted alone, independently invalidates its claim (a real
    registry entry, not a synthetic fixture) — proving the round-3 binding
    is load-bearing, not decorative."""
    real_registry = cce.load_registry(REPO_ROOT / "docs" / "claims" / "claims.yaml")
    work = tmp_path / "work"
    _copy_registry_referenced_files(work, real_registry)
    target = work / module_path
    target.write_text(target.read_text(encoding="utf-8") + "\n# UNBOUND EDIT.\n", encoding="utf-8")

    registry = cce.load_registry(work / "docs" / "claims" / "claims.yaml")
    violations = cce.blob_drift_violations(work, registry)
    assert violations, f"expected a blob-drift violation after mutating {module_path}"
    assert all(module_path in v for v in violations), (
        "mutating exactly one file must not implicate any other input path",
        violations,
    )
    assert any(claim_id in v for v in violations), (claim_id, violations)


# ===========================================================================
# Round 4 (WS3 audit fixes: CC-WS3-006 Critical, CC-WS3-007 Minor)
#
# The round-4 finding: a claim's registered `code` input was blob
# `cccc...c`; the evidence body named `bbbb...b`, used
# `environment={"hostname": "..."}` (no tool/version), referenced a
# nonexistent raw path — and resolve_external_evidence returned ok=True.
# Two defects: (1) input blob IDs were shape-checked (40-hex) but never
# compared to the claim's registered role-file identities; (2) environment
# accepted ANY non-empty mapping. Fixed by the structured `inputs`
# multiset-equality check and the required `environment.tools` sub-structure
# above; every test below was RED against the pre-round-4 code and is GREEN
# against the fix.
# ===========================================================================

# ---------------------------------------------------------------------------
# CC-WS3-006 (a): input blob_id mismatch — one entry, different well-formed blob
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# CC-WS3-006 (b): extra / missing / duplicate input entries
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# CC-WS3-006 (c): environment.tools — the auditor's literal counterexample
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# CC-WS3-006: the literal round-4 finding, reproduced end-to-end
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# CC-WS3-006 (e): ordering — auth failure still wins over input-binding failure
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# CC-WS3-006: direct-call round-trip (evidence_inputs_for / expected_inputs_for agree)
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# CC-WS3-007 (Minor): schema/runtime parity
# ---------------------------------------------------------------------------


# ===========================================================================
# Round 5 (CC-WS3-008 Major; CC-WS3-007 Minor folds)
# ===========================================================================

# ---------------------------------------------------------------------------
# CC-WS3-007 fold (a): parser/schema additionalProperties parity. The schema
# has always declared additionalProperties:false for roleFile/testRole/
# control/claimEntry (and claimEntry.where) -- the parsers were the half that
# lagged, silently accepting stray keys.
# ---------------------------------------------------------------------------


def test_ws3r5_007a_role_file_rejects_unknown_key(repo: Path) -> None:
    """FALSIFICATION: claims-schema.json's roleFile def is
    additionalProperties: false, but the pre-fix parser silently accepted a
    stray key on a role-file mapping -- it must now raise, naming the key
    AND the parser location (round-6 CC-WS3-007 residue: assert location,
    not just the key name)."""
    entry = make_entry_dict(repo)
    entry["inputs"]["code"]["unexpected_key"] = "surprise"
    with pytest.raises(
        cce.MalformedRegistryError, match=r"demo-claim\.inputs\.code.*unexpected_key"
    ):
        cce.load_registry(write_yaml(repo / "claims.yaml", make_registry_dict(repo, [entry])))


def test_ws3r5_007a_list_role_file_rejects_unknown_key(repo: Path) -> None:
    """FALSIFICATION: the same roleFile def backs list-shaped roles
    (verifier/fixtures/code-as-list) -- a stray key there must also raise,
    naming the key AND the parser location (round-6)."""
    entry = make_entry_dict(repo)
    entry["inputs"]["fixtures"][0]["unexpected_key"] = "surprise"
    with pytest.raises(
        cce.MalformedRegistryError, match=r"demo-claim\.inputs\.fixtures\[0\].*unexpected_key"
    ):
        cce.load_registry(write_yaml(repo / "claims.yaml", make_registry_dict(repo, [entry])))


def test_ws3r5_007a_test_role_file_rejects_unknown_key(repo: Path) -> None:
    """FALSIFICATION: claims-schema.json's testRole def (path/blob/node_id
    only) is additionalProperties: false; a stray key on the `test` role
    must also raise, naming the key AND the parser location (round-6)."""
    entry = make_entry_dict(repo)
    entry["inputs"]["test"]["unexpected_key"] = "surprise"
    with pytest.raises(
        cce.MalformedRegistryError, match=r"demo-claim\.inputs\.test.*unexpected_key"
    ):
        cce.load_registry(write_yaml(repo / "claims.yaml", make_registry_dict(repo, [entry])))


def test_ws3r5_007a_non_test_role_file_rejects_node_id(repo: Path) -> None:
    """FALSIFICATION: claims-schema.json's roleFile def (every role except
    `test`) has no node_id property at all -- only testRole does. A non-test
    role carrying node_id must now raise, matching the schema exactly."""
    entry = make_entry_dict(repo)
    entry["inputs"]["code"]["node_id"] = "code.py::test_something"
    with pytest.raises(cce.MalformedRegistryError, match="node_id"):
        cce.load_registry(write_yaml(repo / "claims.yaml", make_registry_dict(repo, [entry])))


def test_ws3r5_007a_control_rejects_unknown_key(repo: Path) -> None:
    """FALSIFICATION: claims-schema.json's control def is
    additionalProperties: false; a stray key on a control mapping must now
    raise, naming the key AND the parser location (round-6)."""
    control = {
        "id": "a-control",
        "command": "n/a",
        "expected_red_when": "n/a",
        "ci_safe": True,
        "unexpected_key": "surprise",
    }
    entry = make_entry_dict(repo, controls=[control])
    with pytest.raises(
        cce.MalformedRegistryError, match=r"demo-claim\.controls\[0\].*unexpected_key"
    ):
        cce.load_registry(write_yaml(repo / "claims.yaml", make_registry_dict(repo, [entry])))


def test_ws3r5_007a_entry_rejects_unknown_key(repo: Path) -> None:
    """FALSIFICATION: claims-schema.json's claimEntry def is
    additionalProperties: false; a stray top-level entry key must now
    raise, naming the key AND the entry's own location (round-6): the
    colon right after the claim id (not a `.inputs`/`.where` suffix)
    distinguishes this entry-level location from the nested ones above."""
    entry = make_entry_dict(repo)
    entry["unexpected_key"] = "surprise"
    with pytest.raises(
        cce.MalformedRegistryError, match=r"demo-claim: unexpected key.*unexpected_key"
    ):
        cce.load_registry(write_yaml(repo / "claims.yaml", make_registry_dict(repo, [entry])))


def test_ws3r5_007a_where_rejects_unknown_key(repo: Path) -> None:
    """FALSIFICATION: claims-schema.json's claimEntry.where sub-object is
    additionalProperties: false; a stray 'where' key must now raise, naming
    the key AND the parser location (round-6)."""
    entry = make_entry_dict(repo)
    entry["where"]["unexpected_key"] = "surprise"
    with pytest.raises(cce.MalformedRegistryError, match=r"demo-claim\.where.*unexpected_key"):
        cce.load_registry(write_yaml(repo / "claims.yaml", make_registry_dict(repo, [entry])))


def test_ws3r5_007a_real_registry_still_loads_clean_under_strict_parser(tmp_path: Path) -> None:
    """Positive control: the real committed registry carries no stray keys
    anywhere and must still load cleanly under the stricter parser."""
    registry = cce.load_registry(REPO_ROOT / "docs" / "claims" / "claims.yaml")
    assert len(registry.entries) >= 5


# ---------------------------------------------------------------------------
# CC-WS3-008 (Major): D1 per-block CAPABILITY coverage. A marker for one
# registered capability (e.g. capture) must not satisfy D1 for a BROADER
# capability (e.g. restore) strong-claimed in the same governed block.
# ---------------------------------------------------------------------------


def test_ws3r5_008_capture_only_marker_does_not_cover_broader_restore_capability(
    repo: Path,
) -> None:
    """FALSIFICATION: the exact CC-WS3-008 shape, as an isolated fixture -- a
    governed python bullet strong-claims "backup/restore is implemented AND
    executed", but its only adjacent marker's registry entry claims capture
    only. A capture-only entry must NOT satisfy a block that strong-claims a
    broader (restore) capability."""
    capture_entry = make_entry_dict(
        repo,
        claim_id="fx-entry-one",
        where_file="governed.py",
        where_anchor="Postgres backup/restore is implemented",
    )
    capture_entry["claim"] = "Postgres backup capture (pg_dump) is executed for real; capture only."
    (repo / "governed.py").write_text(
        '"""\n'
        "* Postgres backup/restore is implemented AND executed, a real\n"
        "  pg_dump -> restore round trip (# claim:fx-entry-one).\n"
        '"""\n',
        encoding="utf-8",
    )
    registry = cce.load_registry(
        write_yaml(repo / "claims.yaml", make_registry_dict(repo, [capture_entry]))
    )
    violations = cce.scan_marker_violations(repo, registry)
    assert any("restore" in v and "fx-entry-one" in v for v in violations), violations


def test_ws3r5_008_two_markers_covering_all_capabilities_passes(repo: Path) -> None:
    """Positive: once a SECOND marker whose entry's claim text covers the
    missing capability ('restore') sits adjacent to the same block, the
    block passes -- the capture entry stays exactly as narrow as it was."""
    capture_entry = make_entry_dict(
        repo,
        claim_id="fx-entry-one",
        where_file="governed.py",
        where_anchor="Postgres backup/restore is implemented",
    )
    capture_entry["claim"] = "Postgres backup capture (pg_dump) is executed for real; capture only."
    restore_entry = make_entry_dict(
        repo,
        claim_id="fx-entry-two",
        where_file="governed.py",
        where_anchor="Postgres backup/restore is implemented",
    )
    restore_entry["claim"] = (
        "Postgres restore is executed for real, a dump-and-restore round trip, proven end-to-end."
    )
    (repo / "governed.py").write_text(
        '"""\n'
        "* Postgres backup/restore is implemented AND executed, a real\n"
        "  pg_dump -> restore round trip (# claim:fx-entry-one)\n"
        "  (# claim:fx-entry-two).\n"
        '"""\n',
        encoding="utf-8",
    )
    registry = cce.load_registry(
        write_yaml(repo / "claims.yaml", make_registry_dict(repo, [capture_entry, restore_entry]))
    )
    violations = cce.scan_marker_violations(repo, registry)
    assert violations == []


def test_ws3r5_008_block_with_no_capability_keywords_is_unaffected(repo: Path) -> None:
    """MUST NOT require capability coverage for a block with no capability
    keyword at all -- unchanged behavior for ordinary strong-claim blocks."""
    entry = make_entry_dict(repo, where_file="governed.py", where_anchor="is fully implemented")
    (repo / "governed.py").write_text(
        '"""This capability is fully implemented. # claim:demo-claim"""\n', encoding="utf-8"
    )
    registry = cce.load_registry(
        write_yaml(repo / "claims.yaml", make_registry_dict(repo, [entry]))
    )
    assert cce.scan_marker_violations(repo, registry) == []


def test_ws3r5_008_round4_state_reconstruction_is_rejected(tmp_path: Path) -> None:
    """CC-WS3-008 (Major), the round-5 VERDICT's literal ask, fixed round-6
    (round-5's own version of this test had a blind spot the round-6
    verdict named directly): reconstruct the real, FINAL (round-6
    restructured) backup/restore block with ONLY the
    ws2-postgres-restore-drill marker removed -- every other marker,
    INCLUDING the pre-existing ws2-sqlite-restore-falsification marker,
    left exactly in place -- and prove the scanner still rejects it,
    naming the uncovered 'restore' capability.

    Round-5's version of this test stripped BOTH the postgres-restore AND
    the sqlite-restore markers before asserting red; that never exercised
    the real defect, because the sqlite entry's claim text happens to use
    the generic word "restore" (about SQLite, not Postgres) and, pre-fix,
    could silently cover the Postgres bullet's missing association via the
    round-5 `_capability_sub_blocks` "or markers" fallback. Leaving the
    sqlite marker in place is the whole point: this reconstruction is only
    meaningful once (a) `civiccast/dr/__init__.py` is restructured
    (round-6 rule 2) so the sqlite marker lives in its OWN top-level
    bullet, physically separate from the Postgres bullet, and (b)
    `scan_marker_violations` enforces strict per-sub-block marker locality
    (round-6 rule 1, no `or markers` fallback) -- with only one of the two
    landed, this test still fails, as recorded in the round-6 evidence
    README's red-run log."""
    real_root = REPO_ROOT
    real_registry = cce.load_registry(real_root / "docs" / "claims" / "claims.yaml")
    work = tmp_path / "work"
    _copy_registry_referenced_files(work, real_registry)
    target = work / "civiccast" / "dr" / "__init__.py"
    text = target.read_text(encoding="utf-8")
    stripped = text.replace(
        "(# claim:ws2-postgres-restore-drill)", "(marker removed for this reconstruction)"
    )
    assert stripped != text, "fixture assumption broken: restore marker text not found to strip"
    assert "(# claim:ws2-sqlite-restore-falsification)" in stripped, (
        "fixture assumption broken: the sqlite marker must remain untouched in this reconstruction"
    )
    target.write_text(stripped, encoding="utf-8")

    registry = cce.load_registry(work / "docs" / "claims" / "claims.yaml")
    violations = cce.scan_marker_violations(work, registry)
    assert any("restore" in v and "ws2-postgres-backup-capture" in v for v in violations), (
        violations
    )


def test_ws3r5_008_real_postgres_block_has_full_capability_coverage(tmp_path: Path) -> None:
    """Positive: the REAL, fixed civiccast/dr/__init__.py Postgres block
    carries both markers and must report zero marker/capability
    violations."""
    real_root = REPO_ROOT
    real_registry = cce.load_registry(real_root / "docs" / "claims" / "claims.yaml")
    work = tmp_path / "work"
    _copy_registry_referenced_files(work, real_registry)
    registry = cce.load_registry(work / "docs" / "claims" / "claims.yaml")
    assert cce.scan_marker_violations(work, registry) == []


# ===========================================================================
# Round 6 (CC-WS3-008 Major residual; CC-WS3-007 Minor residue folds)
# ===========================================================================

# ---------------------------------------------------------------------------
# CC-WS3-008 residual (round-6 verdict): the second independent false-pass
# form -- a marker-free strong-claim MARKDOWN bullet inheriting coverage
# from a SIBLING bullet's marker via the round-5 `_capability_sub_blocks`
# "or markers" fallback. Strict sub-block locality (rule 1) deletes that
# fallback: a sub-block's own markers are only the markers found IN that
# sub-block, never borrowed from the enclosing paragraph.
# ---------------------------------------------------------------------------


def test_ws3r6_008_markdown_sibling_bullet_marker_does_not_cover_unmarked_bullet(
    repo: Path,
) -> None:
    """FALSIFICATION: the verdict's exact markdown reproduction -- a
    marker-free 'Postgres restore is implemented' bullet sits in the same
    blank-line-delimited paragraph as a SIBLING bullet whose marker's
    entry claim genuinely covers 'restore'. Pre-fix, `sub_markers =
    marker_re.findall(sub_block) or markers` let the unmarked bullet
    inherit the sibling's marker and pass; the fixed scanner must reject
    it -- no inheritance across bullets."""
    sibling_entry = make_entry_dict(
        repo,
        claim_id="sibling-restore-entry",
        where_file="docs.md",
        where_anchor="an unrelated sibling bullet",
    )
    sibling_entry["claim"] = (
        "Postgres restore is proven end to end, a real dump-and-restore round trip."
    )
    (repo / "docs.md").write_text(
        "* Postgres restore is implemented, proven end to end.\n"
        "* an unrelated sibling bullet <!-- claim:sibling-restore-entry -->\n",
        encoding="utf-8",
    )
    registry = cce.load_registry(
        write_yaml(repo / "claims.yaml", make_registry_dict(repo, [sibling_entry]))
    )
    violations = cce.scan_marker_violations(repo, registry)
    assert any("restore" in v and "no marker of its own" in v for v in violations), violations


def test_ws3r6_008_markdown_bullet_with_its_own_marker_passes(repo: Path) -> None:
    """Positive companion: once the strong-claim bullet carries its OWN
    marker (no sibling inheritance needed), the block passes -- the
    locality fix must not newly flag a genuinely marked bullet."""
    own_entry = make_entry_dict(
        repo, claim_id="own-restore-entry", where_file="docs.md", where_anchor="Postgres restore"
    )
    own_entry["claim"] = (
        "Postgres restore is proven end to end, a real dump-and-restore round trip."
    )
    (repo / "docs.md").write_text(
        "* Postgres restore is implemented, proven end to end. "
        "<!-- claim:own-restore-entry -->\n"
        "* an unrelated sibling bullet with no claim of its own.\n",
        encoding="utf-8",
    )
    registry = cce.load_registry(
        write_yaml(repo / "claims.yaml", make_registry_dict(repo, [own_entry]))
    )
    assert cce.scan_marker_violations(repo, registry) == []


# ---------------------------------------------------------------------------
# CC-WS3-007 residue (b): registry-root and governed-document unknown-key
# parity -- claims-schema.json declares additionalProperties:false at BOTH
# the registry root and each governed_doc_set item, but load_registry never
# enforced either.
# ---------------------------------------------------------------------------


def test_ws3r6_007b_registry_root_rejects_unknown_key(repo: Path) -> None:
    """FALSIFICATION: the registry root schema is additionalProperties:
    false; a stray top-level registry key must now raise, naming the
    location (not just the key)."""
    entry = make_entry_dict(repo)
    registry_dict = make_registry_dict(repo, [entry])
    registry_dict["unexpected_key"] = "surprise"
    with pytest.raises(cce.MalformedRegistryError, match=r"registry root.*unexpected_key"):
        cce.load_registry(write_yaml(repo / "claims.yaml", registry_dict))


def test_ws3r6_007b_governed_doc_rejects_unknown_key(repo: Path) -> None:
    """FALSIFICATION: each governed_doc_set item schema is
    additionalProperties: false too; a stray key on governed_doc_set[0]
    must now raise, naming the location."""
    entry = make_entry_dict(repo)
    registry_dict = make_registry_dict(repo, [entry])
    registry_dict["governed_doc_set"][0]["unexpected_key"] = "surprise"
    with pytest.raises(cce.MalformedRegistryError, match=r"governed_doc_set\[0\].*unexpected_key"):
        cce.load_registry(write_yaml(repo / "claims.yaml", registry_dict))


# ===========================================================================
# Round 7 (CC-WS3-008 residual #2, round-6 verdict): normal Markdown list
# forms other than "*"/"-" still fused sibling items into one sub-block, so
# a strong-claim item's own missing marker could be silently "covered" by a
# sibling's marker. Each form below gets an expected-red sibling-marker
# regression (no borrowing) with an own-marker positive companion, plus a
# fail-closed regression for a list-start shape outside the supported set.
# ===========================================================================


def _sibling_restore_entry(repo: Path, claim_id: str = "sibling-restore-entry") -> dict:
    entry = make_entry_dict(
        repo, claim_id=claim_id, where_file="docs.md", where_anchor="an unrelated sibling"
    )
    entry["claim"] = "Postgres restore is proven end to end, a real dump-and-restore round trip."
    return entry


def _own_restore_entry(repo: Path, claim_id: str = "own-restore-entry") -> dict:
    entry = make_entry_dict(
        repo, claim_id=claim_id, where_file="docs.md", where_anchor="Postgres restore"
    )
    entry["claim"] = "Postgres restore is proven end to end, a real dump-and-restore round trip."
    return entry


def test_ws3r7_008_plus_sibling_marker_does_not_cover_unmarked_item(repo: Path) -> None:
    """FALSIFICATION (CC-WS3-008 round-7, "+" form): a marker-free
    "Postgres restore is implemented" "+" item sits next to a SIBLING "+"
    item whose marker's entry genuinely covers "restore". Round-6's
    `_bullet_split` never recognized "+" at all, so both items stayed one
    fused sub-block and the sibling's marker silently covered the claim.
    The round-7 splitter must reject it -- no inheritance across items."""
    sibling_entry = _sibling_restore_entry(repo)
    (repo / "docs.md").write_text(
        "+ Postgres restore is implemented, proven end to end.\n"
        "+ an unrelated sibling item <!-- claim:sibling-restore-entry -->\n",
        encoding="utf-8",
    )
    registry = cce.load_registry(
        write_yaml(repo / "claims.yaml", make_registry_dict(repo, [sibling_entry]))
    )
    violations = cce.scan_marker_violations(repo, registry)
    assert any("restore" in v and "no marker of its own" in v for v in violations), violations


def test_ws3r7_008_plus_item_with_its_own_marker_passes(repo: Path) -> None:
    """Positive companion: the "+" strong-claim item carries its OWN
    marker -- the fix must not newly flag a genuinely marked item."""
    own_entry = _own_restore_entry(repo)
    (repo / "docs.md").write_text(
        "+ Postgres restore is implemented, proven end to end. <!-- claim:own-restore-entry -->\n"
        "+ an unrelated sibling item with no claim of its own.\n",
        encoding="utf-8",
    )
    registry = cce.load_registry(
        write_yaml(repo / "claims.yaml", make_registry_dict(repo, [own_entry]))
    )
    assert cce.scan_marker_violations(repo, registry) == []


def test_ws3r7_008_ordered_dot_sibling_marker_does_not_cover_unmarked_item(repo: Path) -> None:
    """FALSIFICATION (ordered-dot form, "1. "/"2. "): same shape as the
    "+" case above -- round-6's splitter never recognized digit markers,
    so the sibling's marker silently covered the unmarked claim item."""
    sibling_entry = _sibling_restore_entry(repo)
    (repo / "docs.md").write_text(
        "1. Postgres restore is implemented, proven end to end.\n"
        "2. an unrelated sibling item <!-- claim:sibling-restore-entry -->\n",
        encoding="utf-8",
    )
    registry = cce.load_registry(
        write_yaml(repo / "claims.yaml", make_registry_dict(repo, [sibling_entry]))
    )
    violations = cce.scan_marker_violations(repo, registry)
    assert any("restore" in v and "no marker of its own" in v for v in violations), violations


def test_ws3r7_008_ordered_dot_item_with_its_own_marker_passes(repo: Path) -> None:
    """Positive companion for ordered-dot."""
    own_entry = _own_restore_entry(repo)
    (repo / "docs.md").write_text(
        "1. Postgres restore is implemented, proven end to end. <!-- claim:own-restore-entry -->\n"
        "2. an unrelated sibling item with no claim of its own.\n",
        encoding="utf-8",
    )
    registry = cce.load_registry(
        write_yaml(repo / "claims.yaml", make_registry_dict(repo, [own_entry]))
    )
    assert cce.scan_marker_violations(repo, registry) == []


def test_ws3r7_008_ordered_paren_sibling_marker_does_not_cover_unmarked_item(repo: Path) -> None:
    """FALSIFICATION (ordered-paren form, "1) "/"2) ")."""
    sibling_entry = _sibling_restore_entry(repo)
    (repo / "docs.md").write_text(
        "1) Postgres restore is implemented, proven end to end.\n"
        "2) an unrelated sibling item <!-- claim:sibling-restore-entry -->\n",
        encoding="utf-8",
    )
    registry = cce.load_registry(
        write_yaml(repo / "claims.yaml", make_registry_dict(repo, [sibling_entry]))
    )
    violations = cce.scan_marker_violations(repo, registry)
    assert any("restore" in v and "no marker of its own" in v for v in violations), violations


def test_ws3r7_008_ordered_paren_item_with_its_own_marker_passes(repo: Path) -> None:
    """Positive companion for ordered-paren."""
    own_entry = _own_restore_entry(repo)
    (repo / "docs.md").write_text(
        "1) Postgres restore is implemented, proven end to end. <!-- claim:own-restore-entry -->\n"
        "2) an unrelated sibling item with no claim of its own.\n",
        encoding="utf-8",
    )
    registry = cce.load_registry(
        write_yaml(repo / "claims.yaml", make_registry_dict(repo, [own_entry]))
    )
    assert cce.scan_marker_violations(repo, registry) == []


def test_ws3r7_008_blockquoted_sibling_marker_does_not_cover_unmarked_item(repo: Path) -> None:
    """FALSIFICATION (blockquoted form, "> * "): round-6's splitter matched
    the raw line only, never stripping a leading "> " quote prefix, so a
    quoted item never split at all and the sibling's marker covered it."""
    sibling_entry = _sibling_restore_entry(repo)
    (repo / "docs.md").write_text(
        "> * Postgres restore is implemented, proven end to end.\n"
        "> * an unrelated sibling item <!-- claim:sibling-restore-entry -->\n",
        encoding="utf-8",
    )
    registry = cce.load_registry(
        write_yaml(repo / "claims.yaml", make_registry_dict(repo, [sibling_entry]))
    )
    violations = cce.scan_marker_violations(repo, registry)
    assert any("restore" in v and "no marker of its own" in v for v in violations), violations


def test_ws3r7_008_blockquoted_item_with_its_own_marker_passes(repo: Path) -> None:
    """Positive companion for the blockquoted form."""
    own_entry = _own_restore_entry(repo)
    (repo / "docs.md").write_text(
        "> * Postgres restore is implemented, proven end to end. <!-- claim:own-restore-entry -->\n"
        "> * an unrelated sibling item with no claim of its own.\n",
        encoding="utf-8",
    )
    registry = cce.load_registry(
        write_yaml(repo / "claims.yaml", make_registry_dict(repo, [own_entry]))
    )
    assert cce.scan_marker_violations(repo, registry) == []


def test_ws3r7_008_deeper_nested_sibling_children_marker_does_not_cover_unmarked_child(
    repo: Path,
) -> None:
    """FALSIFICATION (valid deeper-nested form): two "*" children nested
    five spaces under a parent item (round-6's verdict evidence: a nested
    "*" item indented five spaces fell outside `\\s{0,4}` and fused with
    its parent). A marker-free child must not borrow its NESTED SIBLING
    child's marker either."""
    sibling_entry = _sibling_restore_entry(repo)
    (repo / "docs.md").write_text(
        "* Postgres restore drills (parent note, no claim of its own here).\n"
        "     * Postgres restore is implemented, proven end to end.\n"
        "     * an unrelated sibling item <!-- claim:sibling-restore-entry -->\n",
        encoding="utf-8",
    )
    registry = cce.load_registry(
        write_yaml(repo / "claims.yaml", make_registry_dict(repo, [sibling_entry]))
    )
    violations = cce.scan_marker_violations(repo, registry)
    assert any("restore" in v and "no marker of its own" in v for v in violations), violations


def test_ws3r7_008_deeper_nested_child_with_its_own_marker_passes(repo: Path) -> None:
    """Positive companion for the nested-sibling-children case."""
    own_entry = _own_restore_entry(repo)
    (repo / "docs.md").write_text(
        "* Postgres restore drills (parent note, no claim of its own here).\n"
        "     * Postgres restore is implemented, proven end to end. <!-- claim:own-restore-entry -->\n"
        "     * an unrelated sibling item with no claim of its own.\n",
        encoding="utf-8",
    )
    registry = cce.load_registry(
        write_yaml(repo / "claims.yaml", make_registry_dict(repo, [own_entry]))
    )
    assert cce.scan_marker_violations(repo, registry) == []


def test_ws3r7_008_deeper_nested_parent_marker_does_not_cover_unmarked_child(repo: Path) -> None:
    """FALSIFICATION (nested case, no PARENT inheritance): the PARENT item
    carries a marker whose entry covers "restore"; a CHILD item nested
    five spaces under it strong-claims "restore" with no marker of its
    own. A parent's marker must not cover its child's unmarked claim."""
    parent_entry = make_entry_dict(
        repo,
        claim_id="parent-restore-entry",
        where_file="docs.md",
        where_anchor="Postgres restore drills",
    )
    parent_entry["claim"] = (
        "Postgres restore is proven end to end, a real dump-and-restore round trip."
    )
    (repo / "docs.md").write_text(
        "* Postgres restore drills. <!-- claim:parent-restore-entry -->\n"
        "     * Postgres restore is implemented, proven end to end.\n",
        encoding="utf-8",
    )
    registry = cce.load_registry(
        write_yaml(repo / "claims.yaml", make_registry_dict(repo, [parent_entry]))
    )
    violations = cce.scan_marker_violations(repo, registry)
    assert any("restore" in v and "no marker of its own" in v for v in violations), violations


def test_ws3r7_008_deeper_nested_child_own_marker_passes_no_parent_needed(repo: Path) -> None:
    """Positive companion: the child carries its OWN marker (the parent
    has none at all) -- the fix must not require a parent marker either,
    just true own-item locality."""
    child_entry = make_entry_dict(
        repo,
        claim_id="child-restore-entry",
        where_file="docs.md",
        where_anchor="Postgres restore is implemented",
    )
    child_entry["claim"] = (
        "Postgres restore is proven end to end, a real dump-and-restore round trip."
    )
    (repo / "docs.md").write_text(
        "* Postgres restore drills, no claim of its own here.\n"
        "     * Postgres restore is implemented, proven end to end. <!-- claim:child-restore-entry -->\n",
        encoding="utf-8",
    )
    registry = cce.load_registry(
        write_yaml(repo / "claims.yaml", make_registry_dict(repo, [child_entry]))
    )
    assert cce.scan_marker_violations(repo, registry) == []


def test_ws3r7_008_letter_ordered_item_fails_closed_instead_of_silently_passing(repo: Path) -> None:
    """FALSIFICATION (fail-closed rule 3): a letter-ordered "a. " item is
    outside the supported marker set ("*", "-", "+", ordered-dot,
    ordered-paren). Its strong-claim text ("restore ... implemented") has
    no marker of its own, but a SIBLING "*" item's marker sits in the same
    blank-line-delimited block. Because the letter-ordered item cannot be
    confidently localized, the splitter must fail closed (name the doc,
    the line, and "unsupported list form for locality analysis") rather
    than silently excluding it from the (would-be green) per-item scan."""
    sibling_entry = _sibling_restore_entry(repo)
    (repo / "docs.md").write_text(
        "a. Postgres restore is implemented, proven end to end.\n"
        "* an unrelated sibling item <!-- claim:sibling-restore-entry -->\n",
        encoding="utf-8",
    )
    registry = cce.load_registry(
        write_yaml(repo / "claims.yaml", make_registry_dict(repo, [sibling_entry]))
    )
    violations = cce.scan_marker_violations(repo, registry)
    assert any(
        "docs.md:1" in v and "unsupported list form for locality analysis" in v for v in violations
    ), violations


def test_ws3r7_008_letter_ordered_item_without_a_nearby_strong_claim_does_not_fail_closed(
    repo: Path,
) -> None:
    """Precision companion: a letter-ordered item with NO strong-claim
    token anywhere in its own accumulated text must not trip the
    fail-closed rule -- it only fires when a strong token or marker
    actually sits in or next to the unsupported content."""
    entry = make_entry_dict(
        repo, where_file="docs.md", where_anchor="Postgres restore is implemented"
    )
    entry["claim"] = "Postgres restore is proven end to end, a real dump-and-restore round trip."
    (repo / "docs.md").write_text(
        "a. an ordinary letter-ordered note, nothing strong-claimed here.\n"
        "* Postgres restore is implemented, proven end to end. <!-- claim:demo-claim -->\n",
        encoding="utf-8",
    )
    registry = cce.load_registry(
        write_yaml(repo / "claims.yaml", make_registry_dict(repo, [entry]))
    )
    violations = cce.scan_marker_violations(repo, registry)
    assert not any("unsupported list form for locality analysis" in v for v in violations), (
        violations
    )


# ===========================================================================
# Round 8 (CC-WS3-008 round-7 verdict, Major): the block-level early exits
# in scan_marker_violations ("no strong token -> continue", "no markers ->
# continue") ran BEFORE the round-7 unsupported-form analysis, so either
# one-sided trigger -- a marker with no strong token anywhere in the block,
# or a strong token with no marker anywhere in the block -- skipped the
# unsupported-form check entirely. R-A and R-B below are each the verdict's
# literal falsification, red against the round-7 code (returns [] / emits
# only the generic unmarked-token violation).
# ===========================================================================


def test_ws3r8_008_letter_ordered_marker_no_strong_token_fails_closed(repo: Path) -> None:
    """R-A FALSIFICATION: a letter-ordered "a. " item with ordinary prose
    and a valid, registered claim marker (matching registry entry + anchor)
    but NO strong-claim token anywhere. Round-7's `if not token_re.search
    (block): continue` (~:914) ran before unsupported-form analysis ever
    started, so a marker sitting in an unsupported list form was accepted
    with zero locality analysis -- `scan_marker_violations` returned [].
    The fix must still name the file, the exact line, and "unsupported
    list form for locality analysis" because a claim marker sits in it."""
    entry = make_entry_dict(repo, where_file="docs.md", where_anchor="Ordinary prose here")
    (repo / "docs.md").write_text(
        "a. Ordinary prose here, nothing strong-claimed. <!-- claim:demo-claim -->\n",
        encoding="utf-8",
    )
    registry = cce.load_registry(
        write_yaml(repo / "claims.yaml", make_registry_dict(repo, [entry]))
    )
    violations = cce.scan_marker_violations(repo, registry)
    assert any(
        "docs.md:1" in v and "unsupported list form for locality analysis" in v for v in violations
    ), violations


def test_ws3r8_008_letter_ordered_strong_claim_no_marker_anywhere_fails_closed(repo: Path) -> None:
    """R-B FALSIFICATION: a letter-ordered "a. " strong-claim item with NO
    claim marker anywhere in the governed doc. Round-7's `if not markers:
    ...; continue` (~:917) fired the older generic unmarked-token message
    and returned before the unsupported-form analysis at (~:938) ever ran,
    so the required "unsupported list form for locality analysis" naming
    doc:line never appeared. The fix must emit it (alongside, not instead
    of, the still-correct generic unmarked-token violation)."""
    entry = make_entry_dict(
        repo, where_file="docs.md", where_anchor="Postgres restore is implemented"
    )
    (repo / "docs.md").write_text(
        "a. Postgres restore is implemented, proven end to end.\n",
        encoding="utf-8",
    )
    registry = cce.load_registry(
        write_yaml(repo / "claims.yaml", make_registry_dict(repo, [entry]))
    )
    violations = cce.scan_marker_violations(repo, registry)
    assert any(
        "docs.md:1" in v and "unsupported list form for locality analysis" in v for v in violations
    ), violations
    assert any("unmarked strong-claim token" in v for v in violations), violations
