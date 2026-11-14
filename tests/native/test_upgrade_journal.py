# SPDX-License-Identifier: Apache-2.0
# Copyright (c) The CivicCast Authors
"""Journal state-machine + durable-persistence tests for the D3 upgrade engine.

These pin the transition GRAMMAR (the safety proof rests on it) and the
fail-loud/atomic persistence behavior that makes a resume trustworthy after a
power-loss kill. Pure — no Windows, no Postgres.
"""

from __future__ import annotations

import pytest

from civiccast.native.upgrade.journal import (
    JournalError,
    advance,
    journal_path,
    load_journal,
    write_journal,
)
from civiccast.native.upgrade.models import (
    UpgradeContext,
    UpgradeJournal,
    UpgradePhase,
)


def _context(tmp_path) -> UpgradeContext:
    return UpgradeContext(
        install_root=str(tmp_path / "install"),
        state_root=str(tmp_path / "state"),
        database_url="postgresql://u@localhost/db",
        owner_run_id="run-1",
    )


def _journal(tmp_path) -> UpgradeJournal:
    from civiccast.native.upgrade.models import UpgradePlan

    return UpgradeJournal(
        plan=UpgradePlan(old_version="1.0", new_version="1.1"),
        context=_context(tmp_path),
        phase=UpgradePhase.INIT,
    )


# --- phase ordering & frontier ------------------------------------------------


def test_forward_phase_ranks_are_strictly_increasing() -> None:
    forward = [
        UpgradePhase.INIT,
        UpgradePhase.INTERLOCK_ACQUIRED,
        UpgradePhase.WRITERS_DRAINED,
        UpgradePhase.BACKUP_VERIFIED,
        UpgradePhase.TREE_LAID,
        UpgradePhase.MIGRATED,
        UpgradePhase.HEALTH_GREEN,
        UpgradePhase.COMPLETE,
    ]
    ranks = [p.rank for p in forward]
    assert ranks == sorted(ranks)
    assert len(set(ranks)) == len(ranks)


def test_mutation_frontier_is_migrated() -> None:
    # Before MIGRATED: not after-frontier. At/after: yes (until terminal).
    assert not UpgradePhase.BACKUP_VERIFIED.is_after_mutation_frontier
    assert not UpgradePhase.TREE_LAID.is_after_mutation_frontier
    assert UpgradePhase.MIGRATED.is_after_mutation_frontier
    assert UpgradePhase.HEALTH_GREEN.is_after_mutation_frontier
    # Terminal states are never "after frontier" (they are done, not in-flight).
    assert not UpgradePhase.ROLLED_BACK.is_after_mutation_frontier
    assert not UpgradePhase.COMPLETE.is_after_mutation_frontier


def test_terminal_phases_flagged() -> None:
    assert UpgradePhase.COMPLETE.is_terminal
    assert UpgradePhase.ROLLED_BACK.is_terminal
    assert UpgradePhase.HALTED_RESTORE_FAILED.is_terminal
    assert UpgradePhase.REFUSED_NON_RESTORABLE.is_terminal
    assert not UpgradePhase.MIGRATED.is_terminal


# --- transition grammar -------------------------------------------------------


def test_advance_one_forward_boundary_ok(tmp_path) -> None:
    j = _journal(tmp_path)
    j2 = advance(j, UpgradePhase.INTERLOCK_ACQUIRED, "acquired")
    assert j2.phase is UpgradePhase.INTERLOCK_ACQUIRED
    assert j2.history[-1][0] == UpgradePhase.INTERLOCK_ACQUIRED.value


def test_advance_skipping_a_forward_boundary_is_rejected(tmp_path) -> None:
    j = _journal(tmp_path)
    with pytest.raises(JournalError, match="illegal forward transition"):
        advance(j, UpgradePhase.BACKUP_VERIFIED, "skipped two boundaries")


def test_advance_to_terminal_from_any_phase_ok(tmp_path) -> None:
    j = advance(_journal(tmp_path), UpgradePhase.INTERLOCK_ACQUIRED, "acquired")
    rolled = advance(j, UpgradePhase.ROLLED_BACK, "unwound")
    assert rolled.phase is UpgradePhase.ROLLED_BACK


def test_cannot_advance_from_a_terminal_phase(tmp_path) -> None:
    j = advance(_journal(tmp_path), UpgradePhase.ROLLED_BACK, "done")
    with pytest.raises(JournalError, match="terminal"):
        advance(j, UpgradePhase.COMPLETE, "should not be allowed")


def test_history_is_append_only(tmp_path) -> None:
    j = _journal(tmp_path)
    j = advance(j, UpgradePhase.INTERLOCK_ACQUIRED, "a")
    j = advance(j, UpgradePhase.WRITERS_DRAINED, "b")
    assert [h[0] for h in j.history] == [
        UpgradePhase.INTERLOCK_ACQUIRED.value,
        UpgradePhase.WRITERS_DRAINED.value,
    ]


# --- durable persistence ------------------------------------------------------


def test_write_then_load_roundtrips(tmp_path) -> None:
    j = advance(_journal(tmp_path), UpgradePhase.INTERLOCK_ACQUIRED, "acquired")
    write_journal(j)
    loaded = load_journal(j.context.state_root)
    assert loaded is not None
    assert loaded.phase is UpgradePhase.INTERLOCK_ACQUIRED
    assert loaded.plan.new_version == "1.1"


def test_load_missing_journal_returns_none(tmp_path) -> None:
    assert load_journal(str(tmp_path / "nonexistent")) is None


