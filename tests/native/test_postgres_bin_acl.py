# SPDX-License-Identifier: Apache-2.0
# Copyright (c) The CivicCast Authors
"""Tests for the PostgreSQL binary-tree ACL grant
(:mod:`civiccast.native.postgres_bin_acl`, rc.6 -> rc.7 Dell upgrade blocker).

ACE rendering, DACL merging, the folder chain and the read-back check are
pure. The tests that drive the Windows branch through injected seams still
require native Windows path semantics; they run in the Windows lane. The REAL
Win32 call is proven in ``test_postgres_bin_acl_win.py``.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from civiccast.native import postgres_bin_acl
from civiccast.native.pgdata_acl import PgDataAclError
from civiccast.native.postgres_bin_acl import (
    FAILURE_STEP,
    RX_MASK_SDDL,
    bin_rx_ace,
    has_rx_ace,
    merge_dacl_ace,
    normalize_postgres_bin_acl,
    postgres_prefix_dir,
    server_bin_traversal_chain,
)

_SID = "S-1-5-21-1662294811-121888399-200151778-1001"

_WINDOWS_PATH_TEST = pytest.mark.skipif(
    os.name != "nt", reason="requires native Windows path semantics"
)

#: The Dell's install-root DACL shape (issue report, 2026-11-19): SYSTEM,
#: Administrators and the service -- and nothing for the installing account.
_DELL_ROOT_DACL = "D:(A;OICI;FA;;;SY)(A;OICI;FA;;;BA)"


@pytest.fixture
def on_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(postgres_bin_acl.os, "name", "nt")


class _FakeDacl:
    """In-memory DACL per path plus a record of every write."""

    def __init__(self, current: str = _DELL_ROOT_DACL) -> None:
        self.current = current
        self.by_path: dict[str, str] = {}
        self.writes: list[tuple[str, str]] = []

    def read(self, path: str) -> str:
        return self.by_path.get(path, self.current)

    def apply(self, path: str, sddl: str) -> None:
        self.writes.append((path, sddl))
        self.by_path[path] = sddl


def _server_bin(tmp_path: Path) -> Path:
    bin_dir = tmp_path / "install" / "packs" / "native-server-binaries" / "payload" / "bin"
    bin_dir.mkdir(parents=True)
    return bin_dir


# --- the ACE itself (pure) ---------------------------------------------------


def test_bin_ace_grants_read_and_execute_only() -> None:
    ace = bin_rx_ace(_SID, inheritable=True)

    assert ace == f"(A;OICI;{RX_MASK_SDDL};;;{_SID})"
    assert RX_MASK_SDDL == "0x1200a9"
    assert int(RX_MASK_SDDL, 16) & 0x40000000 == 0, "must never grant write (GENERIC_WRITE)"
    for broad in (";;;BU)", ";;;AU)", ";;;WD)", ";;;IU)"):
        assert broad not in ace, f"must never grant {broad}"


def test_bin_ace_is_non_inheritable_for_traverse_only_ancestors() -> None:
    assert bin_rx_ace(_SID, inheritable=False) == f"(A;;{RX_MASK_SDDL};;;{_SID})"


@pytest.mark.parametrize(
    "bogus",
    ["", "BigDelli7", "S-1-5", "BA)(A;OICI;GA;;;WD", "S-1-5-21-abc", None],
)
def test_bin_ace_refuses_anything_that_is_not_a_textual_sid(bogus: object) -> None:
    with pytest.raises(PgDataAclError, match="not a textual SID"):
        bin_rx_ace(bogus, inheritable=True)  # type: ignore[arg-type]


# --- merging keeps the existing descriptor -----------------------------------


def test_merge_adds_the_ace_first_and_keeps_everything_else() -> None:
    ace = bin_rx_ace(_SID, inheritable=True)

    merged = merge_dacl_ace(_DELL_ROOT_DACL, ace)

    assert merged == "D:" + ace + _DELL_ROOT_DACL[len("D:") :]
    assert "(A;OICI;FA;;;SY)" in merged
    assert "(A;OICI;FA;;;BA)" in merged


def test_merge_places_the_allow_after_the_last_leading_explicit_deny() -> None:
    """Windows stops at the first matching ACE, so the new allow must not
    jump ahead of an explicit deny and defeat it."""

    ace = bin_rx_ace(_SID, inheritable=True)
    deny = f"(D;;0x1200a9;;;{_SID})"

    merged = merge_dacl_ace("D:" + deny + "(A;OICI;FA;;;SY)", ace)

    assert merged == "D:" + deny + ace + "(A;OICI;FA;;;SY)"


def test_merge_stops_at_the_first_non_deny_ace() -> None:
    ace = bin_rx_ace(_SID, inheritable=True)
    deny = "(D;;0x1200a9;;;S-1-5-21-1)"
    other_deny = "(D;;0x1200a9;;;S-1-5-21-2)"

    merged = merge_dacl_ace(
        "D:" + deny + other_deny + "(A;OICI;FA;;;SY)" + "(D;;GA;;;S-1-5-21-3)", ace
    )

    assert merged == "D:" + deny + other_deny + ace + "(A;OICI;FA;;;SY)(D;;GA;;;S-1-5-21-3)"


def test_merge_leaves_inherited_aces_after_the_new_allow() -> None:
    """An inherited deny (``ID`` flag) is not explicit: it stays after the
    explicit allows, exactly as canonical order requires."""

    ace = bin_rx_ace(_SID, inheritable=True)
    inherited_deny = "(D;ID;0x1200a9;;;S-1-5-21-4)"

    merged = merge_dacl_ace("D:AI" + inherited_deny + "(A;OICI;FA;;;SY)", ace)

    assert merged == "D:AI" + ace + inherited_deny + "(A;OICI;FA;;;SY)"


@pytest.mark.parametrize("header", ["D:P", "D:AI", "D:PAI", "D:"])
def test_merge_keeps_the_dacl_header_flags_verbatim(header: str) -> None:
    ace = bin_rx_ace(_SID, inheritable=False)

    assert merge_dacl_ace(header + "(A;;FA;;;SY)", ace) == header + ace + "(A;;FA;;;SY)"


def test_merge_handles_an_empty_dacl() -> None:
    ace = bin_rx_ace(_SID, inheritable=False)

    assert merge_dacl_ace("D:P", ace) == "D:P" + ace


def test_merge_refuses_a_non_dacl_string() -> None:
    with pytest.raises(PgDataAclError, match="not an SDDL DACL"):
        merge_dacl_ace("O:SYG:SY", bin_rx_ace(_SID, inheritable=True))


# --- which folders get the ACE ------------------------------------------------


def test_traversal_chain_starts_at_the_payload_prefix_and_ends_at_the_install_root(
    tmp_path: Path,
) -> None:
    bin_dir = _server_bin(tmp_path)
    install_root = tmp_path / "install"

    chain = server_bin_traversal_chain(bin_dir)

    assert chain[0] == bin_dir.parent, (
        "the inheritable ACE goes on bin's PARENT (payload) so lib and share inherit it too"
    )
    assert chain[0].name == "payload"
    assert chain[-1] == install_root, "nothing above the install root may be touched"
    assert [p.name for p in chain] == [
        "payload",
        "native-server-binaries",
        "packs",
        "install",
    ]
    assert bin_dir not in chain, "bin is covered by inheritance from payload"


def test_prefix_dir_falls_back_to_bin_when_it_is_not_inside_packs(tmp_path: Path) -> None:
    bin_dir = tmp_path / "elsewhere" / "bin"
    bin_dir.mkdir(parents=True)

    assert postgres_prefix_dir(bin_dir) == bin_dir
    assert server_bin_traversal_chain(bin_dir) == [bin_dir]


def test_prefix_dir_is_bin_when_bin_sits_directly_under_packs(tmp_path: Path) -> None:
    bin_dir = tmp_path / "install" / "packs" / "bin"
    bin_dir.mkdir(parents=True)

    assert postgres_prefix_dir(bin_dir) == bin_dir
    assert [p.name for p in server_bin_traversal_chain(bin_dir)] == ["bin", "packs", "install"]


# --- the read-back check ------------------------------------------------------


def test_has_rx_ace_matches_the_ace_this_module_writes() -> None:
    dacl = merge_dacl_ace(_DELL_ROOT_DACL, bin_rx_ace(_SID, inheritable=True))

    assert has_rx_ace(dacl, _SID, inheritable=True)
    assert not has_rx_ace(_DELL_ROOT_DACL, _SID, inheritable=True)


def test_has_rx_ace_accepts_windows_alias_rendering() -> None:
    dacl = f"D:(A;OICI;GA;;;{_SID})"

    assert has_rx_ace(dacl, _SID, inheritable=True)


def test_has_rx_ace_requires_inheritance_only_when_asked() -> None:
    dacl = f"D:(A;;{RX_MASK_SDDL};;;{_SID})"

    assert has_rx_ace(dacl, _SID, inheritable=False)
    assert not has_rx_ace(dacl, _SID, inheritable=True)


def test_has_rx_ace_ignores_a_write_only_or_other_sid_ace() -> None:
    write_only = f"D:(A;OICI;0x120116;;;{_SID})"
    other = "D:(A;OICI;GA;;;S-1-5-18)"

    assert not has_rx_ace(write_only, _SID, inheritable=True)
    assert not has_rx_ace(other, _SID, inheritable=True)


# --- normalize_postgres_bin_acl (decision logic over injected seams) ----------


@pytest.mark.windows_only
@_WINDOWS_PATH_TEST
def test_normalize_grants_the_payload_prefix_inheritably_and_ancestors_for_traverse(
    tmp_path: Path, on_windows: None
) -> None:
    bin_dir = _server_bin(tmp_path)
    payload = bin_dir.parent
    dacl = _FakeDacl()

    applied = normalize_postgres_bin_acl(
        bin_dir / "pg_ctl.exe", sid_reader=lambda: _SID, read_dacl=dacl.read, apply_dacl=dacl.apply
    )

    assert applied is not None
    assert next(path for path, _sddl in applied) == str(payload)
    written = dict(applied)
    assert bin_rx_ace(_SID, inheritable=True) in written[str(payload)]
    assert str(bin_dir) not in written, "bin inherits from payload; it needs no ACE of its own"
    for ancestor in server_bin_traversal_chain(bin_dir)[1:]:
        assert bin_rx_ace(_SID, inheritable=False) in written[str(ancestor)]
        assert bin_rx_ace(_SID, inheritable=True) not in written[str(ancestor)]


@pytest.mark.windows_only
@_WINDOWS_PATH_TEST
def test_normalize_is_idempotent(tmp_path: Path, on_windows: None) -> None:
    bin_dir = _server_bin(tmp_path)
    already = merge_dacl_ace(_DELL_ROOT_DACL, bin_rx_ace(_SID, inheritable=True))
    dacl = _FakeDacl(current=already)

    applied = normalize_postgres_bin_acl(
        bin_dir / "pg_ctl.exe", sid_reader=lambda: _SID, read_dacl=dacl.read, apply_dacl=dacl.apply
    )

    assert applied == []
    assert dacl.writes == []


@pytest.mark.windows_only
@_WINDOWS_PATH_TEST
def test_normalize_falls_back_to_an_inheritable_ace_on_bin_outside_a_packs_tree(
    tmp_path: Path, on_windows: None
) -> None:
    bin_dir = tmp_path / "elsewhere" / "bin"
    bin_dir.mkdir(parents=True)
    dacl = _FakeDacl()

    applied = normalize_postgres_bin_acl(
        bin_dir / "pg_ctl.exe", sid_reader=lambda: _SID, read_dacl=dacl.read, apply_dacl=dacl.apply
    )

    assert applied is not None
    assert len(applied) == 1
    path, sddl = applied[0]
    assert path == str(bin_dir)
    assert bin_rx_ace(_SID, inheritable=True) in sddl


def test_normalize_is_a_noop_off_windows(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(postgres_bin_acl.os, "name", "posix")

    def _never_called() -> str:  # pragma: no cover - asserted not to run
        raise AssertionError("the SID must not be read off Windows")

    assert normalize_postgres_bin_acl(tmp_path / "pg_ctl.exe", sid_reader=_never_called) is None


@pytest.mark.windows_only
@_WINDOWS_PATH_TEST
def test_normalize_ignores_a_bare_binary_name(on_windows: None) -> None:
    """A bare ``pg_ctl`` resolved through PATH names no product tree; the
    process's current directory must never be re-ACLed."""

    def _never_called() -> str:  # pragma: no cover - asserted not to run
        raise AssertionError("nothing to grant for a bare binary name")

    assert normalize_postgres_bin_acl("pg_ctl", sid_reader=_never_called) == []


