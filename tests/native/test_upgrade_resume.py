# SPDX-License-Identifier: Apache-2.0
# Copyright (c) The CivicCast Authors
"""Resume-from-each-boundary idempotency (simulated power loss at every phase).

A kill is simulated by constructing a journal persisted at a mid-flight phase
(with the world left in the state that phase implies) and re-running
``run_upgrade``. The re-run must COMPLETE cleanly (forward phases) or, when the
recorded phase is past the mutation frontier and the remaining steps fail, ROLL
BACK cleanly — never leave a half state. This is the core auditability property
of the slice.
"""

from __future__ import annotations

from pathlib import Path

from civiccast.native.upgrade import junction
from civiccast.native.upgrade.journal import write_journal
from civiccast.native.upgrade.models import (
    BackupRef,
    UpgradeContext,
    UpgradeJournal,
    UpgradePhase,
    UpgradePlan,
)
from civiccast.native.upgrade.orchestrator import run_upgrade
from tests.native.test_upgrade_orchestrator import Harness


def _make(tmp_path) -> Harness:
    install_root = tmp_path / "install"
    state_root = tmp_path / "state"
    payload = tmp_path / "payload"
    payload.mkdir()
    (payload / "civiccast.exe").write_text("payload", encoding="utf-8")
    old_tree = install_root / "app" / "1.0"
    old_tree.mkdir(parents=True)
    (old_tree / "civiccast.exe").write_text("1.0", encoding="utf-8")
    junction.point_current_at(install_root, old_tree)
    return Harness(install_root=install_root, state_root=state_root, payload_source=payload)


def _context(h: Harness) -> UpgradeContext:
    return UpgradeContext(
        install_root=str(h.install_root),
        state_root=str(h.state_root),
        database_url="postgresql://u@localhost/db",
        owner_run_id="run-1",
    )


def _seed_journal(h: Harness, phase: UpgradePhase, **fields) -> None:
    """Persist a journal at ``phase`` to simulate a kill at that boundary."""

    journal = UpgradeJournal(
        plan=UpgradePlan(old_version="1.0", new_version="1.1"),
        context=_context(h),
        phase=phase,
        **fields,
    )
    write_journal(journal)


def test_resume_from_interlock_acquired_completes(tmp_path) -> None:
    h = _make(tmp_path)
    h.interlock_held = True  # world state: interlock was taken before the kill
    _seed_journal(h, UpgradePhase.INTERLOCK_ACQUIRED, pre_schema_revision="old-rev")
    outcome = run_upgrade(UpgradePlan(old_version="1.0", new_version="1.1"), _context(h), h.seams())
    assert outcome.phase is UpgradePhase.COMPLETE
    # Resume did NOT re-acquire the interlock (already held); it drove forward.
    assert "acquire_interlock" not in h.calls
    assert Path(h.read_junction()).name == "1.1"


def test_resume_from_backup_verified_completes(tmp_path) -> None:
    h = _make(tmp_path)
    h.interlock_held = True
    h.backup_snapshot = "old-rev"
    backup = BackupRef(
        backup_id="b",
        backup_dir=str(h.state_root / "backups" / "pre-1.1"),
        manifest_hash="a" * 64,
        db_artifact="database.pgdump",
        verified=True,
        restore_drill_ok=True,
    )
    Path(backup.backup_dir).mkdir(parents=True)
    _seed_journal(h, UpgradePhase.BACKUP_VERIFIED, pre_schema_revision="old-rev", backup=backup)
    outcome = run_upgrade(UpgradePlan(old_version="1.0", new_version="1.1"), _context(h), h.seams())
    assert outcome.phase is UpgradePhase.COMPLETE
    assert "backup" not in h.calls  # not re-taken
    assert h.db_schema == "new-rev"


