# SPDX-License-Identifier: Apache-2.0
# Copyright (c) The CivicCast Authors
"""station-state.json keeps its own file rights when CivicCast rewrites it.

rc.6.1 item 8: every save used to rebuild the DACL around the writing process
(an admin tool added its own SID and cut the service to R,W,D). Saves, the
offline admin password reset, and a backup restore now keep a safe existing
DACL; a missing or broad DACL still gets the standard strict ACL.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from civiccast.installer import station_state

pytestmark = [pytest.mark.windows_only]

# LocalService read: a principal CivicCast never grants, so it proves the
# operator's own entry survived.
_LOCAL_SERVICE_SID = "S-1-5-19"
_FILE_GENERIC_READ = 0x00120089


def _custom_sddl() -> str:
    from civiccast.certs.authority import _current_process_sid

    return (
        "D:P"
        f"(A;;FA;;;{_current_process_sid()})"
        "(A;;FA;;;S-1-5-18)"
        "(A;;FA;;;S-1-5-32-544)"
        f"(A;;0x{_FILE_GENERIC_READ:08X};;;{_LOCAL_SERVICE_SID})"
    )


def _set_dacl(path: Path, sddl: str) -> None:
    import win32security

    descriptor = win32security.ConvertStringSecurityDescriptorToSecurityDescriptor(sddl, 1)
    win32security.SetFileSecurity(
        str(path),
        win32security.DACL_SECURITY_INFORMATION | win32security.PROTECTED_DACL_SECURITY_INFORMATION,
        descriptor,
    )


def _dacl_rows(path: Path) -> tuple[bool, list[tuple[int, int, str]]]:
    import win32security

    descriptor = win32security.GetFileSecurity(str(path), win32security.DACL_SECURITY_INFORMATION)
    control, _revision = descriptor.GetSecurityDescriptorControl()
    acl = descriptor.GetSecurityDescriptorDacl()
    rows = []
    for index in range(acl.GetAceCount()):
        (ace_type, _flags), mask, sid = acl.GetAce(index)
        rows.append((ace_type, mask, win32security.ConvertSidToStringSid(sid)))
    return bool(control & win32security.SE_DACL_PROTECTED), rows


@pytest.fixture
def state_path(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    path = tmp_path / "station-state.json"
    monkeypatch.setenv("CIVICCAST_STATION_STATE_PATH", str(path))
    return path


def test_save_keeps_existing_custom_dacl(state_path: Path) -> None:
    state_path.write_text("{}", encoding="utf-8")
    _set_dacl(state_path, _custom_sddl())
    before = _dacl_rows(state_path)

    station_state._save_raw_state({"setup_complete": False, "marker": 1})

    assert json.loads(state_path.read_text(encoding="utf-8"))["marker"] == 1
    assert _dacl_rows(state_path) == before
    assert not state_path.with_suffix(".json.tmp").exists()


def test_two_saves_do_not_drift(state_path: Path) -> None:
    state_path.write_text("{}", encoding="utf-8")
    _set_dacl(state_path, _custom_sddl())
    before = _dacl_rows(state_path)

    station_state._save_raw_state({"n": 1})
    station_state._save_raw_state({"n": 2})

    assert _dacl_rows(state_path) == before


def test_broad_dacl_is_not_kept(state_path: Path) -> None:
    from civiccast.native.service_identity import SERVICE_SID

    state_path.write_text("{}", encoding="utf-8")
    _set_dacl(state_path, _custom_sddl() + "(A;;FR;;;S-1-5-32-545)")  # + Users read

    station_state._save_raw_state({"n": 1})

    protected, rows = _dacl_rows(state_path)
    sids = {sid for _type, _mask, sid in rows}
    assert protected
    assert "S-1-5-32-545" not in sids
    assert SERVICE_SID in sids  # the standard strict ACL was applied


def test_new_file_gets_standard_strict_acl(state_path: Path) -> None:
    from civiccast.native.service_identity import SERVICE_SID

    station_state._save_raw_state({"n": 1})

    protected, rows = _dacl_rows(state_path)
    assert protected
    assert SERVICE_SID in {sid for _type, _mask, sid in rows}


def test_inherited_dacl_is_not_kept(tmp_path: Path) -> None:
    path = tmp_path / "station-state.json"
    path.write_text("{}", encoding="utf-8")  # inherits the folder's rights

    assert station_state._preserved_state_dacl(path) is None
    assert station_state._preserved_state_dacl(tmp_path / "missing.json") is None


def test_admin_password_reset_keeps_existing_dacl(
    monkeypatch: pytest.MonkeyPatch, state_path: Path
) -> None:
    from civiccast.native import admin_recovery as recovery

    raw = {
        "setup_complete": True,
        "station": {"station_name": "Keep", "admin_username": "owner"},
        "admin": {"username": "owner", "display_name": "Owner"},
        "operator_console": {"tokens": []},
        "recovery": {"code_hashes": ["old"]},
    }
    station_state._replace_admin_password(raw, "old-password-value")
    state_path.write_text(json.dumps(raw), encoding="utf-8")
    _set_dacl(state_path, _custom_sddl())
    before = _dacl_rows(state_path)

    backup = recovery.reset_password(state_path, "new-password-value")

    assert _dacl_rows(state_path) == before
    assert json.loads(state_path.read_text(encoding="utf-8"))["recovery"]["code_hashes"] == []
    backup.unlink()


def test_restore_staging_file_gets_live_state_dacl(tmp_path: Path) -> None:
    from civiccast.dr import station_backup

    target = tmp_path / "station-state.json"
    target.write_text("{}", encoding="utf-8")
    _set_dacl(target, _custom_sddl())
    staging = tmp_path / ".r-staging"
    staging.write_bytes(b"")

    station_backup._keep_station_state_acl(target, staging)

    assert _dacl_rows(staging) == _dacl_rows(target)


def test_restore_leaves_other_files_alone(tmp_path: Path) -> None:
    from civiccast.dr import station_backup

    target = tmp_path / "other.json"
    target.write_text("{}", encoding="utf-8")
    _set_dacl(target, _custom_sddl())
    staging = tmp_path / ".r-staging"
    staging.write_bytes(b"")
    before = _dacl_rows(staging)

    station_backup._keep_station_state_acl(target, staging)

    assert _dacl_rows(staging) == before
