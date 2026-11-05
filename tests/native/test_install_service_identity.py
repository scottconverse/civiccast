# SPDX-License-Identifier: Apache-2.0
"""Exercise installer decisions without changing this machine's SCM or registry."""

import os
from contextlib import nullcontext

import pytest

from civiccast.native import install_service_identity as migration
from civiccast.native.install_service_identity import _grant_tree as grant_windows_tree
from civiccast.native.service_identity import SERVICE_ACCOUNT

pytestmark = [
    pytest.mark.windows_only,
    pytest.mark.skipif(os.name != "nt", reason="Windows installer APIs"),
]


@pytest.fixture
def installed(tmp_path, monkeypatch):
    import winreg

    import win32service

    from civiccast.native import admin_recovery

    root = tmp_path / "app"
    (root / "runtime").mkdir(parents=True)
    (root / "runtime" / "python.exe").touch()
    profile = tmp_path / "profile"
    profile.mkdir()
    monkeypatch.setenv("PROGRAMDATA", str(tmp_path))
    monkeypatch.setenv("CIVICCAST_STATION_STATE_PATH", str(profile / "station-state.json"))
    state = {"environment": ["CUSTOM=preserved"], "account": "LocalSystem", "starts": 0}
    monkeypatch.setattr(admin_recovery, "_require_elevation", lambda: None)
    monkeypatch.setattr(migration, "station_profile_root", lambda: profile)
    monkeypatch.setattr(migration, "_grant_tree", lambda *args, **kwargs: None)
    monkeypatch.setattr(migration, "restrict_data_directory", lambda *args, **kwargs: None)
    monkeypatch.setattr(migration, "_grant_database_key_read", lambda: None)
    monkeypatch.setattr(migration, "_grant_service_environment_read", lambda: None)
    monkeypatch.setattr(winreg, "OpenKey", lambda *args: nullcontext("key"))
    monkeypatch.setattr(
        winreg, "QueryValueEx", lambda *args: (state["environment"], winreg.REG_MULTI_SZ)
    )
    monkeypatch.setattr(winreg, "SetValueEx", lambda *args: state.update(environment=args[-1]))
    monkeypatch.setattr(win32service, "OpenSCManager", lambda *args: "scm")
    monkeypatch.setattr(win32service, "OpenService", lambda *args: "service")
    monkeypatch.setattr(win32service, "CloseServiceHandle", lambda *args: None)
    monkeypatch.setattr(
        win32service, "QueryServiceConfig", lambda *args: [None] * 7 + [state["account"]]
    )
    monkeypatch.setattr(
        win32service, "QueryServiceStatus", lambda *args: [0, win32service.SERVICE_STOPPED]
    )

    def start(*args):
        assert migration.MIGRATION_ENV + "=1" in state["environment"]
        state["starts"] += 1
        (profile / "identity-migration.complete").write_text("preserved")

    monkeypatch.setattr(win32service, "StartService", start)
    monkeypatch.setattr(
        win32service, "ChangeServiceConfig", lambda *args: state.update(account=args[8])
    )
    return root, state


def test_migration_preserves_environment_and_changes_account_after_export(installed):
    root, state = installed
    migration.configure_service_identity(root)
    assert state["account"] == SERVICE_ACCOUNT
    assert state["starts"] == 1
    assert "CUSTOM=preserved" in state["environment"]
    assert not any(item.startswith(migration.MIGRATION_ENV + "=") for item in state["environment"])
    assert any(item.startswith("LOCALAPPDATA=") for item in state["environment"])


def test_failed_export_keeps_original_identity_and_environment(installed, monkeypatch):
    import win32service

    root, state = installed
    monkeypatch.setattr(win32service, "StartService", lambda *args: None)
    with pytest.raises(RuntimeError, match="could not preserve"):
        migration.configure_service_identity(root)
    assert state["account"] == "LocalSystem"
    assert state["environment"] == ["CUSTOM=preserved"]


def test_existing_virtual_account_skips_old_account_export(installed):
    root, state = installed
    state["account"] = SERVICE_ACCOUNT
    migration.configure_service_identity(root)
    assert state["starts"] == 0


def test_virtual_account_configuration_uses_null_password(installed, monkeypatch):
    import win32service

    root, _ = installed
    calls = []
    monkeypatch.setattr(win32service, "ChangeServiceConfig", lambda *args: calls.append(args))
    migration.configure_service_identity(root)
    assert calls[0][8] == SERVICE_ACCOUNT
    assert calls[0][9] is None


