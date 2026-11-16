# SPDX-License-Identifier: Apache-2.0
# Copyright (c) The CivicCast Authors
"""Staff token commands on an installed station (rc.6.1 item 5).

The commands read the station database address from the admin-only registry
value into their own process, never print it, and can hand the bearer secret
over in an administrator-only file instead of on screen.
"""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import pytest
from typer.testing import CliRunner

import civiccast.cli as cli_module
from civiccast.auth.store import InMemoryStaffTokenStore
from civiccast.cli import app
from civiccast.native import private_files
from civiccast.native.supervisor import service_env

_FAKE_URL = "postgresql+psycopg://civiccast:NOT-A-REAL-PASSWORD-7c1e@127.0.0.1:5433/civiccast"


@pytest.fixture
def store(monkeypatch: pytest.MonkeyPatch) -> InMemoryStaffTokenStore:
    """Real CLI store builder, in-memory store; no database is touched."""

    memory = InMemoryStaffTokenStore()
    monkeypatch.setattr(cli_module, "PostgresStaffTokenStore", lambda _factory: memory)
    return memory


@pytest.fixture
def installed_station(monkeypatch: pytest.MonkeyPatch) -> None:
    """No DATABASE_URL in the shell; the registry holds it (faked)."""

    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setattr(cli_module.sys, "platform", "win32")

    real_ensure = service_env.ensure_database_url_env

    def ensure_with_fake_registry() -> None:
        # Real decision logic, fake registry reader: proves the registry branch.
        real_ensure(registry_reader=lambda: _FAKE_URL)

    monkeypatch.setattr(service_env, "ensure_database_url_env", ensure_with_fake_registry)


def _issue(args: list[str]) -> object:
    return CliRunner().invoke(
        app,
        ["token", "issue", "--operator-id", "helper-ai", "--display-name", "AI helper", *args],
    )


def test_issue_reads_registry_and_never_shows_database_url(
    installed_station: None, store: InMemoryStaffTokenStore
) -> None:
    result = _issue(["--scopes", "support_admin", "--json"])

    assert result.exit_code == 0, result.output
    assert os.environ["DATABASE_URL"] == _FAKE_URL  # this process only
    assert "NOT-A-REAL-PASSWORD" not in result.output
    assert "postgresql" not in result.output
    payload = json.loads(result.stdout)
    assert payload["scopes"] == ["support_admin"]
    assert len(store.list_tokens()) == 1


def test_list_and_revoke_use_registry_address(
    installed_station: None, store: InMemoryStaffTokenStore
) -> None:
    issued = store.issue_token(operator_id="helper-it", operator_display_name="IT helper")

    listed = CliRunner().invoke(app, ["token", "list"])
    revoked = CliRunner().invoke(
        app, ["token", "revoke", issued.metadata.token_id, "--reason", "job done"]
    )

    assert listed.exit_code == 0, listed.output
    assert issued.metadata.token_id in listed.output
    assert revoked.exit_code == 0, revoked.output
    assert store.list_tokens()[0].revoked_at is not None
    for output in (listed.output, revoked.output):
        assert "NOT-A-REAL-PASSWORD" not in output


def test_explicit_database_url_still_wins(
    monkeypatch: pytest.MonkeyPatch, store: InMemoryStaffTokenStore
) -> None:
    monkeypatch.setenv("DATABASE_URL", "sqlite:///explicit.db")
    monkeypatch.setattr(cli_module.sys, "platform", "win32")

    def must_not_run() -> None:
        raise AssertionError("registry must not be consulted when DATABASE_URL is set")

    monkeypatch.setattr(service_env, "ensure_database_url_env", must_not_run)

    assert CliRunner().invoke(app, ["token", "list"]).exit_code == 0


def test_not_elevated_gets_run_as_administrator_message(
    monkeypatch: pytest.MonkeyPatch, store: InMemoryStaffTokenStore
) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setattr(cli_module.sys, "platform", "win32")

    def denied() -> None:
        raise PermissionError(5, "Access is denied")

    monkeypatch.setattr(service_env, "ensure_database_url_env", denied)

    result = CliRunner().invoke(app, ["token", "list"])

    assert result.exit_code == 1
    assert "Run as administrator" in result.output


def test_missing_registry_value_names_the_key_not_a_value(
    monkeypatch: pytest.MonkeyPatch, store: InMemoryStaffTokenStore
) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setattr(cli_module.sys, "platform", "win32")

    def missing() -> None:
        raise service_env.DatabaseUrlUnavailableError("absent")

    monkeypatch.setattr(service_env, "ensure_database_url_env", missing)

    result = CliRunner().invoke(app, ["token", "list"])

    assert result.exit_code == 1
    assert r"SOFTWARE\CivicCast\Native" in result.output