def test_load_corrupt_journal_raises_fail_loud(tmp_path) -> None:
    state_root = tmp_path / "state"
    state_root.mkdir()
    journal_path(state_root).write_text("{ this is not valid json", encoding="utf-8")
    with pytest.raises(JournalError, match=r"corrupt|unparseable"):
        load_journal(str(state_root))


def test_load_schema_drifted_journal_raises(tmp_path) -> None:
    # extra="forbid": an unknown field is a hard parse failure, not tolerated.
    state_root = tmp_path / "state"
    state_root.mkdir()
    journal_path(state_root).write_text(
        '{"schema_version": 1, "unexpected_field": true}', encoding="utf-8"
    )
    with pytest.raises(JournalError):
        load_journal(str(state_root))


def test_write_leaves_no_temp_files_behind(tmp_path) -> None:
    j = _journal(tmp_path)
    write_journal(j)
    state_root = j.context.state_root
    from pathlib import Path

    leftovers = [p.name for p in Path(state_root).glob("*.tmp")]
    assert leftovers == []


# --- security fix (2026-07-30): state-root ACL hardening ----------------------
#
# Mirrors civiccast/native/provision/journal.py's fix for the same class of
# defect (world-readable ProgramData state root on a shared municipal PC).
# Unlike that sibling fix, context.database_url is NOT redacted here -- see
# this module's docstring for why (a real consumer,
# orchestrator._write_recovery_document, reads it back from a RESUMED/loaded
# journal on the halt path).


def test_write_journal_invokes_state_root_acl_hardening(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The core of the "hardening code path is invoked when the location is
    written" requirement -- cross-platform (no real Win32 call here; see
    ``test_upgrade_journal_win.py`` for the REAL DACL-content proof,
    Windows-only). Spies on the real hardening function so this passes/fails
    on the actual wiring, not a restated assumption.

    PRIVILEGE TIER: none -- ``tmp_path`` only, no real Win32 call happens
    (the spy replaces it), so this behaves identically under an elevated or
    non-elevated token."""

    import civiccast.native.upgrade.journal as journal_module

    calls: list = []
    monkeypatch.setattr(
        journal_module,
        "_harden_state_root_acl",
        lambda state_root: calls.append(state_root),
    )

    j = _journal(tmp_path)
    write_journal(j)

    from pathlib import Path

    assert calls == [Path(j.context.state_root)]


def test_database_url_password_is_not_persisted(tmp_path) -> None:
    """Replaces the old plaintext characterization test. The password is
    redacted at write; resume/recovery take the real URL from the caller's
    live context (see ``with_live_database_url``), not from this file."""

    distinctive_url = "postgresql://svc:TEST-ONLY-DISTINCTIVE-9f3c7a1e@localhost/civiccast"
    j = _journal(tmp_path)
    j = j.model_copy(
        update={"context": j.context.model_copy(update={"database_url": distinctive_url})}
    )

    path = write_journal(j)

    raw_bytes = path.read_bytes()
    assert b"TEST-ONLY-DISTINCTIVE-9f3c7a1e" not in raw_bytes
    assert b"postgresql://svc:***@localhost/civiccast" in raw_bytes


# --- database password never reaches disk ------------------------------------

_FAKE_PASSWORD = "s3cret-test"
_URL_WITH_PASSWORD = f"postgresql://civic:{_FAKE_PASSWORD}@127.0.0.1:5432/civiccast"


def _journal_with_password(tmp_path) -> UpgradeJournal:
    base = _journal(tmp_path)
    context = base.context.model_copy(update={"database_url": _URL_WITH_PASSWORD})
    return advance(
        base.model_copy(update={"context": context}),
        UpgradePhase.INTERLOCK_ACQUIRED,
        f"connect failed for {_URL_WITH_PASSWORD} (password {_FAKE_PASSWORD})",
        error=f"could not reach {_URL_WITH_PASSWORD}",
    )


def test_written_journal_contains_no_database_password(tmp_path) -> None:
    journal = _journal_with_password(tmp_path)
    path = write_journal(journal)
    text = path.read_text(encoding="utf-8")
    assert _FAKE_PASSWORD not in text
    assert "postgresql://civic:***@127.0.0.1:5432/civiccast" in text
    assert load_journal(journal.context.state_root).context.database_url == (  # type: ignore[union-attr]
        "postgresql://civic:***@127.0.0.1:5432/civiccast"
    )
    # The in-memory journal the engine is driving keeps the real URL.
    assert journal.context.database_url == _URL_WITH_PASSWORD


def test_redact_database_url_shapes() -> None:
    from civiccast.native.upgrade.journal import redact_database_url

    assert redact_database_url("postgresql://u@localhost/db") == "postgresql://u@localhost/db"
    assert redact_database_url("postgresql://u:pw1234@h:5433/db") == "postgresql://u:***@h:5433/db"
    assert (
        redact_database_url("postgresql://h/db?user=u&password=pw1234")
        == "postgresql://h/db?user=u&password=***"
    )
    assert redact_database_url("not a url") == "not a url"


def test_with_live_database_url_restores_real_url(tmp_path) -> None:
    from civiccast.native.upgrade.journal import with_live_database_url

    journal = _journal_with_password(tmp_path)
    write_journal(journal)
    loaded = load_journal(journal.context.state_root)
    assert loaded is not None
    assert _FAKE_PASSWORD not in loaded.context.database_url
    live = with_live_database_url(loaded, _URL_WITH_PASSWORD)
    assert live.context.database_url == _URL_WITH_PASSWORD