@pytest.mark.parametrize("phase", ["pre-upgrade", "registration"])
def test_model_data_is_writable_but_runtime_stays_read_only(installed, monkeypatch, phase):
    import win32security

    from civiccast.native.service_identity import SERVICE_SID, restrict_data_directory

    root, state = installed
    state["account"] = SERVICE_ACCOUNT
    model = root / "models" / "ollama" / "manifests-v2" / "old.json"
    model.parent.mkdir(parents=True)
    model.write_text("{}", encoding="utf-8")
    legacy = root / "packs" / "local-ai-model" / "models" / "manifests"
    legacy.mkdir(parents=True)
    monkeypatch.setattr(migration, "restrict_data_directory", restrict_data_directory)
    monkeypatch.setattr(migration, "_grant_tree", grant_windows_tree)

    if phase == "pre-upgrade":
        restrict_data_directory(root, writable=False)
        grant_windows_tree(root, "RX")
        migration.prepare_model_store_permissions(root)
        assert state["starts"] == 0
    else:
        migration.configure_service_identity(root)

    def service_mask(path):
        acl = win32security.GetFileSecurity(
            str(path), win32security.DACL_SECURITY_INFORMATION
        ).GetSecurityDescriptorDacl()
        return next(
            acl.GetAce(index)[1]
            for index in range(acl.GetAceCount())
            if win32security.ConvertSidToStringSid(acl.GetAce(index)[2]) == SERVICE_SID
            and not acl.GetAce(index)[0][1] & win32security.INHERITED_ACE
        )

    assert service_mask(model) == 0x001301BF
    assert service_mask(model.parent.parent) == 0x001301BF
    assert service_mask(legacy) == 0x001301BF
    assert service_mask(root) == 0x001200A9
    assert service_mask(root / "runtime" / "python.exe") == 0x001200A9


def test_data_root_grant_does_not_override_protected_installer_subtrees(installed, monkeypatch):
    import win32security

    from civiccast.native.service_identity import SERVICE_SID
    from civiccast.native.upgrade.journal import _harden_state_root_acl

    root, state = installed
    state["account"] = SERVICE_ACCOUNT
    data_root = root.parent / "CivicCast"
    protected = data_root / "upgrade"
    protected.mkdir(parents=True)
    _harden_state_root_acl(protected)

    def grant(path, rights, **kwargs):
        if path == data_root:
            grant_windows_tree(path, rights, **kwargs)

    monkeypatch.setattr(migration, "_grant_tree", grant)
    migration.configure_service_identity(root)
    acl = win32security.GetFileSecurity(
        str(protected), win32security.DACL_SECURITY_INFORMATION
    ).GetSecurityDescriptorDacl()
    trustees = {
        win32security.ConvertSidToStringSid(acl.GetAce(index)[2])
        for index in range(acl.GetAceCount())
    }
    assert SERVICE_SID not in trustees


def test_unregistered_service_sid_grant_uses_native_acl_and_keeps_protected_child(
    tmp_path, monkeypatch
):
    import win32security

    from civiccast.certs.authority import _current_process_sid

    fake_service_sid = "S-1-5-80-1111111111-2222222222-3333333333-1234567890-987654321"
    writer_sid = _current_process_sid()
    monkeypatch.setattr(migration, "SERVICE_SID", fake_service_sid)
    data_root = tmp_path / "CivicCast"
    protected = data_root / "upgrade"
    protected.mkdir(parents=True)
    state_file = data_root / "station-state.json"
    state_file.write_text("{}", encoding="utf-8")
    sddl = f"D:P(A;OICI;FA;;;{writer_sid})(A;OICI;FA;;;S-1-5-18)(A;OICI;FA;;;S-1-5-32-544)"
    descriptor = win32security.ConvertStringSecurityDescriptorToSecurityDescriptor(sddl, 1)
    for directory in (data_root, protected):
        win32security.SetFileSecurity(
            str(directory),
            win32security.DACL_SECURITY_INFORMATION
            | win32security.PROTECTED_DACL_SECURITY_INFORMATION,
            descriptor,
        )
    win32security.SetFileSecurity(
        str(state_file),
        win32security.DACL_SECURITY_INFORMATION | win32security.PROTECTED_DACL_SECURITY_INFORMATION,
        descriptor,
    )

    migration._grant_tree(data_root, "M", recursive=False)
    migration._grant_tree(state_file, "M", recursive=False)

    def grants(path):
        security = win32security.GetFileSecurity(str(path), win32security.DACL_SECURITY_INFORMATION)
        acl = security.GetSecurityDescriptorDacl()
        return {
            win32security.ConvertSidToStringSid(acl.GetAce(index)[2]): acl.GetAce(index)[1]
            for index in range(acl.GetAceCount())
        }

    root_grants = grants(data_root)
    assert root_grants[fake_service_sid] == 0x001301BF
    assert set(root_grants) == {writer_sid, "S-1-5-18", "S-1-5-32-544", fake_service_sid}
    assert fake_service_sid not in grants(protected)
    file_grants = grants(state_file)
    assert file_grants[fake_service_sid] == 0x001301BF
    assert set(file_grants) == {writer_sid, "S-1-5-18", "S-1-5-32-544", fake_service_sid}


