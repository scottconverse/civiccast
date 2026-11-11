# SPDX-License-Identifier: Apache-2.0
"""Validate the reviewed source-built interpreter independently of payload manifests."""

from __future__ import annotations

import hashlib
import io
import json
import zipfile
from collections.abc import Callable

from civiccast.native.app_payload import INTERPRETER_DISTRIBUTION, INTERPRETER_VERSION

PROVENANCE_PATH = "python-runtime-provenance.json"
# This binds source/dependency pins, compiler/builder identities, licenses and
# every original runtime file. Changing a payload manifest cannot change this pin.
PROVENANCE_SHA256 = "8d94f44d30ee3f6b032fca8a5a93f1062df5faa3042bdd902142b30fd1c1c5b2"
BUILDER_SHA256 = "8b3036752ec76cfce8b07281d32ab964ee3251f64f3bf7edc371ea6fa93b9cc6"
INSTALLED_PTH = (
    b"python312.zip\n.\n\n# Uncomment to run site.main() automatically\n"
    b"import site\nLib\\site-packages\n"
)
STDLIB_PATH = "python312.zip"
ORIGINAL_STDLIB_SHA256 = "fdd8dcd90b1c87dddd9086502b206e3ed48a5a8e30e58c6b45d000fe518c7d5a"
ORIGINAL_STDLIB_BYTES = 3_854_776
# PSF-2.0 Lib/http/client.py from v3.12.16, with only the parser changes from:
# https://github.com/python/cpython/commit/2eb2af805c156ca52b316bb4ced5e0ab3971d0ae
# The original runtime's LICENSE.txt continues to cover this stdlib source.
HTTP_CLIENT_BASE_SOURCE_SHA256 = "c2c0694155837d72fc710a4138e5d1e53ae1a652b117a2e23a5b1b0db527a0f9"
HTTP_CLIENT_BACKPORT_COMMIT = "2eb2af805c156ca52b316bb4ced5e0ab3971d0ae"
HTTP_CLIENT_SOURCE_SHA256 = "2590d9284866458cc6d2b7c0b726338867ea6bb849581945562913f035835c67"
HTTP_CLIENT_SOURCE_BYTES = 59_788
PATCHED_STDLIB_SHA256 = "65551fbece52b878bf1bc9187b8bb4d2518767de4d3a4f9a6a64d4a79016681b"
PATCHED_STDLIB_BYTES = 9_030_959


def patch_stdlib_http_client(original: bytes, source: bytes) -> bytes:
    """Apply the pinned CVE-2026-103995 backport to the reviewed stdlib ZIP.

    No interpreter-dependent bytecode or zlib output: all members use ZIP_STORED
    and fixed metadata, with every other member's uncompressed bytes unchanged.
    The output pin is independent of the generated payload manifest.
    """
    if (
        len(original) != ORIGINAL_STDLIB_BYTES
        or hashlib.sha256(original).hexdigest() != ORIGINAL_STDLIB_SHA256
    ):
        raise ValueError("stdlib input differs from reviewed original ZIP")
    if (
        len(source) != HTTP_CLIENT_SOURCE_BYTES
        or hashlib.sha256(source).hexdigest() != HTTP_CLIENT_SOURCE_SHA256
    ):
        raise ValueError("HTTP client backport source differs from reviewed pin")
    output = io.BytesIO()
    with (
        zipfile.ZipFile(io.BytesIO(original)) as archive,
        zipfile.ZipFile(output, "w", compression=zipfile.ZIP_STORED) as patched,
    ):
        names = (set(archive.namelist()) - {"http/client.pyc"}) | {"http/client.py"}
        for name in sorted(names):
            data = source if name == "http/client.py" else archive.read(name)
            info = zipfile.ZipInfo(name, (1980, 1, 1, 0, 0, 0))
            info.create_system = 0
            info.external_attr = 0x20
            patched.writestr(info, data)
    result = output.getvalue()
    if (
        len(result) != PATCHED_STDLIB_BYTES
        or hashlib.sha256(result).hexdigest() != PATCHED_STDLIB_SHA256
    ):
        raise ValueError("patched stdlib ZIP differs from reviewed output pin")
    return result


def verify_runtime_files(read: Callable[[str], bytes], *, installed: bool = False) -> list[str]:
    """Check original input, or installed bytes with the approved ._pth and stdlib edits."""
    try:
        raw = read(PROVENANCE_PATH)
        if hashlib.sha256(raw).hexdigest() != PROVENANCE_SHA256:
            return ["runtime provenance SHA-256 differs from reviewed pin"]
        provenance = json.loads(raw)
        if (
            provenance["schema_version"] != 1
            or provenance["distribution"] != INTERPRETER_DISTRIBUTION
            or provenance["version"] != INTERPRETER_VERSION
            or provenance["architecture"] != "amd64"
            or provenance["builder_sha256"] != BUILDER_SHA256
        ):
            return ["runtime provenance identity/version/builder mismatch"]
        if installed and STDLIB_PATH not in provenance["files"]:
            return ["runtime provenance omits required patched stdlib ZIP"]
        problems = []
        for name, record in provenance["files"].items():
            # Paths are also pinned by the provenance digest; retain a local
            # boundary check so malformed test/prospective pins fail closed.
            if "\\" in name or name.startswith("/") or ".." in name.split("/"):
                return ["unsafe runtime provenance path"]
            data = read(name)
            if installed and name == "python312._pth":
                valid = data == INSTALLED_PTH
            elif installed and name == STDLIB_PATH:
                valid = len(data) == PATCHED_STDLIB_BYTES and (
                    hashlib.sha256(data).hexdigest() == PATCHED_STDLIB_SHA256
                )
            else:
                valid = len(data) == record["bytes"] and (
                    hashlib.sha256(data).hexdigest() == record["sha256"]
                )
            if not valid:
                problems.append(f"runtime file differs from reviewed build: {name}")
        return problems
    except (OSError, KeyError, ValueError, TypeError) as exc:
        return [f"missing or invalid runtime provenance/file: {exc}"]
