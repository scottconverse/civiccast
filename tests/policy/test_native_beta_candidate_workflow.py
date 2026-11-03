# SPDX-License-Identifier: Apache-2.0
# Copyright (c) The CivicCast Authors
"""Contract for the branch-gated, non-publishing native-beta artifact producer."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

from scripts.policy.check_actions_budget import validate_workflow

WORKFLOW = Path(".github/workflows/native-beta-candidate-artifacts.yml")
CATALOG = Path("civiccast/apps/installer/src-tauri/src/acquisition_catalog.rs")


def _workflow() -> tuple[str, dict[str, object]]:
    text = WORKFLOW.read_text(encoding="utf-8")
    return text, yaml.load(text, Loader=yaml.BaseLoader)


def _job_from_text(text: str) -> dict[str, object]:
    workflow = yaml.load(text, Loader=yaml.BaseLoader)
    return workflow["jobs"]["build-native-beta"]


def _powershell_lines(script: str) -> tuple[str, ...]:
    return tuple(line.strip() for line in script.splitlines() if line.strip())


def _assert_cpython_handoff(job: dict[str, object]) -> None:
    steps = {step["name"]: step for step in job["steps"]}
    filename = "python-3.12.16-expat-2.10.0-civiccast-embed-amd64.zip"
    producer = _powershell_lines(steps["Acquire the pinned CPython payload interpreter"]["run"])
    consumer = _powershell_lines(steps["Build and verify signed component packs"]["run"])

    assert f'$filename = "{filename}"' in producer
    assert "$pythonEmbedZip = Join-Path $env:RUNNER_TEMP $filename" in producer
    assert "Copy-Item -LiteralPath $mirror -Destination $pythonEmbedZip" in producer
    assert any("runtime-artifact-mirror" in line for line in producer)
    assert any("11202072" in line for line in producer)
    assert any(
        "cb9dda240374cedfcfad227831683779d0cd29b49a46f2e865affd5ea21aa9b6" in line
        for line in producer
    )
    assert "Invoke-WebRequest" not in "\n".join(producer)
    assert consumer.count(f'$pythonEmbedZip = Join-Path $env:RUNNER_TEMP "{filename}"') == 1
    assert "--interpreter-zip $pythonEmbedZip `" in consumer


def _assert_fail_closed_pack_guard(job: dict[str, object]) -> None:
    step_names = [step["name"] for step in job["steps"]]
    guard_name = "Assert clean source tree before pack build"
    bind_name = "Bind packs to the checked-out source commit"
    pack_name = "Build and verify signed component packs"
    assert step_names.index(guard_name) == step_names.index(bind_name) - 1
    assert step_names.index(bind_name) == step_names.index(pack_name) - 1

    steps = {step["name"]: step for step in job["steps"]}
    assert _powershell_lines(steps[guard_name]["run"]) == (
        "$dirty = @(git status --porcelain=v1 -uall)",
        'if ($LASTEXITCODE -ne 0) { throw "Could not inspect the source tree before pack build." }',
        "if ($dirty.Count -ne 0) {",
        'throw "Refusing to build packs from a dirty source tree:`n$($dirty -join "`n")"',
        "}",
    )


def _assert_source_sha_binding(job: dict[str, object]) -> None:
    steps = {step["name"]: step for step in job["steps"]}
    step_names = [step["name"] for step in job["steps"]]
    guard_name = "Assert clean source tree before pack build"
    bind_name = "Bind packs to the checked-out source commit"
    pack_name = "Build and verify signed component packs"
    assert step_names.index(bind_name) == step_names.index(guard_name) + 1
    assert step_names.index(pack_name) == step_names.index(bind_name) + 1

    assert _powershell_lines(steps[bind_name]["run"]) == (
        "$sourceSha = (git rev-parse HEAD).Trim()",
        'if ($LASTEXITCODE -ne 0) { throw "Could not resolve the source commit before pack build." }',
        'if ($sourceSha -notmatch "^[0-9a-f]{40}$") { throw "Resolved source commit is not a lowercase full SHA: $sourceSha" }',
        'if ($sourceSha -ne "${{ github.sha }}") { throw "Resolved source commit $sourceSha does not match GitHub candidate ${{ github.sha }}" }',
        '"SOURCE_SHA=$sourceSha" | Out-File -FilePath $env:GITHUB_ENV -Encoding utf8 -Append',
    )

    pack_build = steps[pack_name]["run"]
    assert pack_build.count("--source-sha $env:SOURCE_SHA `") == 2


def _assert_embedded_closure_smoke_contract(text: str, job: dict[str, object]) -> None:
    steps = job["steps"]
    names = [step["name"] for step in steps]
    pack = next(
        step for step in steps if step["name"] == "Build and verify signed component packs"
    )["run"]
    assert "scripts/build_native_runtime_closure.py" in pack
    assert "scripts/verify_native_runtime_closure.py" in pack
    assert "--gstreamer-closure $gstreamerClosure" in pack
    assert "native-gstreamer-runtime.ccpack" not in text
    smoke_name = "Smoke the installed GStreamer closure through the product worker"
    assert smoke_name in names
    assert names.index(smoke_name) > names.index(
        "Verify the compiled bootstrap trusts the freshly signed packs"
    )
    assert names.index(smoke_name) < names.index(
        "Sign the native bootstrap (Azure Artifact Signing)"
    )
    assert names.index(smoke_name) < names.index("Upload native-beta candidate evidence")
    assert names.index(smoke_name) < names.index("Upload native-beta candidate binaries")


def _assert_msvc_install_path_binding(job: dict[str, object]) -> None:
    assert "CIVICCAST_MSVC_INSTALLATION_PATH" not in job["env"]

    step_names = [step["name"] for step in job["steps"]]
    binding_name = "Bind reviewed MSVC install location"
    provision_name = "Provision the reviewed native build toolchain"
    assert step_names.index(binding_name) < step_names.index(provision_name)

    steps = {step["name"]: step for step in job["steps"]}
    binding = steps[binding_name]
    assert binding["shell"] == "pwsh"
    assert _powershell_lines(binding["run"]) == (
        '$msvcInstall = Join-Path $env:RUNNER_TEMP "civiccast-msvc-build-tools"',
        '"CIVICCAST_MSVC_INSTALLATION_PATH=$msvcInstall" | Out-File -FilePath $env:GITHUB_ENV -Encoding utf8 -Append',
    )


def _assert_dual_lane_scheduling(job: dict[str, object], workflow: dict[str, object]) -> None:
    """Both build_target lanes must be genuinely present, not one silently
    replacing the other. hosted stays the exact original literal (isolated
    per-workflow concurrency group, windows-latest); self-hosted only kicks
    in when workflow_dispatch explicitly asks for it, never unconditionally
    -- a push-triggered release-branch build (no `inputs` at all) must still
    resolve to the hosted literals.
    """
    concurrency = workflow["concurrency"]
    assert concurrency["cancel-in-progress"] == "false"
    group = concurrency["group"]
    assert group.startswith("${{") and group.endswith("}}"), (
        "concurrency.group must stay a workflow_dispatch-input-gated expression, "
        "not a bare literal that can only serve one lane"
    )
    assert "github.event_name == 'workflow_dispatch'" in group
    assert "inputs.build_target == 'self-hosted'" in group
    assert "!inputs.prepare_only" in group
    assert "'sandbox-lab'" in group, "self-hosted lane must share Gate A's own concurrency group"
    assert "'native-beta-candidate-artifacts'" in group, (
        "hosted lane's original per-workflow concurrency group must be unchanged"
    )

    runs_on = job["runs-on"]
    assert isinstance(runs_on, str) and runs_on.startswith("${{") and runs_on.endswith("}}"), (
        "runs-on must stay a workflow_dispatch-input-gated expression, not a bare "
        "literal (string or label list) that can only serve one lane"
    )
    assert "github.event_name == 'workflow_dispatch'" in runs_on
    assert "inputs.build_target == 'self-hosted'" in runs_on
    assert "!inputs.prepare_only" in runs_on
    assert '["self-hosted","windows","sandbox-lab"]' in runs_on, (
        "self-hosted lane must target the same box Gate A runs on"
    )
    assert "'windows-latest'" in runs_on, "hosted lane's original runner must be unchanged"


def test_native_beta_candidate_workflow_builds_signed_artifacts_without_publishing() -> None:
    text, workflow = _workflow()
    triggers = workflow.get("on", workflow.get(True))

    assert set(triggers) == {"push", "workflow_dispatch"}
    assert triggers["push"]["branches"] == ["release/native-beta-1.0.0-beta.1-rc1"]
    job = workflow["jobs"]["build-native-beta"]
    _assert_dual_lane_scheduling(job, workflow)
    # Reusing signed station artifacts from a prior run requires Actions read access.
    assert workflow["permissions"] == {
        "contents": "read",
        "actions": "read",
        "id-token": "write",
    }

    # Was "360" -- GitHub's own default, pinned here only because that is what
    # the workflow happened to say. Two successful candidate builds measured
    # 36m (run 32307731262) and 30m (run 32294680736), so 180 is 5x headroom
    # and 360 was five hours of runway for a wedge. See
    # scripts/policy/check_workflow_timeouts.py, which now enforces the cap
    # across every workflow.
    assert job["timeout-minutes"] == "180"
    assert job["env"]["PACK_PUBLIC_KEY_BASE64"] == "${{ vars.CIVICCAST_PACK_PUBLIC_KEY_BASE64 }}"
    assert job["env"]["PACK_SIGNING_KEY_ID"] == "${{ vars.CIVICCAST_PACK_SIGNING_KEY_ID }}"
    assert "PACK_SIGNING_PRIVATE_KEY" not in job["env"]

    steps = {step["name"]: step for step in job["steps"]}
    pack_step = steps["Build and verify signed component packs"]
    assert pack_step["env"]["PACK_SIGNING_PRIVATE_KEY"] == (
        "${{ secrets.CIVICCAST_PACK_SIGNING_PRIVATE_KEY }}"
    )

    pack_build = pack_step["run"]
    assert "scripts/build_native_app_payload_pack.py" in pack_build
    assert "scripts/build_native_server_pack.py" in pack_build
    assert "scripts/build_native_ffmpeg_pack.py" in pack_build
    assert "scripts/build_native_ollama_pack.py" in pack_build
    assert "scripts/build_native_cuda_pack.py" in pack_build
    assert "--output artifacts/native-beta/packs/native-app-payload.ccpack `" in pack_build
    assert "--output artifacts/native-beta/packs/native-server-binaries.ccpack `" in pack_build
    assert "--output artifacts/native-beta/packs/native-ffmpeg-runtime.ccpack `" in pack_build
    assert "--acquire `" in pack_build
    assert "--cache $ffmpegPackCache `" in pack_build
    assert "--output artifacts/native-beta/packs/native-ollama-runtime.ccpack `" in pack_build
    assert "--cache $ollamaPackCache `" in pack_build
    assert "--output artifacts/native-beta/packs/native-cuda-runtime.ccpack `" in pack_build
    assert "--cache $cudaPackCache `" in pack_build
    assert "--allow-development-key" not in pack_build
    assert "--signing-private-key $keyPath" in pack_build
    assert "--signing-key-id $env:PACK_SIGNING_KEY_ID" in pack_build
    assert pack_build.count("--source-sha $env:SOURCE_SHA `") == 2
    assert "[System.IO.File]::Delete($keyPath)" in pack_build
    assert "${{ secrets.CIVICCAST_PACK_SIGNING_PRIVATE_KEY }}" not in pack_build

    bootstrap = steps["Build the native bootstrap with the release trust root"]["run"]
    assert "scripts/build_native_bootstrap.py" in bootstrap
    assert "--pack-public-key-base64 $env:PACK_PUBLIC_KEY_BASE64" in bootstrap
    assert "--pack-signing-key-id $env:PACK_SIGNING_KEY_ID" in bootstrap

    trust_bridge = steps["Verify the compiled bootstrap trusts the freshly signed packs"]["run"]
    assert '$sideload = "artifacts/native-beta"' in trust_bridge
    assert "Copy-Item" not in trust_bridge
    assert '"--require-component", "native-ffmpeg-runtime"' in trust_bridge
    assert (
        '"$installRoot/dependencies/ffmpeg", "--expected-component", '
        '"native-ffmpeg-runtime"' in trust_bridge
    )
    assert '"--require-component", "native-ollama-runtime"' in trust_bridge
    assert (
        '"$installRoot/dependencies/ollama", "--expected-component", '
        '"native-ollama-runtime"' in trust_bridge
    )
    # native-cuda-runtime is OPTIONAL (native_pack_staging::DEFAULT_OPTIONAL_
    # COMPONENTS), so it is passed with --optional-component, never
    # --require-component -- setup must never be conditioned on obtaining it.
    assert '"--optional-component", "native-cuda-runtime"' in trust_bridge
    assert '"--require-component", "native-cuda-runtime"' not in trust_bridge
    assert (
        '"$installRoot/dependencies/cuda", "--expected-component", '
        '"native-cuda-runtime"' in trust_bridge
    )

    assert "--sign-installed-executables" in bootstrap
    assert bootstrap.index("if ($LASTEXITCODE -ne 0)") < bootstrap.index("$setup = Get-ChildItem")
    installed_signing = steps["Build the native bootstrap with the release trust root"]["env"]
    assert installed_signing["AZURE_CLIENT_SECRET"] == "${{ secrets.AZURE_CLIENT_SECRET }}"
    sign = steps["Sign the native bootstrap (Azure Artifact Signing)"]
    assert sign["uses"] == "azure/artifact-signing-action@v2"
    assert sign["with"]["azure-client-secret"] == "${{ secrets.AZURE_CLIENT_SECRET }}"

    checksums = steps["Verify signed candidate and write checksums"]["run"]
    assert 'Get-Item "artifacts/native-beta/packs/native-ffmpeg-runtime.ccpack"' in checksums
    assert 'Get-Item "artifacts/native-beta/packs/native-ollama-runtime.ccpack"' in checksums
    assert 'Get-Item "artifacts/native-beta/packs/native-cuda-runtime.ccpack"' in checksums
    assert "GetRelativePath($artifactRoot, $asset.FullName)" in checksums

    evidence_upload = steps["Upload native-beta candidate evidence"]
    assert evidence_upload["uses"] == "actions/upload-artifact@v4"
    assert evidence_upload["with"]["name"] == "native-beta-candidate-${{ github.sha }}"
    assert "*-report.json" in evidence_upload["with"]["path"]
    assert "SHA256SUMS.txt" in evidence_upload["with"]["path"]
    assert "candidate-receipt.json" in evidence_upload["with"]["path"]
    assert "CivicCast (Native)_*_x64-setup.exe" not in evidence_upload["with"]["path"]
    assert ".ccpack" not in evidence_upload["with"]["path"]
    # Small evidence is never lane-gated -- it must upload on every build,
    # same reasoning as the tiny station-embed artifact.
    assert "if" not in evidence_upload, "the small evidence artifact must upload on every lane"

    upload = steps["Upload native-beta candidate binaries"]
    assert upload["uses"] == "actions/upload-artifact@v4"
    assert upload["with"]["name"] == "native-beta-candidate-binaries-${{ github.sha }}"
    # Gated the same way as the ~18 GB station bundle and ~21 GB kit: always
    # true on hosted (no local box to read from), skippable on self-hosted
    # by default (owner decision 2026-09-09, shared 10 GB/month Actions
    # artifact-storage cap) unless upload_candidate_binaries forces it.
    assert upload["if"] == (
        "env.BUILD_TARGET != 'self-hosted' || env.UPLOAD_CANDIDATE_BINARIES == 'true'"
    )
    assert "native-app-payload.ccpack" in upload["with"]["path"]
    assert "native-server-binaries.ccpack" in upload["with"]["path"]
    assert "native-ffmpeg-runtime.ccpack" in upload["with"]["path"]
    assert "native-ollama-runtime.ccpack" in upload["with"]["path"]
    assert "native-cuda-runtime.ccpack" in upload["with"]["path"]
    assert "CivicCast (Native)_*_x64-setup.exe" in upload["with"]["path"]
    upload_paths = {line.strip() for line in upload["with"]["path"].splitlines() if line.strip()}
    for component in (
        "native-app-payload",
        "native-server-binaries",
        "native-ffmpeg-runtime",
        "native-ollama-runtime",
    ):
        assert f"artifacts/native-beta/packs/{component}.ccpack" in upload_paths
    # native-cuda-runtime is OPTIONAL at install time (never in the loop
    # above's required-sidecar set) but is still built, signed, checksummed,
    # and uploaded here exactly like every other pack -- a published release
    # needs a real asset for the download-plan catalog to fetch.
    assert "artifacts/native-beta/packs/native-cuda-runtime.ccpack" in upload_paths

    # Artifact reuse may download, but the candidate producer must not publish.
    # The reviewed PyAV wheel is downloaded from its own small provenance asset
    # (release PR #275 added it so CI no longer reads the mutable app-payload
    # pack for it), and that download must stay the ONLY `gh release` verb here.
    reviewed_download = (
        "gh release download $env:REVIEWED_RELEASE --repo $env:GITHUB_REPOSITORY "
        "--pattern $env:REVIEWED_WHEEL_NAME --dir $reuse"
    )
    assert text.count(reviewed_download) == 1
    reuse_env = steps["Reuse the exact pinned PyAV wheel from an existing release"]["env"]
    assert reuse_env["REVIEWED_WHEEL_NAME"] == "av-18.0.0-cp311-abi3-win_amd64.whl"
    assert "gh release" not in text.replace(reviewed_download, "")
    assert "gh api" not in text


def test_native_beta_candidate_workflow_checks_real_pack_sizes_against_addsize() -> None:
    _, workflow = _workflow()
    steps = {step["name"]: step for step in workflow["jobs"]["build-native-beta"]["steps"]}
    check = steps["Verify installer disk estimate covers built sidecars"]["run"]

    for component in (
        "native-app-payload",
        "native-server-binaries",
        "native-ffmpeg-runtime",
        "native-ollama-runtime",
    ):
        assert f'"artifacts/native-beta/{component}-report.json"' in check
    # native-cuda-runtime is OPTIONAL and never staged into $INSTDIR by
    # default (native_pack_staging::DEFAULT_OPTIONAL_COMPONENTS): its bytes
    # must NOT count toward the installer's REQUIRED-sidecar AddSize
    # declaration, which sizes only what a fresh install unconditionally lays
    # down.
    assert "native-cuda-runtime-report.json" not in check
    assert "$report.pack_bytes + [long]$report.payload_bytes" in check
    assert "CIVICCAST_ADDSIZE_PACKS_KB" in check
    assert "$requiredBytes -gt $estimatedBytes" in check
    assert "exceeding installer estimate" in check


def test_native_beta_candidate_workflow_keeps_build_scratch_out_of_the_source_tree() -> None:
    """The pack builder's clean-source guard must see no CI-generated files."""
    text, workflow = _workflow()
    job = workflow["jobs"]["build-native-beta"]
    steps = {step["name"]: step for step in job["steps"]}

    _assert_msvc_install_path_binding(job)

    toolchain = steps["Provision the reviewed native build toolchain"]["run"]
    assert '$toolchainCache = Join-Path $env:RUNNER_TEMP "civiccast-toolchain-cache"' in toolchain
    assert "--cache $toolchainCache" in toolchain
    assert "--output build/wp1-native-toolchain" in toolchain
    assert "--msvc-install $env:CIVICCAST_MSVC_INSTALLATION_PATH" in toolchain
    assert 'Resolve-Path "build/wp1-native-toolchain"' in toolchain
    assert '"node", "uv"' in toolchain

    pack_build = steps["Build and verify signed component packs"]["run"]
    assert '$appPayload = Join-Path $env:RUNNER_TEMP "civiccast-app-payload"' in pack_build
    assert '$appScratch = Join-Path $env:RUNNER_TEMP "civiccast-app-payload-scratch"' in pack_build
    assert (
        '$serverPackCache = Join-Path $env:RUNNER_TEMP "civiccast-server-pack-cache"' in pack_build
    )
    assert (
        '$ffmpegPackCache = Join-Path $env:RUNNER_TEMP "civiccast-ffmpeg-pack-cache"' in pack_build
    )
    assert (
        '$ollamaPackCache = Join-Path $env:RUNNER_TEMP "civiccast-ollama-pack-cache"' in pack_build
    )
    assert "--payload-out $appPayload" in pack_build
    assert "--build-scratch $appScratch" in pack_build
    assert "--interpreter-zip $pythonEmbedZip" in pack_build
    assert "--cache $serverPackCache" in pack_build
    assert "--cache $ffmpegPackCache" in pack_build
    assert "--cache $ollamaPackCache" in pack_build

    msvc_import = steps["Import reviewed MSVC environment for the Tauri build"]["run"]
    assert "$env:CIVICCAST_MSVC_INSTALLATION_PATH/VC/Auxiliary/Build/vcvars64.bat" in msvc_import

    upload_paths = [
        line.strip()
        for step_name in (
            "Upload native-beta candidate evidence",
            "Upload native-beta candidate binaries",
        )
        for line in steps[step_name]["with"]["path"].splitlines()
        if line.strip()
    ]
    assert all(path.startswith("artifacts/native-beta/") for path in upload_paths)

    assert "--allow-dirty-source" not in text


