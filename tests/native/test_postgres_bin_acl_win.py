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

1. the calling account ends up with an inheritable RX ACE on the PostgreSQL
   PREFIX folder (``payload``, the parent of ``bin``) and a plain RX ACE on
   each ancestor up to the install root,
2. that ACE PROPAGATES onto an existing binary inside ``bin`` AND onto files
   inside the sibling ``lib`` and ``share`` folders (the whole point --
   ``postgres.exe``, the DLLs and the timezone data already exist when this
   runs, and the restricted-token postmaster reads all three),
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


def _dell_shaped_tree(tmp_path: Path) -> tuple[Path, list[Path], list[Path], str]:
    """The real payload layout -- ``bin``, ``lib`` and ``share`` under
    ``payload`` -- carrying the Dell's SYSTEM+Administrators-only access and
    no RX for the calling account.

    The PROTECTED Dell DACL goes on the install root and its ``packs``
    subtree down to ``payload``; ``bin``/``lib``/``share`` and their files
    stay unprotected so Windows' own propagation (the mechanism this fix
    relies on) can deliver the new inheritable ACE to them.
    """

    sid = _current_process_sid()
    payload = tmp_path / "install" / "packs" / "native-server-binaries" / "payload"
    bin_dir = payload / "bin"
    lib_dir = payload / "lib"
    share_dir = payload / "share" / "timezonesets"
    for directory in (bin_dir, lib_dir, share_dir):
        directory.mkdir(parents=True)
    files = {
        "bin": bin_dir / "postgres.exe",
        "lib": lib_dir / "icuuc77.dll",
        "share": share_dir / "Default",
    }
    for path in files.values():
        path.write_bytes(b"MZ")

    shape = f"D:P(A;OICI;FA;;;SY)(A;OICI;FA;;;BA)(A;;{_OPERATE_MASK};;;{sid})"
    protected = [
        tmp_path / "install",
        tmp_path / "install" / "packs",
        tmp_path / "install" / "packs" / "native-server-binaries",
        payload,
    ]
    for directory in protected:
        _apply_sddl(directory, shape, protect=True)
    # The prefix's children stay unprotected so Windows' propagation delivers
    # the new ACE to them, but each keeps a non-RX operate ACE for this
    # process so the test can stat and delete the tree.
    child_shape = f"D:(A;OICI;FA;;;SY)(A;OICI;FA;;;BA)(A;;{_OPERATE_MASK};;;{sid})"
    for directory in (payload / "share", share_dir, lib_dir, bin_dir):
        _apply_sddl(directory, child_shape, protect=False)
    for path in files.values():
        _apply_sddl(
            path,
            f"D:(A;;FA;;;SY)(A;;FA;;;BA)(A;;{_OPERATE_MASK};;;{sid})",
            protect=False,
        )
    return bin_dir, list(files.values()), protected, sid


def _every_dir(bin_dir: Path, protected: list[Path]) -> list[Path]:
    payload = bin_dir.parent
    return [
        bin_dir,
        payload / "lib",
        payload / "share",
        payload / "share" / "timezonesets",
        *protected,
    ]


@pytest.fixture
def dell_tree(tmp_path: Path) -> Iterator[tuple[Path, list[Path], list[Path], str]]:
    """The Dell-shaped tree, with the calling account's full control restored
    afterwards so pytest can delete the temporary tree."""

    bin_dir, files, protected, sid = _dell_shaped_tree(tmp_path)
    yield bin_dir, files, protected, sid
    for directory in _every_dir(bin_dir, protected):
        _grant_full_control(directory, sid, directory=True)
    for path in files:
        _grant_full_control(path, sid, directory=False)


def test_normalize_grants_the_calling_account_and_reaches_bin_lib_and_share(
    tmp_path: Path, dell_tree: tuple[Path, list[Path], list[Path], str]
) -> None:
    bin_dir, files, _protected, sid = dell_tree
    payload = bin_dir.parent

    applied = normalize_postgres_bin_acl(bin_dir / "pg_ctl.exe")

    assert applied, (
        "the Dell shape has no RX ACE for the calling account, so something must be written"
    )
    payload_dacl = _dacl_sddl(payload)
    assert has_rx_ace(payload_dacl, sid, inheritable=True), payload_dacl
    assert has_rx_ace(_dacl_sddl(tmp_path / "install"), sid, inheritable=False)
    for path in files:
        assert has_rx_ace(_dacl_sddl(path), sid, inheritable=False), (
            f"{path} must carry the ACE -- a payload-only directory change would "
            f"not let the restricted-token postmaster read {path.parent.name}"
        )
    # Nothing the Dell already had may be lost.
    for directory in [payload, tmp_path / "install"]:
        dacl = _dacl_sddl(directory)
        assert "(A;OICI;FA;;;SY)" in dacl, dacl
        assert "(A;OICI;FA;;;BA)" in dacl, dacl


def test_normalize_does_not_widen_to_users_or_everyone(
    tmp_path: Path, dell_tree: tuple[Path, list[Path], list[Path], str]
) -> None:
    bin_dir, _files, protected, _sid = dell_tree

    normalize_postgres_bin_acl(bin_dir / "pg_ctl.exe")

    for directory in [bin_dir, *protected]:
        dacl = _dacl_sddl(directory)
        for broad in (";;;BU)", ";;;WD)", ";;;AU)"):
            assert broad not in dacl, f"must never grant {broad}: {dacl}"


def test_normalize_is_idempotent_against_the_real_api(
    tmp_path: Path, dell_tree: tuple[Path, list[Path], list[Path], str]
) -> None:
    bin_dir, _files, _protected, _sid = dell_tree

    first = normalize_postgres_bin_acl(bin_dir / "pg_ctl.exe")
    before = {path: _dacl_sddl(Path(path)) for path, _sddl in first or []}
    second = normalize_postgres_bin_acl(bin_dir / "pg_ctl.exe")

    assert len(first or []) > 0
    assert second == []
    for path, dacl in before.items():
        assert _dacl_sddl(Path(path)) == dacl