@pytest.mark.windows_only
@_WINDOWS_PATH_TEST
def test_normalize_fails_loud_when_the_bin_folder_is_missing(
    tmp_path: Path, on_windows: None
) -> None:
    missing = tmp_path / "install" / "packs" / "bin"
    dacl = _FakeDacl()

    with pytest.raises(PgDataAclError) as excinfo:
        normalize_postgres_bin_acl(
            missing / "pg_ctl.exe",
            sid_reader=lambda: _SID,
            read_dacl=dacl.read,
            apply_dacl=dacl.apply,
        )

    assert FAILURE_STEP in str(excinfo.value)
    assert repr(str(missing)) in str(excinfo.value)
    assert dacl.writes == []


@pytest.mark.windows_only
@_WINDOWS_PATH_TEST
def test_normalize_fails_loud_when_the_caller_sid_cannot_be_read(
    tmp_path: Path, on_windows: None
) -> None:
    bin_dir = _server_bin(tmp_path)
    dacl = _FakeDacl()

    def _boom() -> str:
        raise OSError("token query refused")

    with pytest.raises(PgDataAclError) as excinfo:
        normalize_postgres_bin_acl(
            bin_dir / "pg_ctl.exe", sid_reader=_boom, read_dacl=dacl.read, apply_dacl=dacl.apply
        )

    assert FAILURE_STEP in str(excinfo.value)
    assert "token query refused" in str(excinfo.value)
    assert dacl.writes == []