def test_native_beta_candidate_workflow_binds_cpython_producer_to_consumer() -> None:
    _, workflow = _workflow()
    _assert_cpython_handoff(workflow["jobs"]["build-native-beta"])


def test_native_beta_candidate_workflow_has_fail_closed_immediate_pack_guard() -> None:
    _, workflow = _workflow()
    _assert_fail_closed_pack_guard(workflow["jobs"]["build-native-beta"])


def test_native_beta_candidate_workflow_binds_both_source_built_packs_to_github_sha() -> None:
    _, workflow = _workflow()
    _assert_source_sha_binding(workflow["jobs"]["build-native-beta"])


def test_native_beta_candidate_workflow_embeds_verified_closure_and_smokes_before_signing() -> None:
    # tampercheck: allow the builder test while forbidding a standalone GStreamer sidecar
    text, workflow = _workflow()
    _assert_embedded_closure_smoke_contract(text, workflow["jobs"]["build-native-beta"])


def test_native_beta_candidate_workflow_contract_rejects_standalone_gstreamer_pack_or_late_smoke() -> (
    None
):
    text = WORKFLOW.read_text(encoding="utf-8")
    third_pack = text.replace(
        "native-server-binaries.ccpack\n",
        "native-server-binaries.ccpack\n            native-gstreamer-runtime.ccpack\n",
        1,
    )
    with pytest.raises(AssertionError):
        _assert_embedded_closure_smoke_contract(third_pack, _job_from_text(third_pack))
    late_smoke = text.replace(
        "      - name: Smoke the installed GStreamer closure through the product worker\n",
        "      - name: Sign the native bootstrap (Azure Artifact Signing)\n",
        1,
    )
    with pytest.raises(AssertionError):
        _assert_embedded_closure_smoke_contract(late_smoke, _job_from_text(late_smoke))


