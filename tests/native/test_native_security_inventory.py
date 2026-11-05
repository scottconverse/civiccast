# SPDX-License-Identifier: Apache-2.0
# Copyright (c) The CivicCast Authors
"""Shipped runtime coverage and fail-closed native finding triage."""

import datetime as dt

import pytest

from scripts.check_native_audit import check
from scripts.native_gstreamer_ffmpeg_overlay import policy as gstreamer_ffmpeg_policy
from scripts.native_security_inventory import inventory


def test_inventory_includes_ocr_pdfium_native_server_and_excludes_build_node() -> None:
    components = {component["name"]: component for component in inventory()["components"]}
    names = set(components)
    assert {
        "tesseract",
        "leptonica",
        "tessdata-fast-eng",
        "pypdfium2",
        "cpython",
        "postgres",
        "ffmpeg",
        "gstreamer",
        "ollama",
    } <= names
    assert "node" not in names
    for name in ("gstreamer-libexpat", "cpython-libexpat"):
        assert components[name]["cpe"].startswith("cpe:2.3:a:libexpat_project:libexpat:")
    reviewed = gstreamer_ffmpeg_policy()
    ffmpeg = components["gstreamer-ffmpeg"]
    assert ffmpeg["version"] == reviewed["version"]
    assert ":ffmpeg:ffmpeg:7.1.5:" in ffmpeg["cpe"]
    assert ffmpeg["hashes"] == [
        {"alg": "SHA-256", "content": reviewed["provenance"]["source_sha256"]}
    ]


def test_inventory_lists_native_libraries_embedded_in_shipped_wheels() -> None:
    """F8 (RC4 SBOM scan of the installed payload): libpq and OpenSSL inside the
    psycopg-binary wheel and Scintilla inside pywin32 shipped but were never scanned
    (pip-audit checks the Python package, not the DLLs it carries)."""
    components = {component["name"]: component for component in inventory()["components"]}
    expected = {
        "psycopg-binary-libpq": ("18.4", "postgresql:postgresql"),
        "psycopg-binary-openssl": ("3.6.5", "openssl:openssl"),
        "pywin32-scintilla": ("4.4.6", "scintilla:scintilla"),
    }
    for name, (version, cpe) in expected.items():
        assert components[name]["version"] == version, name
        assert components[name]["cpe"].startswith(f"cpe:2.3:a:{cpe}:{version}:"), name
        assert any(
            prop["name"] == "civiccast:parent-wheel-sha256"
            for prop in components[name]["properties"]
        ), name


def test_native_triage_requires_exact_version_and_current_review() -> None:
    match = {
        "artifact": {"name": "library", "version": "2"},
        "vulnerability": {"id": "CVE-example"},
    }
    entry = {
        "package": "library",
        "version": "2",
        "id": "CVE-example",
        "reason": "Absent vulnerable codec",
        "evidence": ["shipped manifest"],
        "review_by": "2026-11-29",
    }
    assert not check({"matches": [match]}, {"not_affected": [entry]}, today=dt.date(2026, 10, 29))
    assert check({"matches": [match]}, {"not_affected": [entry]}, today=dt.date(2026, 12, 1))
    entry["version"] = "1"
    assert check({"matches": [match]}, {"not_affected": [entry]}, today=dt.date(2026, 10, 29))
    with pytest.raises(KeyError):
        check({"error": "scanner failed"}, {"not_affected": []})