@pytest.mark.windows_only
@_WINDOWS_PATH_TEST
def test_normalize_fails_loud_when_windows_refuses_the_dacl(
    tmp_path: Path, on_windows: None
) -> None:
    bin_dir = _server_bin(tmp_path)

    def _refuse(path: str, sddl: str) -> None:
        raise PermissionError("access denied writing the DACL")

    with pytest.raises(PgDataAclError) as excinfo:
        normalize_postgres_bin_acl(
            bin_dir / "pg_ctl.exe",
            sid_reader=lambda: _SID,
            read_dacl=_FakeDacl().read,
            apply_dacl=_refuse,
        )

    assert FAILURE_STEP in str(excinfo.value)
    assert "access denied writing the DACL" in str(excinfo.value)
    assert repr(str(bin_dir.parent)) in str(excinfo.value)


@pytest.mark.windows_only
@_WINDOWS_PATH_TEST
def test_normalize_fails_loud_on_a_null_dacl(tmp_path: Path, on_windows: None) -> None:
    bin_dir = _server_bin(tmp_path)

    def _null(path: str) -> str:
        raise PgDataAclError(f"{FAILURE_STEP}: NULL DACL")

    with pytest.raises(PgDataAclError, match="NULL DACL"):
        normalize_postgres_bin_acl(
            bin_dir / "pg_ctl.exe",
            sid_reader=lambda: _SID,
            read_dacl=_null,
            apply_dacl=lambda p, s: None,
        )