def test_native_beta_candidate_workflow_contract_rejects_cpython_consumer_drift() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    mutated = text.replace(
        "--interpreter-zip $pythonEmbedZip `",
        "--interpreter-zip python-3.12.10-embed-amd64.zip `",
        1,
    )
    assert mutated != text

    with pytest.raises(AssertionError):
        _assert_cpython_handoff(_job_from_text(mutated))


def test_native_beta_candidate_workflow_contract_rejects_guard_semantic_bypass() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    mutated = text.replace(
        "$dirty = @(git status --porcelain=v1 -uall)",
        "$dirty = @()\n          git status --porcelain=v1 -uall | Out-Null",
        1,
    )
    assert mutated != text

    with pytest.raises(AssertionError):
        _assert_fail_closed_pack_guard(_job_from_text(mutated))


def test_native_beta_candidate_workflow_contract_rejects_source_sha_substitution() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    mutated = text.replace("--source-sha $env:SOURCE_SHA `", "--source-sha deadbeef `", 1)
    assert mutated != text

    with pytest.raises(AssertionError):
        _assert_source_sha_binding(_job_from_text(mutated))


def test_native_beta_candidate_workflow_contract_rejects_job_level_runner_context() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    mutated = text.replace(
        "    env:\n      PACK_PUBLIC_KEY_BASE64:",
        "    env:\n      CIVICCAST_MSVC_INSTALLATION_PATH: ${{ runner.temp }}\\civiccast-msvc-build-tools\n      PACK_PUBLIC_KEY_BASE64:",
        1,
    )
    assert mutated != text

    with pytest.raises(AssertionError):
        _assert_msvc_install_path_binding(_job_from_text(mutated))


def test_native_beta_candidate_workflow_contract_rejects_unconditional_self_hosted_runner() -> None:
    """Regression guard for the build_target: self-hosted lane (HALO box
    keeps the assembled kit local for Gate A instead of a ~21 GB round
    trip): the self-hosted runner labels must never become the ONLY
    destination -- that would break every push-triggered release-branch
    build, which carries no `inputs` at all and has no self-hosted box to
    fall back to.
    """
    text = WORKFLOW.read_text(encoding="utf-8")
    runs_on_expression = (
        "${{ (github.event_name == 'workflow_dispatch' && inputs.build_target == "
        '\'self-hosted\' && !inputs.prepare_only) && fromJSON(\'["self-hosted","windows","sandbox-lab"]\') '
        "|| 'windows-latest' }}"
    )
    assert runs_on_expression in text, "test's expected literal has drifted from the workflow"
    mutated = text.replace(
        f"runs-on: {runs_on_expression}",
        "runs-on: [self-hosted, windows, sandbox-lab]",
        1,
    )
    assert mutated != text

    mutated_workflow = yaml.load(mutated, Loader=yaml.BaseLoader)
    with pytest.raises(AssertionError):
        _assert_dual_lane_scheduling(
            mutated_workflow["jobs"]["build-native-beta"], mutated_workflow
        )


def test_native_beta_default_pack_source_points_to_its_frozen_release_tag() -> None:
    assert "native-beta-1.0.0-beta.1-rc1" in CATALOG.read_text(encoding="utf-8")


def test_native_beta_candidate_workflow_is_a_valid_release_candidate_budget_lane() -> None:
    text, _workflow_data = _workflow()
    assert validate_workflow(WORKFLOW, text) == []


def _assert_reviewed_python_seeds_the_bare_python_command(job: dict[str, object]) -> None:
    """The `python` bare pack-build steps invoke must BE the reviewed
    toolchain interpreter (build_native_app_payload.verify_app_build_toolchain
    hashes sys._base_executable / sys.base_prefix -- a PATH lookup alone
    cannot satisfy that), and it must have this project + its dev-group
    build tools (pefile, packaging, cryptography, ...) installed. A plain
    ``pip install -e .`` into the actions/setup-python bootstrap interpreter
    (interpreter A) can never pass the hash check pinned to the provisioned
    toolchain's interpreter (interpreter B) -- confirmed locally: it fails
    with "python executable SHA-256 <A> != reviewed <B>". uv-syncing a venv
    from the provisioned toolchain python is the same mechanism
    scripts/prove_native_app_reproducible.py already uses successfully.
    """
    step_names = [step["name"] for step in job["steps"]]
    provision_name = "Provision the reviewed native build toolchain"
    bootstrap_name = "Bootstrap the reviewed Python build environment"
    pack_name = "Build and verify signed component packs"
    assert bootstrap_name in step_names
    assert step_names.index(bootstrap_name) == step_names.index(provision_name) + 1
    assert step_names.index(bootstrap_name) < step_names.index(pack_name)

    steps = {step["name"]: step for step in job["steps"]}
    bootstrap = _powershell_lines(steps[bootstrap_name]["run"])
    joined = "\n".join(bootstrap)

    # The pack build's toolchain check resolves the reviewed interpreter from
    # build/wp1-native-toolchain/python -- the venv must be seeded from
    # exactly that path, not some other python.
    assert '$toolchainPython = Join-Path $toolchain "python\\python.exe"' in bootstrap
    assert "--python $toolchainPython" in joined
    # --frozen: use the committed uv.lock as-is, no silent re-resolution.
    # --all-groups: pulls in the dev-group pefile/mypy/etc the pack build
    # scripts import directly (e.g. build_native_runtime_closure.py).
    assert "sync --frozen --all-groups --python $toolchainPython --project ." in joined
    # The venv must land on PATH so every later bare `python -I -B ...`
    # invocation in this job resolves to it, not the setup-python bootstrap.
    assert 'Join-Path $buildVenv "Scripts") | Out-File -FilePath $env:GITHUB_PATH' in joined


