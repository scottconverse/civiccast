# SPDX-License-Identifier: Apache-2.0
# Copyright (c) The CivicCast Authors
"""Contract tests for the reviewed native-Windows runtime dependency closure."""

from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import re
import sys
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
LOCK_PATH = ROOT / "native-windows-runtime-dependencies.lock.json"
PROVISIONER_PATH = ROOT / "scripts" / "provision_native_runtime_dependencies.py"
BUILD_TOOLCHAIN_LOCK_PATH = ROOT / "native-windows-build-toolchain.lock.json"
ARTIFACT_NAMES = {"postgres", "tsduck", "ffmpeg", "node", "ollama"}
EXPECTED_VERSIONS = {
    "postgres": "17.11-2",
    "tsduck": "3.44-4676",
    "ffmpeg": "N-128142-g28fda48e0b",
    "node": "24.16.0",
    "ollama": "0.40.13",
}
EXPECTED_SPDX_LICENSES = {
    "postgres": "PostgreSQL",
    "tsduck": "BSD-2-Clause",
    "ffmpeg": "LGPL-3.0-or-later",
    "node": "MIT",
    "ollama": "MIT",
}
POSTGRES_INCLUDE = [
    "bin/**",
    "lib/**",
    "share/**",
    "commandlinetools_3rd_party_licenses.txt",
    "server_license.txt",
]
NODE_INCLUDE = ["node.exe", "LICENSE"]
EXPECTED_EXECUTABLES = {
    "postgres": [
        "bin/initdb.exe",
        "bin/pg_ctl.exe",
        "bin/pg_dump.exe",
        "bin/pg_restore.exe",
        "bin/postgres.exe",
        "bin/psql.exe",
    ],
    "tsduck": ["bin/tsp.exe"],
    "ffmpeg": ["bin/ffmpeg.exe", "bin/ffprobe.exe"],
    "node": ["node.exe"],
    "ollama": ["ollama.exe"],
}
FIXTURE_OLLAMA_LICENSE = b"fixture Ollama license"