def test_grant_tree_prunes_real_junction_and_preserves_external_acl(tmp_path):
    import win32con
    import win32file
    import win32security

    from civiccast.certs.authority import _current_process_sid
    from civiccast.native.service_identity import SERVICE_SID
    from civiccast.native.upgrade.junction import _mklink_junction

    root = tmp_path / "CivicCast"
    ordinary = root / "profile" / "ordinary"
    ordinary.mkdir(parents=True)
    ordinary_file = ordinary / "state.json"
    ordinary_file.write_text("{}", encoding="utf-8")
    inherited = root / "profile" / "maintenance"
    inherited.mkdir()
    inherited_file = inherited / "status.txt"
    inherited_file.write_text("existing", encoding="utf-8")

    outside = tmp_path / "outside"
    outside_child = outside / "nested"
    outside_child.mkdir(parents=True)
    outside_file = outside_child / "external.txt"
    outside_file.write_text("untouched", encoding="utf-8")
    redirect = root / "profile" / "redirect"
    _mklink_junction(redirect, outside)
    assert redirect.is_junction()

    writer_sid = _current_process_sid()
    protected = win32security.ConvertStringSecurityDescriptorToSecurityDescriptor(
        f"D:P(A;OICI;FA;;;{writer_sid})(A;OICI;FA;;;S-1-5-18)(A;OICI;FA;;;S-1-5-32-544)",
        1,
    )
    for path in (ordinary, ordinary_file):
        win32security.SetFileSecurity(
            str(path),
            win32security.DACL_SECURITY_INFORMATION
            | win32security.PROTECTED_DACL_SECURITY_INFORMATION,
            protected,
        )

    def dacl_signature(path):
        security = win32security.GetFileSecurity(str(path), win32security.DACL_SECURITY_INFORMATION)
        acl = security.GetSecurityDescriptorDacl()
        return tuple(
            (
                acl.GetAce(index)[0],
                acl.GetAce(index)[1],
                win32security.ConvertSidToStringSid(acl.GetAce(index)[2]),
            )
            for index in range(acl.GetAceCount())
        )

    outside_before = {path: dacl_signature(path) for path in (outside, outside_child, outside_file)}

    def junction_dacl_signature(path):
        handle = win32file.CreateFile(
            str(path),
            win32con.MAXIMUM_ALLOWED,
            win32con.FILE_SHARE_READ | win32con.FILE_SHARE_WRITE | win32con.FILE_SHARE_DELETE,
            None,
            win32con.OPEN_EXISTING,
            win32file.FILE_FLAG_BACKUP_SEMANTICS | win32file.FILE_FLAG_OPEN_REPARSE_POINT,
            None,
        )
        try:
            security = win32security.GetSecurityInfo(
                handle,
                win32security.SE_FILE_OBJECT,
                win32security.DACL_SECURITY_INFORMATION,
            )
            acl = security.GetSecurityDescriptorDacl()
            return tuple(
                (
                    acl.GetAce(index)[0],
                    acl.GetAce(index)[1],
                    win32security.ConvertSidToStringSid(acl.GetAce(index)[2]),
                )
                for index in range(acl.GetAceCount())
            )
        finally:
            handle.Close()

    migration._grant_tree(root / "profile", "M", recursive=False)
    for path in (inherited, inherited_file):
        assert any(sid == SERVICE_SID for _, _, sid in dacl_signature(path))
    redirect_before_recursive = junction_dacl_signature(redirect)
    migration._grant_tree(root, "M")

    assert {path: dacl_signature(path) for path in outside_before} == outside_before
    assert junction_dacl_signature(redirect) == redirect_before_recursive
    for path in (ordinary, ordinary_file):
        security = win32security.GetFileSecurity(str(path), win32security.DACL_SECURITY_INFORMATION)
        control, _ = security.GetSecurityDescriptorControl()
        assert control & win32security.SE_DACL_PROTECTED
        entries = dacl_signature(path)
        grants = {sid: mask for _, mask, sid in entries}
        assert grants[SERVICE_SID] == 0x001301BF
        assert set(grants) == {writer_sid, "S-1-5-18", "S-1-5-32-544", SERVICE_SID}