def test_native_beta_candidate_workflow_seeds_pack_build_python_from_reviewed_toolchain() -> None:
    _, workflow = _workflow()
    _assert_reviewed_python_seeds_the_bare_python_command(workflow["jobs"]["build-native-beta"])


def test_native_beta_candidate_workflow_contract_rejects_unreviewed_bootstrap_interpreter() -> None:
    """Regression test for the hidden CI defect: the workflow must never go
    back to installing the project into the actions/setup-python bootstrap
    interpreter and relying on it (or a bare PATH-prepend of the provisioned
    python, with no project deps) to run the pack build -- that interpreter
    can never pass verify_app_build_toolchain()'s pinned-hash check.
    """
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "pip install -e ." not in text
    assert "Install packaging dependencies" not in text

    mutated = text.replace(
        '$toolchainPython = Join-Path $toolchain "python\\python.exe"',
        '$toolchainPython = "python.exe"',
        1,
    )
    assert mutated != text
    with pytest.raises(AssertionError):
        _assert_reviewed_python_seeds_the_bare_python_command(_job_from_text(mutated))


def test_native_beta_candidate_workflow_puts_uv_on_path_for_the_closure_walker() -> None:
    """Regression guard for candidate run 31133746679.

    ``scripts/build_native_runtime_closure.py::stage_upstream_wheels`` shells
    out to a BARE ``uv`` (``subprocess.run(["uv", "pip", "install", ...])``),
    and windows-latest images do not carry uv.  The provisioning step used to
    add only node's directory to ``GITHUB_PATH``, so the pack build died with
    ``FileNotFoundError: [WinError 2]`` -- the first candidate build ever to
    get past the missing-pefile wall.

    Asserting the PATH wire-up rather than the absence of the symptom, because
    the symptom only appears on a runner without a stray uv on PATH.
    """

    _, workflow = _workflow()
    steps = {step["name"]: step for step in workflow["jobs"]["build-native-beta"]["steps"]}
    toolchain = steps["Provision the reviewed native build toolchain"]["run"]

    assert "GITHUB_PATH" in toolchain
    for tool in ("node", "uv"):
        assert f'"{tool}"' in toolchain, (
            f"the provisioned {tool} directory must reach GITHUB_PATH; the closure "
            "walker and the Tauri build both invoke their tools bare"
        )
    # Fail loudly at provision time rather than 10 minutes later inside a
    # subprocess with an opaque WinError 2.
    assert "is missing its $tool directory" in toolchain


def test_native_beta_candidate_workflow_installs_the_vc_runtime_before_the_pack_build() -> None:
    """Regression guard for candidate run 31143881561.

    The server pack's live bootstrap proof launches the PACKED PostgreSQL.
    Every pinned PostgreSQL executable imports ``VCRUNTIME140.dll`` (verified by
    a pefile import walk and independently by ``dumpbin /dependents``), and a
    clean ``windows-latest`` runner has no VC++ runtime -- so the proof died
    with exit 3221225781 (``0xC0000135``, ``STATUS_DLL_NOT_FOUND``).

    Developer machines hide this entirely: the Windows system directory already
    holds ``vcruntime140.dll``, so the identical build passes locally. Asserting
    the WORKFLOW ORDERING rather than the absence of the symptom, because the
    symptom is invisible on any machine that happens to have the runtime.

    The product installs this same reviewed redistributable on real stations
    before staging any pack, so this makes CI match the station, not paper over
    a gap.
    """

    _, workflow = _workflow()
    steps = workflow["jobs"]["build-native-beta"]["steps"]
    names = [step["name"] for step in steps]

    install_name = "Install the reviewed VC++ runtime on the runner"
    assert install_name in names, (
        "the VC++ runtime must be installed on the runner; without it the packed "
        "PostgreSQL cannot start and the bootstrap proof fails with 0xC0000135"
    )

    recover_idx = names.index("Recover the exact reviewed VC++ redistributable")
    install_idx = names.index(install_name)
    pack_idx = names.index("Build and verify signed component packs")

    assert recover_idx < install_idx, "cannot install the redistributable before recovering it"
    assert install_idx < pack_idx, (
        "the VC++ runtime must be installed BEFORE the pack build, whose bootstrap "
        "proof launches the packed PostgreSQL"
    )

    install_step = steps[install_idx]["run"]
    assert "$env:VC_REDIST_X64" in install_step, "must install the reviewed, hash-checked binary"
    # Mirrors nsis-hooks-bootstrap.nsh: 1638 means a same-or-newer runtime is
    # already present, which hard-failed a real-hardware run on 2026-08-01
    # before the installer learned to accept it. Do not regress that lesson.
    for code in ("3010", "1638"):
        assert code in install_step, (
            f"exit code {code} must be treated as success, matching the installer's "
            "own hard-won handling in nsis-hooks-bootstrap.nsh"
        )


def _assert_self_hosted_dotnet_sdk_provisioning(job: dict[str, object]) -> None:
    """Regression guard for candidate run 32838619949.

    azure/artifact-signing-action installs its `sign` CLI (net8.0-targeted,
    per nuget.org/packages/sign) via `dotnet tool install`, which needs the
    .NET SDK, not just the runtime. A hosted windows-latest runner ships the
    SDK preinstalled; self-hosted had only the runtime on PATH (`dotnet
    --list-sdks` empty), so the sign step died with "No .NET SDKs were
    found." A self-hosted-only provisioning step installs a pinned SDK via
    dotnet-install.ps1 before the signing step runs.
    """
    step_names = [step["name"] for step in job["steps"]]
    steps = {step["name"]: step for step in job["steps"]}
    provision_name = "Provision a pinned .NET SDK for the signing action (self-hosted only)"
    sign_name = "Sign the native bootstrap (Azure Artifact Signing)"

    assert provision_name in step_names
    assert step_names.index(provision_name) < step_names.index(
        "Build the native bootstrap with the release trust root"
    )
    assert step_names.index(provision_name) < step_names.index(sign_name)

    provision = steps[provision_name]
    assert provision.get("if") == "env.BUILD_TARGET == 'self-hosted'", (
        "hosted runners ship the .NET SDK preinstalled -- this step must never run there"
    )
    assert provision["shell"] == "pwsh"
    joined = "\n".join(_powershell_lines(provision["run"]))

    # Pinned by an exact version, never "latest"/"LTS" (which would drift
    # silently between runs and defeat the whole point of a reviewed pin).
    assert '$dotnetSdkVersion = "8.0.424"' in joined
    assert "-Version $dotnetSdkVersion" in joined
    assert "-Channel" not in joined
    assert '"latest"' not in joined
    assert '"LTS"' not in joined

    # Fetched over TLS from a Microsoft-controlled domain.
    assert "https://dot.net/v1/dotnet-install.ps1" in joined

    # Self-hosted-lane scratch convention: RUNNER_TEMP, same root every
    # other self-hosted-only tool in this job provisions into.
    assert '$dotnetSdkDir = Join-Path $env:RUNNER_TEMP "civiccast-dotnet-sdk"' in joined

    # Idempotent validate-or-reuse: a pre-existing tree is trusted only if
    # `dotnet --list-sdks` actually reports the pinned version (not a
    # marker file alone), matching this workflow's established posture for
    # persistent self-hosted scratch (see "Ensure a clean self-hosted
    # scratch tree before the pack build" / civiccast-msvc-build-tools).
    assert "--list-sdks" in joined
    assert "[regex]::Escape($dotnetSdkVersion)" in joined
    assert "Remove-Item -LiteralPath $dotnetSdkDir -Recurse -Force" in joined

    # The signing action's `dotnet tool install` must resolve this SDK:
    # DOTNET_ROOT plus PATH, exported so the LATER "Sign the native
    # bootstrap" step (a separate process) sees it.
    assert '"DOTNET_ROOT=$dotnetSdkDir" | Out-File -FilePath $env:GITHUB_ENV' in joined
    assert "$dotnetSdkDir | Out-File -FilePath $env:GITHUB_PATH" in joined


def test_native_beta_candidate_workflow_provisions_a_pinned_dotnet_sdk_for_signing() -> None:
    _, workflow = _workflow()
    _assert_self_hosted_dotnet_sdk_provisioning(workflow["jobs"]["build-native-beta"])