def test_off_windows_without_database_url_keeps_old_message(
    monkeypatch: pytest.MonkeyPatch, store: InMemoryStaffTokenStore
) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setattr(cli_module.sys, "platform", "linux")

    result = CliRunner().invoke(app, ["token", "list"])

    assert result.exit_code == 1
    assert "DATABASE_URL must point at the CivicCast database" in result.output


def test_database_failure_does_not_echo_driver_text(
    installed_station: None, monkeypatch: pytest.MonkeyPatch, store: InMemoryStaffTokenStore
) -> None:
    from sqlalchemy.exc import OperationalError

    def fail() -> list[object]:
        raise OperationalError("SELECT 1", {}, Exception(_FAKE_URL))

    monkeypatch.setattr(store, "list_tokens", fail)

    result = CliRunner().invoke(app, ["token", "list"])

    assert result.exit_code == 1
    assert "did not answer" in result.output
    assert "NOT-A-REAL-PASSWORD" not in result.output


def test_revoke_unknown_token_is_a_clear_error(
    installed_station: None, store: InMemoryStaffTokenStore
) -> None:
    result = CliRunner().invoke(app, ["token", "revoke", "nope"])

    assert result.exit_code == 1
    assert "No staff token has id nope" in result.output


def test_issue_out_writes_secret_to_admin_only_file_not_stdout(
    installed_station: None, store: InMemoryStaffTokenStore, tmp_path: Path
) -> None:
    out = tmp_path / "helper-token.txt"

    result = _issue(["--out", str(out), "--json"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    secret = out.read_text(encoding="ascii").strip()
    assert secret.startswith(f"ccst_{payload['token_id']}_")
    assert "secret" not in payload
    assert payload["secret_file"] == str(out.absolute())
    assert secret not in result.output
    _assert_admin_only(out)
    assert list(tmp_path.iterdir()) == [out]  # no temporary sibling left


def test_issue_out_plain_text_mentions_file_only(
    installed_station: None, store: InMemoryStaffTokenStore, tmp_path: Path
) -> None:
    out = tmp_path / "helper-token.txt"

    result = _issue(["--out", str(out)])

    assert result.exit_code == 0, result.output
    assert "administrators only" in result.output
    assert out.read_text(encoding="ascii").strip() not in result.output


def test_issue_out_refuses_existing_file_before_issuing(
    installed_station: None, store: InMemoryStaffTokenStore, tmp_path: Path
) -> None:
    out = tmp_path / "helper-token.txt"
    out.write_text("keep me", encoding="utf-8")

    result = _issue(["--out", str(out)])

    assert result.exit_code == 1
    assert "already exists" in result.output
    assert out.read_text(encoding="utf-8") == "keep me"
    assert store.list_tokens() == []


def test_rotate_out_writes_replacement_to_file(
    installed_station: None, store: InMemoryStaffTokenStore, tmp_path: Path
) -> None:
    issued = store.issue_token(operator_id="helper-it", operator_display_name="IT helper")
    out = tmp_path / "rotated.txt"

    result = CliRunner().invoke(
        app, ["token", "rotate", issued.metadata.token_id, "--out", str(out), "--json"]
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert "secret" not in payload
    assert out.read_text(encoding="ascii").startswith(f"ccst_{payload['token_id']}_")
    _assert_admin_only(out)


def test_admin_only_writer_never_replaces_existing_file(tmp_path: Path) -> None:
    target = tmp_path / "existing.txt"
    target.write_bytes(b"original")

    with pytest.raises(FileExistsError):
        private_files.write_admin_only_new_file(target, b"secret")

    assert target.read_bytes() == b"original"
    assert list(tmp_path.iterdir()) == [target]


def _assert_admin_only(path: Path) -> None:
    # os.name, not sys.platform: the fixtures fake sys.platform as win32.
    if os.name != "nt":
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
        return
    import win32security

    from civiccast.certs.authority import _current_process_sid

    descriptor = win32security.GetFileSecurity(str(path), win32security.DACL_SECURITY_INFORMATION)
    control, _revision = descriptor.GetSecurityDescriptorControl()
    assert control & win32security.SE_DACL_PROTECTED
    acl = descriptor.GetSecurityDescriptorDacl()
    sids = {
        win32security.ConvertSidToStringSid(acl.GetAce(index)[2])
        for index in range(acl.GetAceCount())
    }
    # Writer, SYSTEM and Administrators only: no Users, no service identity.
    assert sids <= {_current_process_sid(), "S-1-5-18", "S-1-5-32-544"}
    assert "S-1-5-32-545" not in sids