def test_grant_tree_rejects_junction_root_without_touching_target_acl(tmp_path):
    import win32security

    from civiccast.native.upgrade.junction import _mklink_junction

    target = tmp_path / "target"
    target.mkdir()
    (target / "state.json").write_text("{}", encoding="utf-8")
    link = tmp_path / "root-junction"
    _mklink_junction(link, target)
    before = win32security.GetFileSecurity(
        str(target), win32security.DACL_SECURITY_INFORMATION
    ).GetSecurityDescriptorDacl()
    before_entries = tuple(
        (before.GetAce(index)[0], before.GetAce(index)[1], before.GetAce(index)[2])
        for index in range(before.GetAceCount())
    )

    with pytest.raises(RuntimeError, match="refuses redirected paths"):
        migration._grant_tree(link, "M")
    migration._grant_tree(tmp_path / "missing", "M")

    after = win32security.GetFileSecurity(
        str(target), win32security.DACL_SECURITY_INFORMATION
    ).GetSecurityDescriptorDacl()
    after_entries = tuple(
        (after.GetAce(index)[0], after.GetAce(index)[1], after.GetAce(index)[2])
        for index in range(after.GetAceCount())
    )
    assert after_entries == before_entries


def test_grant_tree_opens_running_python_image_with_acl_only_access(tmp_path):
    import shutil
    import subprocess
    import sys
    from pathlib import Path

    root = tmp_path / "runtime"
    root.mkdir()
    executable = root / "python.exe"
    base_executable = Path(sys._base_executable)
    assert base_executable.is_file(), f"Base Python executable not found: {base_executable}"
    shutil.copy2(base_executable, executable)
    runtime_dlls = list(Path(sys.base_prefix).glob("python*.dll"))
    assert runtime_dlls, "The running Python installation has no adjacent runtime DLLs."
    for runtime_dll in runtime_dlls:
        shutil.copy2(runtime_dll, root / runtime_dll.name)

    repo_root = Path(__file__).resolve().parents[2]
    environment = os.environ.copy()
    environment["PYTHONHOME"] = sys.base_prefix
    environment["PYTHONPATH"] = os.pathsep.join(
        [str(repo_root), *(entry for entry in sys.path if entry)]
    )
    pywin32_system32 = next(
        entry / "pywin32_system32"
        for entry in map(Path, sys.path)
        if (entry / "pywin32_system32").is_dir()
    )
    script = (
        "import os, sys; from pathlib import Path; "
        f"pywin32_dlls = os.add_dll_directory({str(pywin32_system32)!r}); "
        "assert Path(sys.executable).resolve() == Path(sys.argv[1], 'python.exe').resolve(); "
        "from civiccast.native.install_service_identity import _grant_tree; "
        "_grant_tree(Path(sys.argv[1]), 'RX'); print('GRANT_SUCCEEDED')"
    )
    result = subprocess.run(
        [str(executable), "-c", script, str(root)],
        cwd=repo_root,
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    assert "GRANT_SUCCEEDED" in result.stdout


def test_recursive_directory_open_asks_for_acl_rights_explicitly(tmp_path, monkeypatch):
    # Gate A 41882420078 (2026-11-04) failed with GetSecurityInfo error 5 on the Windows
    # Sandbox MappedFolder C:\CivicCastHostStore\install. Reproduced in Sandbox: there a
    # MAXIMUM_ALLOWED directory handle opens but lacks READ_CONTROL, while
    # MAXIMUM_ALLOWED|READ_CONTROL|WRITE_DAC works on the mapped folder AND, like
    # MAXIMUM_ALLOWED alone, does not auto-propagate to children (mapped and local NTFS).
    import win32con

    root = tmp_path / "data"
    (root / "child").mkdir(parents=True)
    (root / "child" / "state.json").write_text("{}", encoding="utf-8")
    seen = []
    real_open = migration._open_for_acl_change

    def recording_open(member, desired_access):
        seen.append((member.name, desired_access))
        return real_open(member, desired_access)

    monkeypatch.setattr(migration, "_open_for_acl_change", recording_open)
    migration._grant_tree(root, "RX")

    dir_access = {access for name, access in seen if name in ("data", "child")}
    assert dir_access == {win32con.MAXIMUM_ALLOWED | win32con.READ_CONTROL | win32con.WRITE_DAC}


def test_recursive_grant_succeeds_while_another_process_works_inside_the_tree(
    tmp_path, monkeypatch
):
    # RC4 Gate A run 41795787409 (2026-11-04) failed a clean install at
    # d4-service-registration with ERROR_SHARING_VIOLATION from the recursive
    # program-tree grant. A process whose current directory is inside the tree
    # (an operator's Explorer or console window, or a helper process) holds that
    # directory without FILE_SHARE_DELETE, and the MAXIMUM_ALLOWED directory open
    # requests DELETE for an administrator, so CreateFile refused it.
    import subprocess
    import sys
    import time

    import win32security

    fake_service_sid = "S-1-5-80-1111111111-2222222222-3333333333-1234567890-987654321"
    monkeypatch.setattr(migration, "SERVICE_SID", fake_service_sid)
    root = tmp_path / "app"
    held = root / "runtime" / "Lib"
    deep = held / "site-packages"
    deep.mkdir(parents=True)
    held_file = deep / "module.py"
    held_file.write_text("x = 1\n", encoding="utf-8")
    quiet = root / "models"
    quiet.mkdir()
    quiet_file = quiet / "blob"
    quiet_file.write_bytes(b"model")

    def service_aces(path):
        acl = win32security.GetFileSecurity(
            str(path), win32security.DACL_SECURITY_INFORMATION
        ).GetSecurityDescriptorDacl()
        return [
            (acl.GetAce(index)[0][1] & win32security.INHERITED_ACE, acl.GetAce(index)[1])
            for index in range(acl.GetAceCount())
            if win32security.ConvertSidToStringSid(acl.GetAce(index)[2]) == fake_service_sid
        ]

    holder = subprocess.Popen(
        [sys.executable, "-I", "-c", "import time; time.sleep(60)"], cwd=str(held)
    )
    try:
        time.sleep(1.5)
        assert holder.poll() is None
        grant_windows_tree(root, "RX")
    finally:
        holder.kill()
        holder.wait()

    for path in (root, held, deep, held_file, quiet, quiet_file):
        assert (0, 0x001200A9) in service_aces(path), path
    # Only the directory that was in use may fall back to a propagating grant;
    # the rest of the tree keeps exactly one explicit, non-inherited grant.
    for path in (root, quiet, quiet_file):
        assert service_aces(path) == [(0, 0x001200A9)], path


@pytest.mark.parametrize("stop_finishes", [True, False])
def test_timed_out_migration_waits_for_stopped_before_restoring_environment(
    installed, monkeypatch, stop_finishes
):
    import win32service

    root, state = installed
    statuses = iter(
        [
            win32service.SERVICE_STOPPED,
            win32service.SERVICE_RUNNING,
            win32service.SERVICE_STOP_PENDING,
            win32service.SERVICE_STOPPED,
        ]
    )
    observed = []

    def query(*args):
        status = next(statuses)
        observed.append(status)
        if len(observed) > 1:
            assert migration.MIGRATION_ENV + "=1" in state["environment"]
        return [0, status]

    times = iter([0, 91, 91, 92 if stop_finishes else 122])
    monkeypatch.setattr(migration.time, "monotonic", lambda: next(times))
    monkeypatch.setattr(migration.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(win32service, "QueryServiceStatus", query)
    monkeypatch.setattr(win32service, "ControlService", lambda *args: None)
    message = "90 seconds" if stop_finishes else "migration-only mode retained"
    with pytest.raises(RuntimeError, match=message):
        migration.configure_service_identity(root)
    assert state["account"] == "LocalSystem"
    if stop_finishes:
        assert observed[-1] == win32service.SERVICE_STOPPED
        assert state["environment"] == ["CUSTOM=preserved"]
    else:
        assert observed[-1] == win32service.SERVICE_STOP_PENDING
        assert migration.MIGRATION_ENV + "=1" in state["environment"]


def test_custom_account_refused_before_modification(installed):
    root, state = installed
    state["account"] = "DOMAIN\\custom"
    with pytest.raises(RuntimeError, match="Custom service accounts"):
        migration.configure_service_identity(root)
    assert state == {"environment": ["CUSTOM=preserved"], "account": "DOMAIN\\custom", "starts": 0}


def test_configured_media_paths_honor_service_override(tmp_path, monkeypatch):
    import json

    state = tmp_path / "station-state.json"
    media = tmp_path / "old-media"
    recordings = tmp_path / "recordings"
    state.write_text(
        json.dumps(
            {
                "station": {
                    "storage_locations": {
                        "media_library": str(media),
                        "recordings": str(recordings),
                    }
                }
            }
        )
    )
    for variable in (
        "CIVICCAST_STATION_MEDIA_LIBRARY",
        "CIVICCAST_STATION_RECORDINGS",
        "CIVICCAST_STATION_BACKUPS",
    ):
        monkeypatch.delenv(variable, raising=False)
    override = tmp_path / "selected-media"
    assert migration._configured_storage_paths(
        [f"CIVICCAST_STATION_MEDIA_LIBRARY={override}"], state
    ) == [override, recordings]


@pytest.mark.parametrize("service_environment", [False, True])
def test_registry_secret_grants_service_read_only_on_disposable_key(
    monkeypatch, service_environment
):
    import uuid
    import winreg

    import win32con
    import win32security

    from civiccast.native.service_identity import SERVICE_SID

    name = rf"Software\CivicCastIdentityTest\{uuid.uuid4().hex}"
    real_open = winreg.OpenKey
    key = winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, name, 0, winreg.KEY_ALL_ACCESS)
    child = winreg.CreateKeyEx(key, "PythonClass", 0, winreg.KEY_ALL_ACCESS)
    original_acl = win32security.GetSecurityInfo(
        int(key), win32security.SE_REGISTRY_KEY, win32security.DACL_SECURITY_INFORMATION
    ).GetSecurityDescriptorDacl()
    key.Close()
    try:

        def redirected_open(hive, path, reserved, access):
            assert hive == winreg.HKEY_LOCAL_MACHINE
            assert path == (
                migration.SERVICE_KEY if service_environment else r"SOFTWARE\CivicCast\Native"
            )
            return real_open(winreg.HKEY_CURRENT_USER, name, reserved, access)

        monkeypatch.setattr(winreg, "OpenKey", redirected_open)
        if service_environment:
            migration._grant_service_environment_read()
        else:
            migration._grant_database_key_read()
        with real_open(winreg.HKEY_CURRENT_USER, name, 0, win32con.READ_CONTROL) as readback:
            descriptor = win32security.GetSecurityInfo(
                int(readback),
                win32security.SE_REGISTRY_KEY,
                win32security.DACL_SECURITY_INFORMATION,
            )
            acl = descriptor.GetSecurityDescriptorDacl()
            grants = {
                win32security.ConvertSidToStringSid(acl.GetAce(i)[2]): acl.GetAce(i)[1]
                for i in range(acl.GetAceCount())
            }
            assert grants[SERVICE_SID] == winreg.KEY_READ
            assert set(grants) == {SERVICE_SID, "S-1-5-18", "S-1-5-32-544"}
        child_acl = win32security.GetSecurityInfo(
            int(child), win32security.SE_REGISTRY_KEY, win32security.DACL_SECURITY_INFORMATION
        ).GetSecurityDescriptorDacl()
        child_grants = {
            win32security.ConvertSidToStringSid(child_acl.GetAce(i)[2]): child_acl.GetAce(i)[1]
            for i in range(child_acl.GetAceCount())
        }
        assert child_grants[SERVICE_SID] == winreg.KEY_READ
        assert child_grants["S-1-5-18"] == winreg.KEY_ALL_ACCESS
        assert child_grants["S-1-5-32-544"] == winreg.KEY_ALL_ACCESS
    finally:
        child.Close()
        with real_open(winreg.HKEY_CURRENT_USER, name, 0, win32con.WRITE_DAC) as cleanup:
            win32security.SetSecurityInfo(
                int(cleanup),
                win32security.SE_REGISTRY_KEY,
                win32security.DACL_SECURITY_INFORMATION,
                None,
                None,
                original_acl,
                None,
            )
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, name + r"\PythonClass")
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, name)