def test_native_beta_candidate_workflow_contract_rejects_an_unpinned_dotnet_sdk_version() -> None:
    """A regression that swaps the exact -Version pin for a floating
    -Channel LTS/latest must fail this pin, not silently ship the drift."""
    text = WORKFLOW.read_text(encoding="utf-8")
    mutated = text.replace(
        '$dotnetSdkVersion = "8.0.424"',
        '$dotnetSdkVersion = "latest"',
        1,
    )
    assert mutated != text
    with pytest.raises(AssertionError):
        _assert_self_hosted_dotnet_sdk_provisioning(_job_from_text(mutated))


def _assert_build_native_beta_python_provisioning_needs_no_elevation(
    job: dict[str, object],
) -> None:
    """Regression guard for candidate run 32802054925.

    Both jobs died at their `actions/setup-python@v5` step on the self-hosted
    HALO-gate-a runner: the tool cache was empty, so the action fell through
    to actions/python-versions' SYSTEM-scope install path, which writes
    HKEY_LOCAL_MACHINE\\...\\Uninstall and threw PermissionDenied -- HALO-gate-a
    runs this job as an unelevated interactive user. The hosted lane never
    surfaced this because windows-latest images ship 3.12 warm in the tool
    cache.

    build-native-beta only ever needs a bootstrap interpreter to run
    scripts/provision_native_build_toolchain.py (every later pack-build step
    already runs on the REVIEWED toolchain interpreter via the build-venv --
    see test_native_beta_candidate_workflow_seeds_pack_build_python_from_reviewed_toolchain),
    so the self-hosted lane swaps that one bootstrap for a `uv python install`
    provision, entirely under a caller-owned directory (verified locally:
    no registry writes, no admin prompt) -- put on GITHUB_PATH exactly like
    every other tool this job provisions.
    """

    step_names = [step["name"] for step in job["steps"]]
    steps = {step["name"]: step for step in job["steps"]}

    hosted_name = "Set up Python (hosted)"
    install_uv_name = "Install uv (self-hosted Python bootstrap)"
    self_hosted_name = "Set up Python (self-hosted, zero-elevation via uv)"
    provision_name = "Provision the reviewed native build toolchain"

    assert hosted_name in step_names
    assert install_uv_name in step_names
    assert self_hosted_name in step_names

    # All three provisioning steps happen before the toolchain step consumes
    # `python`, and in the order the self-hosted lane needs (uv itself before
    # asking it to install a Python).
    assert step_names.index(hosted_name) < step_names.index(provision_name)
    assert step_names.index(install_uv_name) < step_names.index(self_hosted_name)
    assert step_names.index(self_hosted_name) < step_names.index(provision_name)

    hosted = steps[hosted_name]
    assert hosted.get("if") == "env.BUILD_TARGET != 'self-hosted'", (
        "hosted lane must stay conditional and unchanged -- it is the exact "
        "original actions/setup-python step"
    )
    assert hosted["uses"] == "actions/setup-python@v5"
    assert hosted["with"]["python-version"] == "3.12"

    install_uv = steps[install_uv_name]
    assert install_uv.get("if") == "env.BUILD_TARGET == 'self-hosted'"
    assert install_uv["uses"] == "astral-sh/setup-uv@v8.1.0"
    assert install_uv["with"]["enable-cache"] == "true"

    self_hosted = steps[self_hosted_name]
    assert self_hosted.get("if") == "env.BUILD_TARGET == 'self-hosted'"
    assert self_hosted["shell"] == "pwsh"
    self_hosted_lines = _powershell_lines(self_hosted["run"])
    joined = "\n".join(self_hosted_lines)
    assert "uv python install 3.12" in joined
    assert "(uv python find 3.12).Trim()" in joined
    # The provisioned interpreter must reach GITHUB_PATH -- the immediately
    # following "Provision the reviewed native build toolchain" step invokes
    # a BARE `python`, not `uv run python`, so PATH is the only handoff.
    assert "Out-File -FilePath $env:GITHUB_PATH -Encoding utf8 -Append" in joined
    # Refuse to silently swallow a failed provision or an unresolved
    # interpreter -- both must throw, not fall through to a stray PATH python.
    assert (
        'throw "uv could not provision a self-hosted-lane Python 3.12 bootstrap interpreter."'
        in joined
    )
    assert (
        'throw "uv could not resolve the provisioned Python 3.12 bootstrap interpreter."' in joined
    )


def test_native_beta_candidate_workflow_self_hosted_python_needs_no_elevation() -> None:
    _, workflow = _workflow()
    _assert_build_native_beta_python_provisioning_needs_no_elevation(
        workflow["jobs"]["build-native-beta"]
    )


def test_native_beta_candidate_workflow_contract_rejects_elevated_self_hosted_python() -> None:
    """A regression that puts actions/setup-python back unconditionally on
    the self-hosted lane (or drops the uv-based bootstrap entirely) must fail
    this pin, not silently ship the HKLM-writing defect run 32802054925 hit.
    """
    text = WORKFLOW.read_text(encoding="utf-8")
    mutated = text.replace(
        "      - name: Set up Python (hosted)\n"
        "        if: env.BUILD_TARGET != 'self-hosted'\n"
        "        uses: actions/setup-python@v5\n"
        '        with:\n          python-version: "3.12"\n',
        "      - name: Set up Python (hosted)\n"
        "        uses: actions/setup-python@v5\n"
        '        with:\n          python-version: "3.12"\n',
        1,
    )
    assert mutated != text
    mutated_job = _job_from_text(mutated)
    with pytest.raises(AssertionError):
        _assert_build_native_beta_python_provisioning_needs_no_elevation(mutated_job)


def _assert_build_native_station_bundle_python_provisioning_needs_no_elevation(
    job: dict[str, object],
) -> None:
    """Same regression guard as build-native-beta's, for the second job that
    died on the same candidate run (32802054925) at the same step shape.

    This job never invokes a bare `python` -- only `uv run` / `uv sync` -- so
    the self-hosted lane wires the uv-provisioned interpreter in via
    UV_PYTHON / UV_PYTHON_INSTALL_DIR (persisted through GITHUB_ENV) rather
    than GITHUB_PATH, and every later `uv sync`/`uv run` step picks it up
    with no further changes.
    """

    step_names = [step["name"] for step in job["steps"]]
    steps = {step["name"]: step for step in job["steps"]}

    install_uv_name = "Install uv"
    hosted_name = "Set up Python 3.12 (hosted)"
    self_hosted_name = "Set up Python 3.12 (self-hosted, zero-elevation via uv)"
    install_project_name = "Install project (captions-runtime extra)"

    assert install_uv_name in step_names
    assert hosted_name in step_names
    assert self_hosted_name in step_names

    assert step_names.index(install_uv_name) < step_names.index(hosted_name)
    assert step_names.index(install_uv_name) < step_names.index(self_hosted_name)
    assert step_names.index(hosted_name) < step_names.index(install_project_name)
    assert step_names.index(self_hosted_name) < step_names.index(install_project_name)

    install_uv = steps[install_uv_name]
    assert "if" not in install_uv, "uv install must run unconditionally on every lane"
    assert install_uv["uses"] == "astral-sh/setup-uv@v8.1.0"

    hosted = steps[hosted_name]
    assert hosted.get("if") == "env.BUILD_TARGET != 'self-hosted'"
    assert hosted["uses"] == "actions/setup-python@v5"
    assert hosted["with"]["python-version"] == "3.12"

    self_hosted = steps[self_hosted_name]
    assert self_hosted.get("if") == "env.BUILD_TARGET == 'self-hosted'"
    assert self_hosted["shell"] == "pwsh"
    joined = "\n".join(_powershell_lines(self_hosted["run"]))
    assert "uv python install 3.12" in joined
    assert 'throw "uv could not provision a self-hosted-lane Python 3.12 interpreter."' in joined
    assert (
        '"UV_PYTHON_INSTALL_DIR=$uvPythonInstallDir" | Out-File -FilePath $env:GITHUB_ENV' in joined
    )
    assert '"UV_PYTHON=3.12" | Out-File -FilePath $env:GITHUB_ENV' in joined

    install_project = steps[install_project_name]
    assert "if" not in install_project, "uv sync must run unconditionally on every lane"
    assert install_project["run"] == "uv sync --frozen --extra captions-runtime", (
        "the invocation itself must stay byte-identical across lanes -- lane "
        "selection lives entirely in the UV_PYTHON env var set above, not in "
        "this command"
    )


def test_native_beta_candidate_workflow_station_bundle_python_needs_no_elevation() -> None:
    _, workflow = _workflow()
    _assert_build_native_station_bundle_python_provisioning_needs_no_elevation(
        workflow["jobs"]["build-native-station-bundle"]
    )


def test_native_beta_candidate_workflow_contract_rejects_elevated_station_bundle_python() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    mutated = text.replace(
        "      - name: Set up Python 3.12 (hosted)\n"
        "        if: env.BUILD_TARGET != 'self-hosted'\n"
        "        uses: actions/setup-python@v5\n"
        '        with:\n          python-version: "3.12"\n',
        "      - name: Set up Python 3.12 (hosted)\n"
        "        uses: actions/setup-python@v5\n"
        '        with:\n          python-version: "3.12"\n',
        1,
    )
    assert mutated != text
    mutated_workflow = yaml.load(mutated, Loader=yaml.BaseLoader)
    with pytest.raises(AssertionError):
        _assert_build_native_station_bundle_python_provisioning_needs_no_elevation(
            mutated_workflow["jobs"]["build-native-station-bundle"]
        )


