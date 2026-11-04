import hashlib
import json
import zipfile

import pytest

from scripts import native_glib_soup_overlay as overlay


@pytest.fixture
def runtime_archive(tmp_path, monkeypatch):
    archive = tmp_path / "reviewed.zip"
    payload = {"glib-2.0-0.dll": b"reviewed runtime", "provenance.json": b"reviewed provenance"}

    def pin(members=None):
        with zipfile.ZipFile(archive, "w") as bundle:
            for name, data in (members or payload).items():
                bundle.writestr(name, data)
        policy = {
            "archive": {
                "bytes": archive.stat().st_size,
                "sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
            },
            "files": {
                name: {"bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
                for name, data in payload.items()
            },
        }
        path = tmp_path / "policy.json"
        path.write_text(json.dumps(policy))
        monkeypatch.setattr(overlay, "LOCK_PATH", path)

    pin()
    monkeypatch.setenv("CIVICCAST_GLIB_SOUP_RUNTIME_ZIP", str(archive))
    return archive, payload, pin


def test_verified_runtime_cache_is_rechecked(runtime_archive, tmp_path):
    root = overlay.acquire_runtime(tmp_path / "cache")
    assert (root / "glib-2.0-0.dll").read_bytes() == b"reviewed runtime"
    # An altered cache is never used: it is rebuilt from the reviewed archive.
    (root / "glib-2.0-0.dll").write_bytes(b"altered")
    assert overlay.acquire_runtime(tmp_path / "cache") == root
    assert (root / "glib-2.0-0.dll").read_bytes() == b"reviewed runtime"
    # ...and with no verifiable archive, an altered cache still stops the build.
    archive, _, _ = runtime_archive
    (root / "glib-2.0-0.dll").write_bytes(b"altered")
    with archive.open("ab") as stream:
        stream.write(b"altered")
    with pytest.raises(ValueError, match="unverified"):
        overlay.acquire_runtime(tmp_path / "cache")


def test_archive_outer_hash_is_required(runtime_archive, tmp_path):
    archive, _, _ = runtime_archive
    with archive.open("ab") as stream:
        stream.write(b"altered")
    with pytest.raises(ValueError, match="unverified"):
        overlay.acquire_runtime(tmp_path / "cache")


def test_pinned_archive_must_match_independent_file_inventory(runtime_archive, tmp_path):
    _, payload, pin = runtime_archive
    pin({**payload, "extra.dll": b"unexpected"})
    with pytest.raises(ValueError, match="unexpected"):
        overlay.acquire_runtime(tmp_path / "cache")
    assert not list((tmp_path / "cache").glob(".glib-soup-*"))


def test_individual_file_pins_are_required(runtime_archive, tmp_path):
    archive, payload, _ = runtime_archive
    with zipfile.ZipFile(archive, "w") as bundle:
        for name, data in payload.items():
            bundle.writestr(name, b"tampered" if name.endswith(".dll") else data)
    policy = overlay.policy()
    policy["archive"] = {
        "bytes": archive.stat().st_size,
        "sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
    }
    overlay.LOCK_PATH.write_text(json.dumps(policy))
    with pytest.raises(ValueError, match="unverified"):
        overlay.acquire_runtime(tmp_path / "cache")


def test_installed_overlay_is_verified_without_acquisition(runtime_archive, tmp_path, monkeypatch):
    _, payload, _ = runtime_archive
    root = tmp_path / "installed"
    for name, data in payload.items():
        path = root / (f"bin/{name}" if name.endswith(".dll") else f"licenses/glib-soup/{name}")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    def refuse_acquisition(*args):
        pytest.fail("installed verification must never acquire a runtime")

    monkeypatch.setattr(overlay, "acquire_runtime", refuse_acquisition)
    overlay.verify_overlay(root)
    (root / "licenses/glib-soup/provenance.json").write_bytes(b"rewritten provenance")
    with pytest.raises(ValueError, match="unverified installed"):
        overlay.verify_overlay(root)