def test_resume_from_migrated_completes_when_health_green(tmp_path) -> None:
    h = _make(tmp_path)
    h.interlock_held = True
    h.db_schema = "new-rev"  # migration already applied before the kill
    new_tree = h.install_root / "app" / "1.1"
    new_tree.mkdir(parents=True)
    junction.point_current_at(h.install_root, new_tree)  # junction already flipped
    backup = BackupRef(
        backup_id="b",
        backup_dir=str(h.state_root / "backups" / "pre-1.1"),
        manifest_hash="a" * 64,
        db_artifact="database.pgdump",
        verified=True,
        restore_drill_ok=True,
    )
    Path(backup.backup_dir).mkdir(parents=True)
    _seed_journal(
        h,
        UpgradePhase.MIGRATED,
        pre_schema_revision="old-rev",
        post_schema_revision="new-rev",
        backup=backup,
        previous_junction_target=str((h.install_root / "app" / "1.0").resolve()),
        new_junction_target=str(new_tree.resolve()),
    )
    outcome = run_upgrade(UpgradePlan(old_version="1.0", new_version="1.1"), _context(h), h.seams())
    assert outcome.phase is UpgradePhase.COMPLETE
    assert "migrate" not in h.calls  # not re-run
    assert Path(h.read_junction()).name == "1.1"


def test_resume_from_migrated_rolls_back_when_health_fails(tmp_path) -> None:
    h = _make(tmp_path)
    h.interlock_held = True
    h.db_schema = "new-rev"
    h.fail_health = True  # health still red on resume
    h.backup_snapshot = "old-rev"
    new_tree = h.install_root / "app" / "1.1"
    new_tree.mkdir(parents=True)
    junction.point_current_at(h.install_root, new_tree)
    backup_dir = h.state_root / "backups" / "pre-1.1"
    backup_dir.mkdir(parents=True)
    (backup_dir / "database.pgdump").write_text("old-rev", encoding="utf-8")
    backup = BackupRef(
        backup_id="b",
        backup_dir=str(backup_dir),
        manifest_hash="a" * 64,
        db_artifact="database.pgdump",
        verified=True,
        restore_drill_ok=True,
    )
    prev = str((h.install_root / "app" / "1.0").resolve())
    _seed_journal(
        h,
        UpgradePhase.MIGRATED,
        pre_schema_revision="old-rev",
        post_schema_revision="new-rev",
        backup=backup,
        previous_junction_target=prev,
        new_junction_target=str(new_tree.resolve()),
    )
    outcome = run_upgrade(UpgradePlan(old_version="1.0", new_version="1.1"), _context(h), h.seams())
    assert outcome.phase is UpgradePhase.ROLLED_BACK
    assert "restore_backup" in h.calls
    assert Path(h.read_junction()).name == "1.0"
    assert h.db_schema == "old-rev"


def test_resume_over_terminal_failure_journal_does_not_bulldoze(tmp_path) -> None:
    """A preserved HALTED journal must NOT be silently overwritten by a fresh
    run -- and the refusal must say THAT, not replay the old run's outcome.

    <installer-path-audit BL-06> The refusal used to return the STALE journal's
    own phase and error verbatim, so the CLI logged
    "upgrade outcome: rolled_back" (or halted) with the PREVIOUS run's reason
    and returned that phase's exit code. Post-#143 an exit 10 is a fatal NSIS
    124 telling the operator to "re-run setup after resolving the cause" --
    and every re-run returned 10 again, forever, whatever was fixed, because
    nothing deletes this journal. The refusal now has its own phase, its own
    exit code, and text naming the file to move.
    """
    h = _make(tmp_path)
    _seed_journal(h, UpgradePhase.HALTED_RESTORE_FAILED, error="prior halt")
    outcome = run_upgrade(UpgradePlan(old_version="1.0", new_version="1.1"), _context(h), h.seams())
    assert outcome.phase is UpgradePhase.REFUSED_STALE_JOURNAL
    assert h.calls == []  # nothing re-run over a preserved recovery point
    # The refusal names the earlier phase, the file to move, and says plainly
    # that THIS run did nothing.
    assert outcome.journal.error is not None
    assert "halted_restore_failed" in outcome.journal.error
    assert "upgrade-journal.json" in outcome.journal.error
    assert "did NOTHING" in outcome.journal.error
    assert "prior halt" in outcome.journal.error, (
        "the previous run's own reason must still be surfaced, as context rather than as this "
        "run's outcome"
    )