def test_prepare_only_preserves_signed_candidate_and_small_station_evidence() -> None:
    _, workflow = _workflow()
    dispatch_inputs = workflow["on"]["workflow_dispatch"]["inputs"]
    prepare_only = dispatch_inputs["prepare_only"]
    assert prepare_only["type"] == "boolean"
    assert prepare_only["default"] == "false"

    station_job = workflow["jobs"]["build-native-station-bundle"]
    station_steps = {step["name"]: step for step in station_job["steps"]}
    assert station_steps["Verify the signed station bundle and write checksums"]
    assert "Verify the Beta 11 preparation evidence is complete" in station_steps
    preparation_verify = station_steps["Verify the Beta 11 preparation evidence is complete"]
    assert preparation_verify["if"] == "env.PREPARE_ONLY == 'true'"
    assert "Test-Path -LiteralPath $path -PathType Leaf" in preparation_verify["run"]
    assert "captions-whistle.ccpack" in preparation_verify["run"]
    assert "station-index.json" in preparation_verify["run"]
    assert "SHA256SUMS.txt" in preparation_verify["run"]
    assert "native-station-bundle-build-report.json" in preparation_verify["run"]
    prep_upload_index = next(
        index
        for index, step in enumerate(station_job["steps"])
        if step["name"] == "Upload the Beta 11 preparation evidence"
    )
    assert station_job["steps"].index(preparation_verify) < prep_upload_index
    assert station_steps["Upload the installer-embedded station resources"]
    prep_evidence = station_steps["Upload the Beta 11 preparation evidence"]
    assert prep_evidence["if"] == "env.PREPARE_ONLY == 'true'"
    assert prep_evidence["with"]["name"] == "native-station-prepare-${{ github.sha }}"
    assert prep_evidence["with"]["retention-days"] == "1"
    assert set(prep_evidence["with"]["path"].splitlines()) == {
        "artifacts/native-station-bundle/station/captions-whistle.ccpack",
        "artifacts/native-station-bundle/station/station-index.json",
        "artifacts/native-station-bundle/station/SHA256SUMS.txt",
        "artifacts/native-station-bundle/native-station-bundle-build-report.json",
    }
    assert (
        "env.PREPARE_ONLY != 'true'"
        in station_steps["Upload the native station bundle artifact"]["if"]
    )
    candidate_steps = {
        step["name"]: step for step in workflow["jobs"]["build-native-beta"]["steps"]
    }
    assert candidate_steps["Upload native-beta candidate binaries"]["with"]["name"] == (
        "native-beta-candidate-binaries-${{ github.sha }}"
    )

    assemble_job = workflow["jobs"]["assemble-native-beta-kit"]
    assert assemble_job["if"] == (
        "${{ !(github.event_name == 'workflow_dispatch' && inputs.prepare_only) }}"
    )
    assert "native-beta-kit-${{ github.sha }}" in str(assemble_job["steps"])

    # Even an invalid prepare_only + self-hosted dispatch is rejected on a
    # hosted runner; the active desktop must never be eligible for that job.
    for job_name in ("build-native-beta", "build-native-station-bundle"):
        assert "inputs.prepare_only" in workflow["jobs"][job_name]["runs-on"]
        assert "!inputs.prepare_only" in workflow["jobs"][job_name]["runs-on"]

    concurrency_group = workflow["concurrency"]["group"]
    assert "!inputs.prepare_only" in concurrency_group


def test_prepare_only_artifact_is_the_gate_a_skip_marker() -> None:
    _, workflow = _workflow()
    station_job = workflow["jobs"]["build-native-station-bundle"]
    steps = {step["name"]: step for step in station_job["steps"]}
    prep_artifact = steps["Upload the Beta 11 preparation evidence"]
    assert prep_artifact["with"]["name"] == "native-station-prepare-${{ github.sha }}"
    assert prep_artifact["if"] == "env.PREPARE_ONLY == 'true'"
    assert prep_artifact["with"]["retention-days"] == "1"


def test_native_beta_candidate_workflow_has_a_manual_render_job_for_exact_source() -> None:
    _, workflow = _workflow()
    manual = workflow["jobs"]["render-native-manual"]
    assert manual["runs-on"] == "ubuntu-latest"
    assert manual["timeout-minutes"] == "40"
    steps = {step["name"]: step for step in manual["steps"]}
    assert steps["Checkout exact candidate"]["with"]["ref"] == "${{ github.sha }}"
    assert "texlive-xetex" in steps["Install pandoc and TeX (per ADR 0005)"]["run"]
    assert (
        "OUT_DIR=$PWD/artifacts/release-preparation/manual"
        in steps["Render USER-MANUAL and check currentness"]["run"]
    )
    assert steps["Create exact-candidate manual receipt"]["run"].count("github.sha") == 1
    assert "github.run_id" in steps["Create exact-candidate manual receipt"]["run"]

    upload = steps["Upload exact-candidate manual artifact"]
    assert upload["with"]["name"] == "native-beta-manual-${{ github.sha }}"
    assert upload["with"]["retention-days"] == "1"
    assert set(upload["with"]["path"].splitlines()) == {
        "artifacts/release-preparation/manual/USER-MANUAL.pdf",
        "artifacts/release-preparation/manual/USER-MANUAL.docx",
        "artifacts/release-preparation/manual/USER-MANUAL.render.json",
        "artifacts/release-preparation/manual/candidate-manual-receipt.json",
    }

    assemble = workflow["jobs"]["assemble-native-beta-kit"]
    assert "render-native-manual" in assemble["needs"]
    assemble_steps = {step["name"]: step for step in assemble["steps"]}
    download = assemble_steps["Download exact-candidate manual artifact"]
    assert download["with"]["name"] == "native-beta-manual-${{ github.sha }}"
    assert download["with"]["path"] == "manual-artifact"
    colocate = assemble_steps["Co-locate the installer and station bundle into one kit"]["run"]
    for contract in (
        '$receipt.source_sha -ne "${{ github.sha }}"',
        '$receipt.workflow_run_id -ne "${{ github.run_id }}"',
        "$receipt.render_manifest_sha256 -ne $manifestHash",
        "Copy-Item -Path (Join-Path $manualSource '*') -Destination $kitManual",
        'Join-Path $kit "manual"',
    ):
        assert contract in colocate
    assert (
        assemble_steps["Upload the installable native-beta kit"]["with"]["path"].strip() == "kit/**"
    )


def _assert_kit_carries_the_quickstart_card(job: dict[str, object]) -> None:
    """The field-tested first-run gap: a station volunteer who unboxes the USB
    kit has no plain-language walkthrough, only the installer screens and
    (if setup fails) the operator console's own error copy. docs/QUICKSTART-
    OPERATOR.md fixes that, but only if it actually reaches the stick --
    this pins its presence in the assembled kit, not just in the repo.
    """
    step_names = [step["name"] for step in job["steps"]]
    checkout_name = "Checkout exact candidate"
    colocate_name = "Co-locate the installer and station bundle into one kit"
    assert checkout_name in step_names, (
        "the assemble job has no repo checkout; docs/QUICKSTART-OPERATOR.md "
        "cannot be read from anywhere without one"
    )
    assert step_names.index(checkout_name) < step_names.index(colocate_name)

    steps = {step["name"]: step for step in job["steps"]}
    colocate = steps[colocate_name]["run"]
    assert "Test-Path -LiteralPath $quickstartSource" in colocate
    assert '$quickstartSource = "docs/QUICKSTART-OPERATOR.md"' in colocate
    assert (
        "Copy-Item -LiteralPath $quickstartSource -Destination "
        '(Join-Path $kit "QUICKSTART-OPERATOR.md")' in colocate
    )
    assert "Kit assembly: QUICKSTART-OPERATOR.md is not co-located at the kit root" in colocate

    upload = steps["Upload the installable native-beta kit"]
    # kit/** is a recursive glob over the SAME $kit root the co-locate step
    # writes QUICKSTART-OPERATOR.md into, so the upload step needs no
    # separate path entry -- but this pins the invariant that makes that
    # true, so a future path narrowing here cannot silently drop the card.
    assert upload["with"]["path"].strip() == "kit/**"


def test_native_beta_candidate_workflow_kit_assembly_carries_the_quickstart_card() -> None:
    _, workflow = _workflow()
    _assert_kit_carries_the_quickstart_card(workflow["jobs"]["assemble-native-beta-kit"])


def test_native_beta_candidate_workflow_contract_rejects_a_kit_missing_the_quickstart_card() -> (
    None
):
    text = WORKFLOW.read_text(encoding="utf-8")
    mutated = text.replace(
        '$quickstartSource = "docs/QUICKSTART-OPERATOR.md"\n'
        "          if (-not (Test-Path -LiteralPath $quickstartSource)) {\n"
        '            throw "Kit assembly: $quickstartSource not found in the checked-out source tree."\n'
        "          }\n"
        "          Copy-Item -LiteralPath $quickstartSource -Destination "
        '(Join-Path $kit "QUICKSTART-OPERATOR.md")\n'
        '          $quickstartPlaced = Join-Path $kit "QUICKSTART-OPERATOR.md"\n'
        "          if (-not (Test-Path -LiteralPath $quickstartPlaced)) {\n"
        '            throw "Kit assembly: QUICKSTART-OPERATOR.md is not co-located at the kit root next to the installer."\n'
        "          }\n\n",
        "",
        1,
    )
    assert mutated != text, "test's expected literal has drifted from the workflow"

    mutated_workflow = yaml.load(mutated, Loader=yaml.BaseLoader)
    with pytest.raises(AssertionError):
        _assert_kit_carries_the_quickstart_card(
            mutated_workflow["jobs"]["assemble-native-beta-kit"]
        )


