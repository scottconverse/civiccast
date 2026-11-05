# SPDX-License-Identifier: Apache-2.0
import hashlib
import zipfile

import pytest

from scripts import native_security_overlay as overlay


def test_archive_rejects_additional_unreviewed_members(tmp_path, monkeypatch):
    archive = tmp_path / "runtime.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("z-1.dll", b"reviewed")
        bundle.writestr("extra.dll", b"unreviewed")
    monkeypatch.setenv("CIVICCAST_SECURITY_LIBRARIES_ZIP", str(archive))
    monkeypatch.setattr(
        overlay,
        "policy",
        lambda: {
            "archive": {
                "bytes": archive.stat().st_size,
                "sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
            },
            "files": {"z-1.dll": {}},
        },
    )
    with pytest.raises(ValueError, match="unexpected"):
        overlay.acquire_runtime(tmp_path / "cache")


def test_cached_and_installed_bytes_are_independently_checked(tmp_path, monkeypatch):
    body = b"reviewed"
    pin = {
        "bytes": len(body),
        "sha256": hashlib.sha256(body).hexdigest(),
        "gstreamer": "bin/z-1.dll",
    }
    monkeypatch.setattr(
        overlay,
        "policy",
        lambda: {"archive": {"bytes": 1, "sha256": "a" * 64}, "files": {"z-1.dll": pin}},
    )
    # No reviewed archive is available, so a tampered cache cannot be rebuilt.
    monkeypatch.setenv("CIVICCAST_SECURITY_LIBRARIES_ZIP", str(tmp_path / "missing.zip"))
    cached = tmp_path / ("cache/security-libraries-" + "a" * 16)
    cached.mkdir(parents=True)
    (cached / "z-1.dll").write_bytes(body)
    assert overlay.acquire_runtime(cached.parent) == cached
    out = tmp_path / "installed"
    (out / "bin").mkdir(parents=True)
    (out / "bin/z-1.dll").write_bytes(body)
    overlay.verify_overlay(out)
    (out / "bin/z-1.dll").write_bytes(b"tampered")
    with pytest.raises(ValueError, match="unverified"):
        overlay.verify_overlay(out)
    (cached / "z-1.dll").write_bytes(b"tampered")
    with pytest.raises(ValueError, match="unverified"):
        overlay.acquire_runtime(cached.parent)


def test_postgres_keeps_consumer_filename_and_original_source_mapping(tmp_path, monkeypatch):
    monkeypatch.setattr(
        overlay,
        "policy",
        lambda: {
            "files": {
                "z-1.dll": {"postgres": "bin/zlib1.dll"},
                "libxml2.dll": {"postgres": "bin/libxml2.dll"},
            }
        },
    )
    monkeypatch.setattr(overlay, "acquire_runtime", lambda cache: tmp_path)
    checked = []
    monkeypatch.setattr(
        overlay,
        "assert_compatible_imports",
        lambda consumers, replacements: checked.append(replacements),
    )
    original = {
        "bin/zlib1.dll": tmp_path / "old.dll",
        "bin/postgres.exe": tmp_path / "postgres.exe",
    }
    secured = overlay.secured_postgres_sources(original, tmp_path)
    assert secured["bin/zlib1.dll"] == tmp_path / "z-1.dll"
    assert secured["bin/postgres.exe"] == original["bin/postgres.exe"]
    assert original["bin/zlib1.dll"] == tmp_path / "old.dll"
    assert set(checked[0]) == {"zlib1.dll", "libxml2.dll"}