def _load() -> object:
    assert PROVISIONER_PATH.is_file(), (
        f"planned runtime dependency provisioner is missing: {PROVISIONER_PATH}"
    )
    spec = importlib.util.spec_from_file_location(
        "provision_native_runtime_dependencies",
        PROVISIONER_PATH,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def provisioner() -> object:
    return _load()


class _Response(io.BytesIO):
    def __init__(self, body: bytes, final_url: str) -> None:
        super().__init__(body)
        self._final_url = final_url

    def geturl(self) -> str:
        return self._final_url

    def __enter__(self) -> _Response:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()


def _artifact(name: str, body: bytes) -> dict[str, object]:
    artifact: dict[str, object] = {
        "version": EXPECTED_VERSIONS[name],
        "url": f"https://github.com/civiccast-fixtures/{name}.zip",
        "filename": f"{name}.zip",
        "bytes": len(body),
        "sha256": hashlib.sha256(body).hexdigest(),
        "spdx_license": EXPECTED_SPDX_LICENSES[name],
        "archive": "zip",
        "strip_prefix": "." if name == "ollama" else name,
        "expected_executables": EXPECTED_EXECUTABLES[name],
    }
    if name == "postgres":
        artifact["include"] = POSTGRES_INCLUDE
    elif name == "node":
        artifact["include"] = NODE_INCLUDE
    elif name == "ollama":
        artifact["license_notice"] = {
            "bytes": len(FIXTURE_OLLAMA_LICENSE),
            "filename": "ollama-LICENSE-v0.40.13.txt",
            "sha256": hashlib.sha256(FIXTURE_OLLAMA_LICENSE).hexdigest(),
            "url": "https://raw.githubusercontent.com/ollama/ollama/v0.40.13/LICENSE",
        }
    return artifact


def _zip_bytes(members: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, body in members.items():
            archive.writestr(name, body)
    return buffer.getvalue()


def _write_lock(path: Path, artifacts: dict[str, dict[str, object]]) -> None:
    path.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "target": "windows-x86_64",
                "artifacts": artifacts,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )


def test_runtime_dependency_provisioner_module_exists() -> None:
    assert PROVISIONER_PATH.is_file(), (
        "create scripts/provision_native_runtime_dependencies.py before "
        "implementing the runtime dependency closure"
    )


def test_committed_runtime_lock_is_complete_and_reviewable(provisioner: object) -> None:
    lock = provisioner.load_lock()

    assert provisioner.LOCK_PATH == LOCK_PATH
    assert lock["schema_version"] == 2
    assert lock["target"] == "windows-x86_64"
    assert set(lock["artifacts"]) == ARTIFACT_NAMES

    for name, artifact in lock["artifacts"].items():
        assert artifact["version"] == EXPECTED_VERSIONS[name]
        assert artifact["url"].startswith("https://")
        assert provisioner.is_approved_download_url(artifact["url"])
        assert artifact["filename"] == Path(artifact["filename"]).name
        assert artifact["filename"]
        assert isinstance(artifact["bytes"], int) and artifact["bytes"] > 0
        assert re.fullmatch(r"[0-9a-f]{64}", artifact["sha256"])
        assert artifact["spdx_license"] == EXPECTED_SPDX_LICENSES[name]
        assert re.fullmatch(r"[A-Za-z0-9.-]+", artifact["spdx_license"])
        assert not re.search(r"(^|[^A-Z])A?GPL(?:-|$)", artifact["spdx_license"])
        assert artifact["archive"] == "zip"
        assert isinstance(artifact["strip_prefix"], str) and artifact["strip_prefix"]
        assert artifact["expected_executables"] == EXPECTED_EXECUTABLES[name]
        if name == "postgres":
            assert artifact["include"] == POSTGRES_INCLUDE
        elif name == "node":
            assert artifact["include"] == NODE_INCLUDE
        else:
            assert "include" not in artifact
        if name == "ollama":
            assert artifact["strip_prefix"] == "."
            assert artifact["license_notice"] == {
                "bytes": 1058,
                "filename": "ollama-LICENSE-v0.40.13.txt",
                "sha256": "5934ed2ce0d15154bcdb9c85203210abac0da4314af34081e36df4599f90b226",
                "url": "https://raw.githubusercontent.com/ollama/ollama/v0.40.13/LICENSE",
            }
            assert provisioner.is_approved_download_url(artifact["license_notice"]["url"])
        else:
            assert "license_notice" not in artifact

    provisioner.validate_lock(lock)


def test_runtime_node_identity_matches_the_reviewed_build_toolchain(
    provisioner: object,
) -> None:
    runtime_node = provisioner.load_lock()["artifacts"]["node"]
    build_node = json.loads(BUILD_TOOLCHAIN_LOCK_PATH.read_text(encoding="utf-8"))["artifacts"][
        "node"
    ]

    for field in ("bytes", "filename", "sha256", "strip_prefix", "url", "version"):
        assert runtime_node[field] == build_node[field]


@pytest.mark.parametrize(
    ("field", "value", "match"),
    [
        ("schema_version", 1, "schema"),
        ("target", "linux-x86_64", "target"),
    ],
)
def test_validate_lock_rejects_wrong_root_contract(
    provisioner: object,
    field: str,
    value: object,
    match: str,
) -> None:
    lock = provisioner.load_lock()
    lock[field] = value

    with pytest.raises(provisioner.RuntimeDependencyProvisionError, match=match):
        provisioner.validate_lock(lock)


@pytest.mark.parametrize(
    ("field", "value", "match"),
    [
        ("url", "http://github.com/fixture.zip", "HTTPS"),
        ("url", "https://attacker.invalid/fixture.zip", "approved"),
        ("filename", "../fixture.zip", "filename"),
        ("bytes", 0, "size"),
        ("sha256", "A" * 64, "SHA-256"),
        ("spdx_license", "GPL-3.0-only", "license"),
        ("spdx_license", "LicenseRef-Unreviewed-ThirdParty", "license"),
        ("spdx_license", "ISC", "license"),
        ("archive", "tar.gz", "archive"),
        ("strip_prefix", "", "strip"),
        ("expected_executables", ["../escape.exe"], "executables"),
    ],
)
def test_validate_lock_rejects_unreviewable_artifact_metadata(
    provisioner: object,
    field: str,
    value: object,
    match: str,
) -> None:
    lock = provisioner.load_lock()
    lock["artifacts"]["postgres"][field] = value

    with pytest.raises(provisioner.RuntimeDependencyProvisionError, match=match):
        provisioner.validate_lock(lock)


def test_fetch_verifies_size_and_digest_before_cache_admission(
    provisioner: object,
    tmp_path: Path,
) -> None:
    body = b"reviewed runtime artifact"
    artifact = _artifact("tsduck", body)
    calls: list[str] = []

    def opener(request: object, *, timeout: float) -> _Response:
        calls.append(request.full_url)  # type: ignore[attr-defined]
        assert timeout == 60
        return _Response(body, "https://github.com/civiccast-fixtures/tsduck.zip")

    cached = provisioner.fetch_locked_artifact("tsduck", artifact, tmp_path, opener=opener)
    assert cached == tmp_path / "tsduck.zip"
    assert cached.read_bytes() == body
    assert calls == ["https://github.com/civiccast-fixtures/tsduck.zip"]
    assert not (tmp_path / "tsduck.zip.partial").exists()

    cached.write_bytes(b"tampered")
    with pytest.raises(provisioner.RuntimeDependencyProvisionError, match=r"SHA-256|size"):
        provisioner.fetch_locked_artifact("tsduck", artifact, tmp_path, offline=True)


def test_fetch_rejects_bad_download_and_removes_partial_file(
    provisioner: object,
    tmp_path: Path,
) -> None:
    body = b"truncated"
    artifact = _artifact("tsduck", body + b" artifact")

    def opener(_request: object, *, timeout: float) -> _Response:
        assert timeout == 60
        return _Response(body, "https://github.com/civiccast-fixtures/tsduck.zip")

    with pytest.raises(provisioner.RuntimeDependencyProvisionError, match=r"SHA-256|size"):
        provisioner.fetch_locked_artifact("tsduck", artifact, tmp_path, opener=opener)
    assert not (tmp_path / "tsduck.zip").exists()
    assert not (tmp_path / "tsduck.zip.partial").exists()


def test_fetch_offline_refuses_a_missing_cache_entry(
    provisioner: object,
    tmp_path: Path,
) -> None:
    with pytest.raises(provisioner.RuntimeDependencyProvisionError, match="offline"):
        provisioner.fetch_locked_artifact(
            "tsduck",
            _artifact("tsduck", b"reviewed runtime artifact"),
            tmp_path,
            offline=True,
            opener=lambda *_args, **_kwargs: pytest.fail("offline must not open a URL"),
        )


def test_fetch_prefers_a_verified_mirror_entry_before_any_network(
    provisioner: object,
    tmp_path: Path,
) -> None:
    """The cache-first path: a hash-verified mirror hit must satisfy the
    acquisition with ZERO network use, even though the upstream URL may no
    longer exist (the BtbN autobuild-pruning failure mode)."""

    body = b"reviewed runtime artifact"
    artifact = _artifact("tsduck", body)
    mirror = tmp_path / "mirror"
    entry = mirror / str(artifact["sha256"]) / "tsduck.zip"
    entry.parent.mkdir(parents=True)
    entry.write_bytes(body)
    cache = tmp_path / "cache"

    cached = provisioner.fetch_locked_artifact(
        "tsduck",
        artifact,
        cache,
        mirror=mirror,
        opener=lambda *_args, **_kwargs: pytest.fail("a mirror hit must not open a URL"),
    )
    assert cached == cache / "tsduck.zip"
    assert cached.read_bytes() == body
    assert entry.read_bytes() == body
    assert not (cache / "tsduck.zip.partial").exists()


def test_fetch_mirror_is_configurable_by_environment_variable(
    provisioner: object,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    body = b"reviewed runtime artifact"
    artifact = _artifact("tsduck", body)
    mirror = tmp_path / "mirror"
    entry = mirror / str(artifact["sha256"]) / "tsduck.zip"
    entry.parent.mkdir(parents=True)
    entry.write_bytes(body)
    monkeypatch.setenv(provisioner.MIRROR_ENV_VAR, str(mirror))

    cached = provisioner.fetch_locked_artifact(
        "tsduck",
        artifact,
        tmp_path / "cache",
        opener=lambda *_args, **_kwargs: pytest.fail("a mirror hit must not open a URL"),
    )
    assert cached.read_bytes() == body


def test_fetch_ignores_a_corrupt_mirror_entry_then_repairs_it_from_the_network(
    provisioner: object,
    tmp_path: Path,
) -> None:
    body = b"reviewed runtime artifact"
    artifact = _artifact("tsduck", body)
    mirror = tmp_path / "mirror"
    entry = mirror / str(artifact["sha256"]) / "tsduck.zip"
    entry.parent.mkdir(parents=True)
    entry.write_bytes(b"tampered mirror bytes")

    def opener(_request: object, *, timeout: float) -> _Response:
        assert timeout == 60
        return _Response(body, "https://github.com/civiccast-fixtures/tsduck.zip")

    cached = provisioner.fetch_locked_artifact(
        "tsduck", artifact, tmp_path / "cache", opener=opener, mirror=mirror
    )
    assert cached.read_bytes() == body
    assert entry.read_bytes() == body  # repaired from the verified download
    assert not entry.with_name("tsduck.zip.partial").exists()


def test_fetch_admits_a_verified_download_into_the_mirror(
    provisioner: object,
    tmp_path: Path,
) -> None:
    body = b"reviewed runtime artifact"
    artifact = _artifact("tsduck", body)
    mirror = tmp_path / "mirror"

    def opener(_request: object, *, timeout: float) -> _Response:
        assert timeout == 60
        return _Response(body, "https://github.com/civiccast-fixtures/tsduck.zip")

    cached = provisioner.fetch_locked_artifact(
        "tsduck", artifact, tmp_path / "cache", opener=opener, mirror=mirror
    )
    assert cached.read_bytes() == body
    entry = mirror / str(artifact["sha256"]) / "tsduck.zip"
    assert entry.read_bytes() == body


def test_fetch_mirror_write_failure_never_fails_a_verified_acquisition(
    provisioner: object,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    body = b"reviewed runtime artifact"
    artifact = _artifact("tsduck", body)
    mirror = tmp_path / "mirror"
    mirror.write_bytes(b"a file where the mirror directory should be")

    def opener(_request: object, *, timeout: float) -> _Response:
        assert timeout == 60
        return _Response(body, "https://github.com/civiccast-fixtures/tsduck.zip")

    cached = provisioner.fetch_locked_artifact(
        "tsduck", artifact, tmp_path / "cache", opener=opener, mirror=mirror
    )
    assert cached.read_bytes() == body
    assert "could not admit" in capsys.readouterr().err


def test_fetch_falls_back_to_the_next_reviewed_url_when_the_primary_fails(
    provisioner: object,
    tmp_path: Path,
) -> None:
    body = b"reviewed runtime artifact"
    artifact = _artifact("tsduck", body)
    artifact["fallback_urls"] = ["https://github.com/civiccast-fixtures/mirror/tsduck.zip"]
    calls: list[str] = []

    def opener(request: object, *, timeout: float) -> _Response:
        assert timeout == 60
        url = request.full_url  # type: ignore[attr-defined]
        calls.append(url)
        if url == "https://github.com/civiccast-fixtures/tsduck.zip":
            raise OSError("HTTP Error 404: Not Found (upstream release pruned)")
        return _Response(body, url)

    cached = provisioner.fetch_locked_artifact("tsduck", artifact, tmp_path, opener=opener)
    assert cached.read_bytes() == body
    assert calls == [
        "https://github.com/civiccast-fixtures/tsduck.zip",
        "https://github.com/civiccast-fixtures/mirror/tsduck.zip",
    ]
    assert not (tmp_path / "tsduck.zip.partial").exists()


def test_fetch_reports_every_reviewed_source_when_all_of_them_fail(
    provisioner: object,
    tmp_path: Path,
) -> None:
    artifact = _artifact("tsduck", b"reviewed runtime artifact")
    artifact["fallback_urls"] = ["https://github.com/civiccast-fixtures/mirror/tsduck.zip"]

    def opener(request: object, *, timeout: float) -> _Response:
        assert timeout == 60
        raise OSError(f"HTTP Error 404: Not Found for {request.full_url}")  # type: ignore[attr-defined]

    with pytest.raises(
        provisioner.RuntimeDependencyProvisionError, match="every reviewed source"
    ) as excinfo:
        provisioner.fetch_locked_artifact("tsduck", artifact, tmp_path, opener=opener)
    assert "civiccast-fixtures/tsduck.zip" in str(excinfo.value)
    assert "civiccast-fixtures/mirror/tsduck.zip" in str(excinfo.value)
    assert not (tmp_path / "tsduck.zip").exists()
    assert not (tmp_path / "tsduck.zip.partial").exists()


def test_validate_lock_accepts_reviewed_fallback_urls(provisioner: object) -> None:
    lock = provisioner.load_lock()
    lock["artifacts"]["ffmpeg"]["fallback_urls"] = [
        "https://github.com/civiccast-fixtures/mirror/ffmpeg.zip"
    ]
    provisioner.validate_lock(lock)


@pytest.mark.parametrize(
    ("value", "match"),
    [
        ([], "non-empty"),
        ("https://github.com/one.zip", "non-empty"),
        ([42], "non-empty"),
        (["http://github.com/one.zip"], "HTTPS"),
        (["https://attacker.invalid/one.zip"], "approved"),
        (
            [
                "https://github.com/civiccast-fixtures/one.zip",
                "https://github.com/civiccast-fixtures/one.zip",
            ],
            "duplicates",
        ),
    ],
)
def test_validate_lock_rejects_unreviewable_fallback_urls(
    provisioner: object,
    value: object,
    match: str,
) -> None:
    lock = provisioner.load_lock()
    lock["artifacts"]["ffmpeg"]["fallback_urls"] = value

    with pytest.raises(provisioner.RuntimeDependencyProvisionError, match=match):
        provisioner.validate_lock(lock)


@pytest.mark.parametrize(
    "member",
    [
        "sample/../../escape.txt",
        "/absolute.txt",
        "C:/drive-path.txt",
    ],
)
def test_safe_extract_rejects_traversal_and_absolute_member_paths(
    provisioner: object,
    tmp_path: Path,
    member: str,
) -> None:
    archive = tmp_path / "unsafe.zip"
    archive.write_bytes(_zip_bytes({member: b"escape"}))

    with pytest.raises(provisioner.RuntimeDependencyProvisionError, match="unsafe"):
        provisioner.safe_extract_zip(archive, tmp_path / "out", strip_prefix="sample")
    assert not (tmp_path / "escape.txt").exists()


def test_safe_extract_rejects_normalized_case_insensitive_collisions(
    provisioner: object,
    tmp_path: Path,
) -> None:
    archive = tmp_path / "collision.zip"
    archive.write_bytes(
        _zip_bytes({"sample/bin/SAMPLE-SERVER.EXE": b"one", "sample/bin/sample-server.exe": b"two"})
    )

    with pytest.raises(provisioner.RuntimeDependencyProvisionError, match="collision"):
        provisioner.safe_extract_zip(archive, tmp_path / "out", strip_prefix="sample")


def test_safe_extract_rejects_exact_duplicate_members(
    provisioner: object,
    tmp_path: Path,
) -> None:
    archive = tmp_path / "duplicate.zip"
    with zipfile.ZipFile(archive, "w") as handle:
        handle.writestr("sample/bin/same.dll", b"first")
        with pytest.warns(UserWarning, match="Duplicate name"):
            handle.writestr("sample/bin/same.dll", b"second")

    with pytest.raises(provisioner.RuntimeDependencyProvisionError, match="duplicate"):
        provisioner.safe_extract_zip(archive, tmp_path / "out", strip_prefix="sample")
    assert not (tmp_path / "out").exists()


@pytest.mark.parametrize(
    "member",
    [
        "sample/bin/CON",
        "sample/bin/runner.",
        "sample/bin/runner ",
        "sample/bin/runner.exe:stream",
        "sample/bin/runner?.exe",
    ],
)
def test_safe_extract_rejects_windows_namespace_hazards(
    provisioner: object,
    tmp_path: Path,
    member: str,
) -> None:
    archive = tmp_path / "windows-unsafe.zip"
    archive.write_bytes(_zip_bytes({member: b"unsafe"}))

    with pytest.raises(provisioner.RuntimeDependencyProvisionError, match="unsafe Windows"):
        provisioner.safe_extract_zip(archive, tmp_path / "out", strip_prefix="sample")


def test_safe_extract_rejects_symlink_like_members(
    provisioner: object,
    tmp_path: Path,
) -> None:
    archive = tmp_path / "symlink.zip"
    with zipfile.ZipFile(archive, "w") as handle:
        link = zipfile.ZipInfo("sample/bin/sample-server.exe")
        link.create_system = 3
        link.external_attr = 0o120777 << 16
        handle.writestr(link, b"target")

    with pytest.raises(provisioner.RuntimeDependencyProvisionError, match="symlink"):
        provisioner.safe_extract_zip(archive, tmp_path / "out", strip_prefix="sample")


def test_safe_extract_applies_only_the_reviewed_prefix(
    provisioner: object,
    tmp_path: Path,
) -> None:
    archive = tmp_path / "sample.zip"
    archive.write_bytes(
        _zip_bytes({"sample/sample-server.exe": b"server", "sample/LICENSE": b"license"})
    )

    provisioner.safe_extract_zip(archive, tmp_path / "out", strip_prefix="sample")

    assert (tmp_path / "out" / "sample-server.exe").read_bytes() == b"server"
    assert (tmp_path / "out" / "LICENSE").read_bytes() == b"license"
    assert not (tmp_path / "out" / "sample").exists()


def test_safe_extract_supports_a_reviewed_root_level_archive(
    provisioner: object,
    tmp_path: Path,
) -> None:
    archive = tmp_path / "ollama.zip"
    archive.write_bytes(
        _zip_bytes(
            {
                "ollama.exe": b"portable runtime",
                "lib/ollama/cpu/ggml-cpu.dll": b"cpu backend",
                "LICENSE": b"license",
            }
        )
    )

    provisioner.safe_extract_zip(archive, tmp_path / "out", strip_prefix=".")

    assert (tmp_path / "out" / "ollama.exe").read_bytes() == b"portable runtime"
    assert (tmp_path / "out" / "lib" / "ollama" / "cpu" / "ggml-cpu.dll").is_file()


def _runtime_archive_members(name: str) -> dict[str, bytes]:
    members = {f"{name}/{path}": f"{name}:{path}".encode() for path in EXPECTED_EXECUTABLES[name]}
    if name == "postgres":
        members.update(
            {
                "postgres/lib/libpq.dll": b"libpq",
                "postgres/share/postgresql.conf.sample": b"sample",
                "postgres/server_license.txt": b"postgres license",
                "postgres/commandlinetools_3rd_party_licenses.txt": b"third party",
            }
        )
    elif name == "node":
        members[f"{name}/LICENSE"] = b"node license"
        members[f"{name}/unreviewed.txt"] = b"excluded"
    elif name == "ollama":
        members = {
            "ollama.exe": b"ollama runtime",
            "lib/ollama/cpu/ggml-cpu.dll": b"cpu backend",
        }
    else:
        members[f"{name}/LICENSE.txt"] = f"{name} license".encode()
        members[f"{name}/__pycache__/ignored.pyc"] = b"ignored bytecode"
        members[f"{name}/scratch.tmp"] = b"ignored temporary output"
    return members


def _prepare_runtime_lock_and_cache(tmp_path: Path) -> tuple[Path, Path]:
    cache = tmp_path / "cache"
    cache.mkdir()
    artifacts: dict[str, dict[str, object]] = {}
    for name in sorted(ARTIFACT_NAMES):
        body = _zip_bytes(_runtime_archive_members(name))
        artifact = _artifact(name, body)
        (cache / str(artifact["filename"])).write_bytes(body)
        if name == "ollama":
            notice = artifact["license_notice"]
            (cache / str(notice["filename"])).write_bytes(FIXTURE_OLLAMA_LICENSE)
        artifacts[name] = artifact
    lock_path = tmp_path / "native-windows-runtime-dependencies.lock.json"
    _write_lock(lock_path, artifacts)
    return lock_path, cache


def test_stage_dependencies_produces_the_exact_runtime_roots_and_manifest(
    provisioner: object,
    tmp_path: Path,
) -> None:
    lock_path, cache = _prepare_runtime_lock_and_cache(tmp_path)
    output = tmp_path / "runtime"

    manifest_path = provisioner.stage_dependencies(lock_path, cache, output, offline=True)

    assert manifest_path == output / "native-runtime-dependencies-manifest.json"
    assert {path.name for path in output.iterdir()} == {
        "postgresql",
        "tsduck",
        "ffmpeg",
        "node",
        "ollama",
        "native-runtime-dependencies-manifest.json",
        "SHA256SUMS",
        "LICENSE-BOM.md",
    }
    for name, root in {
        "postgres": "postgresql",
        "tsduck": "tsduck",
        "ffmpeg": "ffmpeg",
        "node": "node",
        "ollama": "ollama",
    }.items():
        assert all(
            (output / root / executable).is_file() for executable in EXPECTED_EXECUTABLES[name]
        )
        assert any("license" in path.name.lower() for path in (output / root).rglob("*"))
    assert (output / "ollama" / "LICENSE").read_bytes() == FIXTURE_OLLAMA_LICENSE

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["lock_sha256"] == hashlib.sha256(lock_path.read_bytes()).hexdigest()
    files = manifest["files"]
    assert files == sorted(files, key=lambda entry: entry["path"])
    assert all(
        set(entry)
        == {
            "path",
            "size",
            "sha256",
            "component",
            "version",
            "license",
        }
        for entry in files
    )
    assert all(entry["license"] in EXPECTED_SPDX_LICENSES.values() for entry in files)
    assert (output / "SHA256SUMS").read_text(encoding="utf-8") == "".join(
        f"{entry['sha256']}  {entry['path']}\n" for entry in files
    )
    bom = (output / "LICENSE-BOM.md").read_text(encoding="utf-8")
    for name, artifact in manifest["artifacts"].items():
        assert f"| {name} | {artifact['version']} | {artifact['spdx_license']} |" in bom
    assert manifest["sha256_to_paths"] == {
        digest: sorted(entry["path"] for entry in files if entry["sha256"] == digest)
        for digest in sorted({entry["sha256"] for entry in files})
    }
    assert not any(
        "__pycache__" in entry["path"].split("/")
        or entry["path"].endswith((".pyc", ".pyo", ".tmp", "~"))
        for entry in files
    )


def test_stage_refuses_missing_required_runtime_files(
    provisioner: object,
    tmp_path: Path,
) -> None:
    lock_path, cache = _prepare_runtime_lock_and_cache(tmp_path)
    name = "postgres"
    members = {"postgres/LICENSE.txt": b"license"}
    body = _zip_bytes(members)
    artifact = json.loads(lock_path.read_text(encoding="utf-8"))["artifacts"][name]
    artifact["bytes"] = len(body)
    artifact["sha256"] = hashlib.sha256(body).hexdigest()
    (cache / artifact["filename"]).write_bytes(body)
    lock = json.loads(lock_path.read_text(encoding="utf-8"))
    lock["artifacts"][name] = artifact
    lock_path.write_text(json.dumps(lock, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    with pytest.raises(
        provisioner.RuntimeDependencyProvisionError,
        match=r"initdb\.exe",
    ):
        provisioner.stage_dependencies(lock_path, cache, tmp_path / "runtime", offline=True)


def test_stage_uses_the_reviewed_include_allowlist(
    provisioner: object,
    tmp_path: Path,
) -> None:
    lock_path, cache = _prepare_runtime_lock_and_cache(tmp_path)
    lock = json.loads(lock_path.read_text(encoding="utf-8"))
    artifact = lock["artifacts"]["postgres"]
    members = _runtime_archive_members("postgres")
    members["postgres/docs/unreviewed.txt"] = b"excluded by the reviewed allowlist"
    body = _zip_bytes(members)
    artifact["bytes"] = len(body)
    artifact["sha256"] = hashlib.sha256(body).hexdigest()
    (cache / artifact["filename"]).write_bytes(body)
    lock_path.write_text(json.dumps(lock, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    provisioner.stage_dependencies(lock_path, cache, tmp_path / "runtime", offline=True)

    assert not (tmp_path / "runtime" / "postgresql" / "docs").exists()


@pytest.mark.parametrize("trust_artifact", ["SHA256SUMS", "LICENSE-BOM.md"])
def test_runtime_dependency_verifier_rejects_tampered_trust_artifacts(
    provisioner: object,
    tmp_path: Path,
    trust_artifact: str,
) -> None:
    lock_path, cache = _prepare_runtime_lock_and_cache(tmp_path)
    output = tmp_path / "runtime"
    provisioner.stage_dependencies(lock_path, cache, output, offline=True)
    (output / trust_artifact).write_text("forged\n", encoding="utf-8")

    with pytest.raises(
        provisioner.RuntimeDependencyProvisionError,
        match=trust_artifact,
    ):
        provisioner.verify_staged_dependencies(output, lock_path=lock_path)