# ---------------------------------------------------------------------------
# Setup.exe embeds the signed station index, tiny `core` pack, and mandatory
# Whistle pack (tauri.native.conf.json bundle.resources). The Whistle pack is
# the only new station payload needed to upgrade from a Beta10 cache; all large
# model packs remain outside setup.exe. These resources have their own bounded
# artifact so the installer job never downloads the full station bundle.
# ---------------------------------------------------------------------------


def _assert_embedded_station_handoff(workflow: dict[str, object]) -> None:
    producer = workflow["jobs"]["build-native-station-bundle"]
    consumer = workflow["jobs"]["build-native-beta"]

    needs = consumer["needs"]
    needs = [needs] if isinstance(needs, str) else list(needs)
    assert "build-native-station-bundle" in needs, (
        "build-native-beta embeds files build-native-station-bundle produces; "
        "without needs:, the two jobs race and the installer builds against "
        "whatever happens to be on disk"
    )

    producer_steps = {step["name"]: step for step in producer["steps"]}
    stage = producer_steps["Stage the installer-embedded station resources"]["run"]
    assert '@("station-index.json", "core.ccpack", "captions-whistle.ccpack")' in stage
    assert "the installer cannot embed it" in stage, (
        "the producer must fail closed with a message naming the consumer's need"
    )

    embed_upload = producer_steps["Upload the installer-embedded station resources"]
    assert embed_upload["uses"] == "actions/upload-artifact@v4"
    assert embed_upload["with"]["name"] == "native-station-embed-${{ github.sha }}"
    assert embed_upload["with"]["if-no-files-found"] == "error"
    assert embed_upload["with"]["retention-days"] == "1"
    # Unconditional on EVERY lane, unlike the ~18.6 GB full-bundle upload
    # below it: the hosted lane's build-native-beta has no local mirror to
    # read, so an `if:` here would break the hosted lane outright.
    assert "if" not in embed_upload, (
        "the small embed artifact must upload on every lane -- the hosted "
        "consumer has no local mirror fallback"
    )

    full_upload = producer_steps["Upload the native station bundle artifact"]
    assert "if" in full_upload, "the ~18.6 GB bundle upload must stay lane-gated"

    consumer_steps = {step["name"]: step for step in consumer["steps"]}
    download = consumer_steps["Download the embedded station index and station packs (hosted)"]
    assert download["uses"] == "actions/download-artifact@v4"
    assert download["with"]["name"] == "native-station-embed-${{ github.sha }}"
    assert download["if"] == (
        "env.BUILD_TARGET != 'self-hosted' && inputs.reuse_station_run_id == ''"
    ), (
        "self-hosted reads build-native-station-bundle's local mirror instead "
        "of downloading; downloading there is the transfer cost the "
        "build_target input exists to avoid"
    )
    assert download["with"]["name"] != "native-station-bundle-${{ github.sha }}", (
        "must not pull the full station-bundle artifact to obtain embedded resources"
    )
    reused = consumer_steps["Download the previously signed unchanged station resources"]
    assert reused["if"] == "inputs.reuse_station_run_id != ''"
    assert reused["uses"] == "actions/download-artifact@v4"
    assert reused["with"]["name"] == ("native-station-embed-${{ inputs.reuse_station_source_sha }}")
    assert reused["with"]["run-id"] == "${{ inputs.reuse_station_run_id }}"
    assert reused["with"]["path"] == download["with"]["path"]

    staging_name = "Stage embedded station resources for the Tauri build"
    staging = consumer_steps[staging_name]["run"]
    # Same mirror, located the same defensive way, as assemble-native-beta-kit.
    assert 'Join-Path $env:CANDIDATE_ROOT "station-bundle"' in staging
    assert '@("station-index.json", "core.ccpack", "captions-whistle.ccpack")' in staging
    assert '$destination = "civiccast/apps/installer/src-tauri/resources/station"' in staging, (
        "must stage at the exact bundle.resources source path tauri.native.conf.json declares"
    )
    assert "throw" in staging, "the staging step must fail closed on a missing input"

    step_names = [step["name"] for step in consumer["steps"]]
    bootstrap_name = "Build the native bootstrap with the release trust root"
    assert step_names.index(download["name"]) < step_names.index(staging_name)
    assert step_names.index(staging_name) < step_names.index(bootstrap_name), (
        "the resources must be on disk before the Tauri build reads them"
    )
    # The clean-source guard runs earlier and is pinned as a consecutive
    # triple by _assert_fail_closed_pack_guard; staging must stay after it
    # (resources/ is gitignored, but ordering keeps that fact non-load-bearing).
    assert step_names.index("Assert clean source tree before pack build") < step_names.index(
        staging_name
    )


def test_native_beta_candidate_workflow_embeds_required_whistle_from_its_producer() -> None:
    _, workflow = _workflow()
    _assert_embedded_station_handoff(workflow)


def test_native_beta_candidate_workflow_contract_rejects_an_unordered_station_embed() -> None:
    """Dropping the `needs:` edge must fail this contract, not pass quietly:
    without it the installer job can start before the station bundle exists
    and build a setup.exe with no embedded index at all."""
    text = WORKFLOW.read_text(encoding="utf-8")
    mutated = text.replace("    needs: build-native-station-bundle\n", "", 1)
    assert mutated != text, "test's expected literal has drifted from the workflow"

    mutated_workflow = yaml.load(mutated, Loader=yaml.BaseLoader)
    with pytest.raises((AssertionError, KeyError)):
        _assert_embedded_station_handoff(mutated_workflow)


def test_native_beta_candidate_workflow_contract_rejects_a_full_bundle_download_for_two_files() -> (
    None
):
    """The cheap-fetch invariant, mutation-tested: repointing the consumer at
    the ~18.6 GB full-bundle artifact must fail, not merely be slower."""
    text = WORKFLOW.read_text(encoding="utf-8")
    mutated = text.replace(
        "          name: native-station-embed-${{ github.sha }}\n          path: station-embed\n",
        "          name: native-station-bundle-${{ github.sha }}\n          path: station-embed\n",
        1,
    )
    assert mutated != text, "test's expected literal has drifted from the workflow"

    mutated_workflow = yaml.load(mutated, Loader=yaml.BaseLoader)
    with pytest.raises(AssertionError):
        _assert_embedded_station_handoff(mutated_workflow)


def test_native_beta_candidate_workflow_kit_assembly_keeps_station_and_manual_inputs() -> None:
    r"""The USB kit is the air-gapped station's whole world: the embed must be
    additive. assemble-native-beta-kit must still depend on the installer and
    station jobs, add the exact-run manual artifact, and co-locate the FULL
    station bundle (index + every component pack) at .\station\ next to setup.exe."""
    _, workflow = _workflow()
    kit = workflow["jobs"]["assemble-native-beta-kit"]

    assert sorted(kit["needs"]) == [
        "build-native-beta",
        "build-native-station-bundle",
        "render-native-manual",
    ]
    colocate = {step["name"]: step for step in kit["steps"]}[
        "Co-locate the installer and station bundle into one kit"
    ]["run"]
    assert "Copy-Item -Path (Join-Path $bundleDir '*') -Destination $kitStation -Recurse" in (
        colocate
    ), "the kit must still carry the WHOLE signed bundle, not just embedded setup resources"
    assert "Kit assembly: the co-located station bundle carries no component packs" in colocate


# ---------------------------------------------------------------------------
# PR #124 fixed the build job's prune to keep the pinned previous-candidate
# kit named by sandbox-lab/upgrade-baseline.json -- the cross-version upgrade
# lane installs that kit from C:\CivicCastTester\kit-staging\<its source_sha>
# and fails closed when it is absent (it was emptied three times on
# 2026-09-02). feat/installer-embeds-station-index then added a SECOND
# self-hosted prune, in build-native-station-bundle, which now runs first --
# and its first draft carried a hand-copied keep-only-this-sha list that would
# have deleted the pinned kit before the fixed prune ever ran. A reviewer
# caught it. These tests exist so the next copy cannot.
# ---------------------------------------------------------------------------

PRUNE_SCRIPT = Path("scripts/ci/prune_local_candidate_roots.ps1")
PRUNE_STEP_NAME = "Prune stale local candidate builds (self-hosted only, keep this sha)"
PRUNING_JOBS = ("build-native-beta", "build-native-station-bundle")
PRUNE_INVOCATION = (
    "          pwsh -NoProfile -File scripts/ci/prune_local_candidate_roots.ps1"
    ' -CurrentSha "${{ github.sha }}"\n'
    '          if ($LASTEXITCODE -ne 0) { throw "Local candidate prune failed'
    ' (exit $LASTEXITCODE)." }\n'
)