def test_the_preserved_journal_on_disk_is_not_rewritten_by_the_refusal(tmp_path) -> None:
    """<installer-path-audit BL-06> The recovery point survives byte-for-byte.

    The refusal is an in-memory copy: rewriting the journal to record the
    refusal would destroy the very artifact the refusal exists to protect.
    """
    from civiccast.native.upgrade.journal import journal_path

    h = _make(tmp_path)
    _seed_journal(h, UpgradePhase.HALTED_RESTORE_FAILED, error="prior halt")
    path = journal_path(Path(h.state_root))
    before = path.read_bytes()
    run_upgrade(UpgradePlan(old_version="1.0", new_version="1.1"), _context(h), h.seams())
    assert path.read_bytes() == before


def test_the_stale_journal_refusal_has_its_own_exit_code(tmp_path) -> None:
    """<installer-path-audit BL-06> and it is NOT ROLLED_BACK's.

    A terminal exit code that asserts an outcome this run never produced is
    the exact shape PR #143 was written to end; making it fatal converted a
    misleading log line into a bricked upgrade path.
    """
    from civiccast.native.upgrade.__main__ import _EXIT_CODES

    stale = _EXIT_CODES[UpgradePhase.REFUSED_STALE_JOURNAL]
    assert stale == 31
    for phase, code in _EXIT_CODES.items():
        if phase is not UpgradePhase.REFUSED_STALE_JOURNAL:
            assert code != stale, f"{phase} shares the stale-journal exit code"


def test_resume_is_deterministic_across_double_run(tmp_path) -> None:
    # Running the SAME completed upgrade's journal again is a no-op re-read:
    # COMPLETE stays COMPLETE, world unchanged.
    h = _make(tmp_path)
    first = run_upgrade(UpgradePlan(old_version="1.0", new_version="1.1"), _context(h), h.seams())
    assert first.phase is UpgradePhase.COMPLETE
    calls_after_first = list(h.calls)
    second = run_upgrade(UpgradePlan(old_version="1.0", new_version="1.1"), _context(h), h.seams())
    assert second.phase is UpgradePhase.COMPLETE
    # A COMPLETE journal re-run starts fresh (previous upgrade committed) — but
    # the world is already at the target, so it still lands COMPLETE.
    assert Path(h.read_junction()).name == "1.1"
    # <batch-fix-list item 14> `assert len(calls_after_first) >= 1` was
    # VACUOUS: it inspected the FIRST run's call list -- which obviously ran
    # steps -- and never looked at the SECOND run's at all. So a second run
    # that behaved completely differently still passed, in the one test whose
    # name is "deterministic across a double run".
    #
    # What determinism means here, given run_upgrade's own contract (a
    # COMPLETE journal is the ONE terminal phase it starts fresh from, because
    # the previous upgrade committed): the second run walks the SAME sequence
    # and leaves the world in the SAME state. Every forward step is
    # individually idempotent, which is what makes that safe -- and this
    # asserts it rather than assuming it.
    calls_after_second = h.calls[len(calls_after_first) :]
    assert calls_after_first, "the first run must have driven the sequence"
    assert calls_after_second == calls_after_first, (
        "the second run must walk the identical sequence; "
        f"first={calls_after_first} second={calls_after_second}"
    )
    assert h.db_schema == "new-rev", "the committed schema must be unchanged by the re-run"
    assert h.interlock_held is False, "the re-run must leave the interlock released"
    assert second.journal.post_schema_revision == "new-rev"


def test_resume_works_when_persisted_journal_has_redacted_password(tmp_path) -> None:
    """The on-disk journal never holds the DB password; resume gets the real URL from
    the caller's context (the live config), and the password stays off disk."""

    fake_password = "s3cret-test"
    url = f"postgresql://civic:{fake_password}@localhost:5432/db"
    h = _make(tmp_path)
    h.interlock_held = True
    context = _context(h).model_copy(update={"database_url": url})
    write_journal(
        UpgradeJournal(
            plan=UpgradePlan(old_version="1.0", new_version="1.1"),
            context=context,
            phase=UpgradePhase.INTERLOCK_ACQUIRED,
            pre_schema_revision="old-rev",
        )
    )
    journal_file = Path(h.state_root) / "upgrade-journal.json"
    assert fake_password not in journal_file.read_text(encoding="utf-8")

    outcome = run_upgrade(UpgradePlan(old_version="1.0", new_version="1.1"), context, h.seams())

    assert outcome.phase is UpgradePhase.COMPLETE
    assert outcome.journal.context.database_url == url  # live value, in memory only
    assert fake_password not in journal_file.read_text(encoding="utf-8")
