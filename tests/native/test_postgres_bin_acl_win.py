# SPDX-License-Identifier: Apache-2.0
# Copyright (c) The CivicCast Authors
"""Windows-only real-Win32 proof for the PostgreSQL binary-tree ACL grant
(rc.6 -> rc.7 Dell upgrade blocker, 2026-11-19).

``win`` appears in this module's own filename (per the D3/CI naming
convention -- see ``test_pgdata_acl_win.py``) so ``-k "not win"`` deselects it
honestly. Skipped entirely on non-Windows.

``test_postgres_bin_acl.py`` proves the decision logic through injected
seams; this file drives the PRODUCTION path (real ``GetNamedSecurityInfo`` /
``SetNamedSecurityInfo``) over a tree shaped like the Dell's: PROTECTED
DACLs naming SYSTEM and Administrators, with NO read+execute ACE for the
account this process runs as. The test process keeps only what it needs to
operate (``READ_CONTROL``, ``FILE_READ_ATTRIBUTES``, ``FILE_TRAVERSE`` --
which deliberately does NOT count as RX), so the module has to do the work.
It asserts what the fix depends on:

1. the calling account ends up with an inheritable RX ACE on the ``bin``
   folder and a plain RX ACE on each ancestor up to the install root,
2. that ACE PROPAGATES onto an existing binary inside ``bin`` (the whole
   point -- ``postgres.exe`` already exists when this runs),
3. every pre-existing ACE survives the merge (SYSTEM's and Administrators'
   full control in particular), and
4. the pass is idempotent -- a second call writes nothing.

PRIVILEGE TIER: ordinary. The tree is created by this test process, so owner
rights supply ``WRITE_DAC`` without elevation.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest

from civiccast.native.pgdata_acl import _current_process_sid
from civiccast.native.postgres_bin_acl import has_rx_ace, normalize_postgres_bin_acl

pytestmark = [
    pytest.mark.windows_only,
    pytest.mark.skipif(os.name != "nt", reason="real Win32 ACL is only meaningful on Windows"),
]

#: READ_CONTROL | FILE_READ_ATTRIBUTES | FILE_TRAVERSE. Enough for this test
#: process to stat and read each level; deliberately NOT RX.
_OPERATE_MASK = "0x200a0"


def _dacl_sddl(path: Path) -> str:
    import win32security

    descriptor = win32security.GetNamedSecurityInfo(
        str(path), win32security.SE_FILE_OBJECT, win32security.DACL_SECURITY_INFORMATION
    )
    return str(
        win32security.ConvertSecurityDescriptorToStringSecurityDescriptor(
            descriptor,
            win32security.SDDL_REVISION_1,
            win32security.DACL_SECURITY_INFORMATION,
        )
    )


def _apply_sddl(path: Path, sddl: str, *, protect: bool) -> None:
    import win32security

    descriptor = win32security.ConvertStringSecurityDescriptorToSecurityDescriptor(
        sddl, win32security.SDDL_REVISION_1
    )
    protection = (
        win32security.PROTECTED_DACL_SECURITY_INFORMATION
        if protect
        else win32security.UNPROTECTED_DACL_SECURITY_INFORMATION
    )
    win32security.SetNamedSecurityInfo(
        str(path),
        win32security.SE_FILE_OBJECT,
        win32security.DACL_SECURITY_INFORMATION | protection,
        None,
        None,
        descriptor.GetSecurityDescriptorDacl(),
        None,
    )


def _grant_full_control(path: Path, sid: str, *, directory: bool) -> None:
    flags = "OICI" if directory else ""
    _apply_sddl(
        path, f"D:(A;{flags};FA;;;SY)(A;{flags};FA;;;BA)(A;{flags};FA;;;{sid})", protect=False
    )


def _dell_shaped_tree(tmp_path: Path) -> tuple[Path, Path, str]:
    """``<root>\\packs\\native-server-binaries\\payload\\bin`` with a
    ``postgres.exe`` inside, carrying the Dell's SYSTEM+Administrators-only
    access and no RX for the calling account."""

    sid = _current_process_sid()
    bin_dir = tmp_path / "install" / "packs" / "native-server-binaries" / "payload" / "bin"
    bin_dir.mkdir(parents=True)
    binary = bin_dir / "postgres.exe"
    binary.write_bytes(b"MZ")
    shape = f"D:P(A;OICI;FA;;;SY)(A;OICI;FA;;;BA)(A;;{_OPERATE_MASK};;;{sid})"
    for directory in [tmp_path / "install", *bin_dir.parents[:3], bin_dir]:
        _apply_sddl(directory, shape, protect=True)
    # The file stays unprotected so Windows' own propagation (the mechanism
    # pgdata_acl's measured test relies on) can reach it.
    _apply_sddl(binary, f"D:(A;;FA;;;SY)(A;;FA;;;BA)(A;;{_OPERATE_MASK};;;{sid})", protect=False)
    return bin_dir, binary, sid


@pytest.fixture
def dell_tree(tmp_path: Path) -> Iterator[tuple[Path, Path, str]]:
    """The Dell-shaped tree, with the calling account's full control restored
    afterwards so pytest can delete the temporary tree."""

    bin_dir, binary, sid = _dell_shaped_tree(tmp_path)
    yield bin_dir, binary, sid
    for directory in [tmp_path / "install", *bin_dir.parents[:3], bin_dir]:
        _grant_full_control(directory, sid, directory=True)
    _grant_full_control(binary, sid, directory=False)


def test_normalize_grants_the_calling_account_and_propagates_to_the_binary(
    tmp_path: Path, dell_tree: tuple[Path, Path, str]
) -> None:
    bin_dir, binary, sid = dell_tree

    applied = normalize_postgres_bin_acl(bin_dir / "pg_ctl.exe")

    assert applied, (
        "the Dell shape has no RX ACE for the calling account, so something must be written"
    )
    bin_dacl = _dacl_sddl(bin_dir)
    assert has_rx_ace(bin_dacl, sid, inheritable=True), bin_dacl
    assert has_rx_ace(_dacl_sddl(binary), sid, inheritable=False), (
        "the existing postgres.exe must carry the ACE -- a directory-only change "
        "would not let the restricted-token postmaster load it"
    )
    assert has_rx_ace(_dacl_sddl(tmp_path / "install"), sid, inheritable=False)
    # Nothing the Dell already had may be lost.
    for directory in [bin_dir, tmp_path / "install"]:
        dacl = _dacl_sddl(directory)
        assert "(A;OICI;FA;;;SY)" in dacl, dacl
        assert "(A;OICI;FA;;;BA)" in dacl, dacl


def test_normalize_does_not_widen_to_users_or_everyone(
    tmp_path: Path, dell_tree: tuple[Path, Path, str]
) -> None:
    bin_dir, _binary, _sid = dell_tree

    normalize_postgres_bin_acl(bin_dir / "pg_ctl.exe")

    for directory in [bin_dir, tmp_path / "install"]:
        dacl = _dacl_sddl(directory)
        for broad in (";;;BU)", ";;;WD)", ";;;AU)"):
            assert broad not in dacl, f"must never grant {broad}: {dacl}"


def test_normalize_is_idempotent_against_the_real_api(
    tmp_path: Path, dell_tree: tuple[Path, Path, str]
) -> None:
    bin_dir, _binary, _sid = dell_tree

    first = normalize_postgres_bin_acl(bin_dir / "pg_ctl.exe")
    before = {path: _dacl_sddl(Path(path)) for path, _sddl in first or []}
    second = normalize_postgres_bin_acl(bin_dir / "pg_ctl.exe")

    assert len(first or []) > 0
    assert second == []
    for path, dacl in before.items():
        assert _dacl_sddl(Path(path)) == dacl