def _prune_steps(workflow: dict[str, object]) -> dict[str, dict[str, object]]:
    found: dict[str, dict[str, object]] = {}
    for job_name in PRUNING_JOBS:
        steps = [
            step for step in workflow["jobs"][job_name]["steps"] if step["name"] == PRUNE_STEP_NAME
        ]
        assert len(steps) == 1, (
            f"{job_name} must carry exactly one prune step named {PRUNE_STEP_NAME!r}, "
            f"found {len(steps)}"
        )
        found[job_name] = steps[0]
    return found


def _assert_every_prune_keeps_the_pinned_baseline(workflow: dict[str, object]) -> None:
    script = PRUNE_SCRIPT.read_text(encoding="utf-8")
    # The keep-list lives in ONE place, and that place reads the baseline.
    assert "sandbox-lab/upgrade-baseline.json" in script, (
        "the shared prune must read the pinned previous-candidate baseline; without it "
        "the cross-version upgrade lane installs from an empty kit-staging and fails closed"
    )
    assert "source_sha" in script
    assert "$keep" in script

    for job_name, step in _prune_steps(workflow).items():
        run = step["run"]
        assert step["if"] == "env.BUILD_TARGET == 'self-hosted'", (
            f"{job_name}'s prune must stay self-hosted-only -- a hosted runner has no "
            "C:\\CivicCastTester roots to prune"
        )
        # Every prune step reaches the baseline through the shared script.
        assert "scripts/ci/prune_local_candidate_roots.ps1" in run, (
            f"{job_name}'s prune must call the shared script, not re-implement the keep-list"
        )
        assert '-CurrentSha "${{ github.sha }}"' in run
        assert "Local candidate prune failed" in run, (
            f"{job_name}'s prune must fail the job when the script fails, not continue on a "
            "silently unpruned box"
        )
        # And must NOT carry its own copy of the deletion logic. This is the
        # assertion that actually catches the regression: an inline
        # Remove-Item loop here is a second keep-list by definition.
        assert "Remove-Item" not in run, (
            f"{job_name}'s prune must not delete anything inline -- deletion belongs to the "
            "shared script so the #124 keep-list cannot be forked"
        )
        assert "-ne $currentSha" not in run, (
            f"{job_name}'s prune carries a keep-only-this-sha filter, which is exactly the "
            "PR #124 regression: it deletes the pinned previous-candidate kit"
        )


def test_native_beta_candidate_workflow_prunes_never_delete_the_pinned_baseline_kit() -> None:
    _, workflow = _workflow()
    _assert_every_prune_keeps_the_pinned_baseline(workflow)


def test_native_beta_candidate_workflow_contract_rejects_an_inline_keep_only_this_sha_prune() -> (
    None
):
    """Mutation guard: reinstating the hand-copied keep-only-this-sha prune in
    the station-bundle job must fail this contract."""
    text = WORKFLOW.read_text(encoding="utf-8")
    inline = (
        '          $currentSha = "${{ github.sha }}"\n'
        '          foreach ($root in @("C:\\CivicCastTester\\candidates")) {\n'
        "            Get-ChildItem -LiteralPath $root -Directory |\n"
        "              Where-Object { $_.Name -ne $currentSha } |\n"
        "              ForEach-Object { Remove-Item -LiteralPath $_.FullName -Recurse -Force }\n"
        "          }\n"
    )
    assert text.count(PRUNE_INVOCATION) == 2, (
        "test's expected literal has drifted from the workflow"
    )
    # Mutate the SECOND occurrence (build-native-station-bundle's).
    head, _, tail = text.rpartition(PRUNE_INVOCATION)
    mutated = head + inline + tail

    mutated_workflow = yaml.load(mutated, Loader=yaml.BaseLoader)
    with pytest.raises(AssertionError):
        _assert_every_prune_keeps_the_pinned_baseline(mutated_workflow)


def _run_prune(*arguments: str) -> subprocess.CompletedProcess[str]:
    pwsh = shutil.which("pwsh")
    if pwsh is None:
        pytest.skip("pwsh is not available on this host; the prune script cannot be executed")
    return subprocess.run(
        [pwsh, "-NoProfile", "-File", str(Path.cwd() / PRUNE_SCRIPT), *arguments],
        check=False,
        capture_output=True,
        text=True,
    )


def test_prune_script_keeps_the_current_and_baseline_shas_and_deletes_the_rest(
    tmp_path: Path,
) -> None:
    """Behavioural, not textual: actually RUN the shared prune against a
    scratch tree. The #124 regression was a behaviour (the pinned kit got
    deleted), and only executing the keep-list proves it does not recur."""
    current = "a" * 40
    baseline_sha = "b" * 40
    stale = "c" * 40

    baseline = tmp_path / "upgrade-baseline.json"
    baseline.write_text(
        json.dumps({"source_sha": baseline_sha, "candidate_label": "candidate-test"}),
        encoding="utf-8",
    )

    roots = []
    for root_name in ("candidates", "kit-staging"):
        root = tmp_path / root_name
        for sha in (current, baseline_sha, stale):
            (root / sha).mkdir(parents=True)
            (root / sha / "marker.txt").write_text(sha, encoding="utf-8")
        roots.append(root)

    completed = _run_prune(
        "-CurrentSha",
        current,
        "-BaselinePath",
        str(baseline),
        "-Roots",
        f"{roots[0]};{roots[1]}",
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr
    for root in roots:
        assert (root / current).is_dir(), "the sha being built must survive"
        assert (root / baseline_sha).is_dir(), (
            "the pinned previous-candidate kit must survive -- deleting it is the PR #124 "
            f"regression; script output:\n{completed.stdout}"
        )
        assert not (root / stale).exists(), "a stale sha must be reclaimed"
    assert baseline_sha in completed.stdout


def test_prune_script_keeps_only_the_current_sha_when_the_baseline_is_unreadable(
    tmp_path: Path,
) -> None:
    """Fail-safe direction: an absent or malformed baseline must prune LESS,
    never more, and must never abort the build job."""
    current = "a" * 40
    stale = "c" * 40
    root = tmp_path / "kit-staging"
    for sha in (current, stale):
        (root / sha).mkdir(parents=True)

    malformed = tmp_path / "upgrade-baseline.json"
    malformed.write_text("{not json", encoding="utf-8")

    for baseline_path in (malformed, tmp_path / "absent.json"):
        completed = _run_prune(
            "-CurrentSha", current, "-BaselinePath", str(baseline_path), "-Roots", str(root)
        )
        assert completed.returncode == 0, completed.stdout + completed.stderr
        assert (root / current).is_dir()

    assert not (root / stale).exists()


def test_prune_script_refuses_a_current_sha_that_is_not_a_sha(tmp_path: Path) -> None:
    """`-CurrentSha` is interpolated from ${{ github.sha }}. Validate it rather
    than trusting it: a non-sha keep-list would prune every real directory."""
    root = tmp_path / "kit-staging"
    (root / ("a" * 40)).mkdir(parents=True)

    completed = _run_prune("-CurrentSha", "not-a-sha", "-Roots", str(root))

    assert completed.returncode != 0
    assert (root / ("a" * 40)).is_dir(), "a refused prune must delete nothing"


def test_local_station_reuse_skips_acquisition_and_keeps_existing_verification() -> None:
    _, workflow = _workflow()
    job = workflow["jobs"]["build-native-station-bundle"]
    steps = {step["name"]: step for step in job["steps"]}
    assert job["env"]["REUSE_STATION_DIR"] == "${{ inputs.reuse_station_dir || '' }}"
    for name in [
        "Provision the pinned Ollama model exports (summary + translation)",
        "Provision the pinned captions-floor bundle root",
        "Provision the pinned Whistle model and engine root",
        "Free the raw provisioned model roots before upload",
    ]:
        assert steps[name]["if"] == "env.REUSE_STATION_DIR == ''"
    assert "--reuse-station-dir" in steps["Build and sign the native station bundle"]["run"]
    assert "@reuseArgs" in steps["Build and sign the native station bundle"]["run"]
    assert (
        "reuse_station_dir requires a self-hosted complete candidate build"
        in steps["Resolve build target and local candidate root"]["run"]
    )
    assert "if" not in steps["Verify the signed station bundle and write checksums"]


def test_candidate_reuses_reviewed_local_pyav_without_release_publication() -> None:
    _, workflow = _workflow()
    steps = {step["name"]: step for step in workflow["jobs"]["build-native-beta"]["steps"]}
    reuse = steps["Reuse the exact pinned PyAV wheel from an existing release"]
    assert (
        reuse["if"] == "inputs.reviewed_pyav_release_tag != '' || env.BUILD_TARGET == 'self-hosted'"
    )
    assert "New-Item -ItemType Directory -Force -Path $reuse" in reuse["run"]
    assert "if ($env:REVIEWED_RELEASE)" in reuse["run"]
    assert "runtime-artifact-mirror" in reuse["run"]
    assert "Copy-Item -LiteralPath $mirrorWheel -Destination $reuse" in reuse["run"]
    assert "EXPECTED_WHEEL_BYTES" in reuse["run"] and "EXPECTED_WHEEL_SHA256" in reuse["run"]
    pack = steps["Build and verify signed component packs"]["run"]
    assert (
        'if ($env:BUILD_TARGET -eq "self-hosted" -and !$env:CIVICCAST_REVIEWED_PYAV_WHEEL)' in pack
    )
