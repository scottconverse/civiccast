# SPDX-License-Identifier: Apache-2.0
# Copyright (c) The CivicCast Authors
"""Durable, power-loss-resilient persistence for the upgrade journal.

The journal is the ONLY thing a resuming process trusts after a kill. Two
properties make that trust sound:

* **Atomic writes.** Every persist writes a sibling temp file, fsyncs it, and
  ``os.replace``\\s it over the real journal. ``os.replace`` is atomic on both
  POSIX and Windows (``MoveFileEx`` with ``REPLACE_EXISTING``), so a power loss
  during a write leaves EITHER the old complete journal OR the new complete
  journal on disk -- never a half-written one. A reader therefore never parses
  a torn record.
* **Fail-loud load.** The journal is parsed through
  :class:`~civiccast.native.upgrade.models.UpgradeJournal` (``extra="forbid"``);
  a truncated, schema-drifted, or hand-edited journal raises
  :class:`JournalError` instead of resuming from a value we cannot trust.

The journal file lives under the upgrade state root (ProgramData), NOT the
install root, so it survives the ``current`` junction/tree swap that the very
upgrade it describes performs.

SECURITY (municipal-shared-PC fix, 2026-07-30; mirrors
:mod:`civiccast.native.provision.journal`'s fix for the same class of defect
in that sibling module -- see its module docstring for the fuller narrative):

1. **The state root's own DACL is hardened** to SYSTEM + Administrators + the
   calling process's own account, on every :func:`write_journal` call -- see
   :func:`_harden_state_root_acl`. Before this fix the directory was created
   with a bare ``mkdir`` and inherited ``C:\\ProgramData``'s default DACL,
   where ``BUILTIN\\Users`` has ``ReadAndExecute`` -- world-readable on a
   shared station. This also covers the pre-upgrade ``pg_dump`` backups the
   engine writes under ``state_root/backups/pre-<version>/``
   (:func:`civiccast.native.upgrade.orchestrator._drive_forward`'s
   ``BACKUP_VERIFIED`` step, via :mod:`civiccast.dr.backup`): that directory
   is created with a plain ``mkdir`` too, AFTER the state root has already
   been hardened by the phase-0 :func:`write_journal` call in
   :func:`civiccast.native.upgrade.orchestrator.run_upgrade`, so it inherits
   the hardened, non-``BUILTIN\\Users`` DACL via ordinary NTFS ACE
   inheritance (the SDDL's ``OICI`` flags) rather than needing its own
   hardening call.

2. **``context.database_url``'s password is redacted before it is written**
   (:func:`redact_database_url`; ``postgresql://user:***@host:5432/db``).
   An earlier revision of this module left it in plaintext on the theory that
   :func:`civiccast.native.upgrade.orchestrator._write_recovery_document`
   reads it back on a resumed run. Nothing actually needs the PERSISTED
   copy: the caller of :func:`~civiccast.native.upgrade.orchestrator.run_upgrade`
   always supplies the real ``UpgradeContext`` (the CLI's ``--database-url``),
   and ``run_upgrade`` re-attaches that live URL to a journal it loaded
   (:func:`with_live_database_url`), so resume and the recovery document
   still see the real value in memory. The password therefore never reaches
   disk, including the copy the installer's failure-diagnostics step makes
   (``install-transaction.ps1``). Any free-text field (history detail,
   error) that embeds the literal URL or password is scrubbed too.
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import quote, unquote, urlsplit, urlunsplit

from pydantic import ValidationError

from civiccast.native.upgrade.models import UpgradeJournal, UpgradePhase

JOURNAL_FILENAME = "upgrade-journal.json"

#: Replaces the password in a persisted connection URL.
REDACTED_PASSWORD = "***"  # noqa: S105 -- a redaction marker, not a password

#: SYSTEM + Administrators full control, PLUS the CURRENT process's own user
#: SID -- identical shape and rationale to
#: ``civiccast.native.provision.journal._STATE_ROOT_SDDL_TEMPLATE``; see that
#: constant's docstring for the full trap narrative (hardening to ONLY
#: SYSTEM+Administrators locks the writing process out of its own SECOND
#: write, because ordinary data access on a subsequent freshly-opened handle
#: is not an owner-implicit right the way ``WRITE_DAC``/``READ_CONTROL``
#: are). Inheritable (``OICI``) so the ``backups/pre-<version>/`` pg_dump
#: directory created under this state root inherits the same hardening
#: without a hardening call of its own. Protected (``P``) so ProgramData's
#: inherited, world-readable default DACL cannot flow back onto this
#: directory on a future create/reboot/GPO refresh.
_STATE_ROOT_SDDL_TEMPLATE = "D:P(A;OICI;GA;;;SY)(A;OICI;GA;;;BA)(A;OICI;GA;;;{caller_sid})"


def _current_process_sid_sddl() -> str:
    """The current process token's user SID, as an SDDL ``S-1-...`` string --
    the piece :data:`_STATE_ROOT_SDDL_TEMPLATE` needs and can only be known at
    runtime (there is no fixed "the installer's account" SID to hard-code).

    Identical to ``civiccast.native.provision.journal``'s function of the
    same name; not shared to keep each module importable standalone (both
    are Windows-only lazy imports, per the house rule that ``import
    civiccast.native.*`` must succeed on Linux)."""

    import win32api
    import win32security

    token = win32security.OpenProcessToken(win32api.GetCurrentProcess(), win32security.TOKEN_QUERY)
    sid, _attributes = win32security.GetTokenInformation(token, win32security.TokenUser)
    # str(): pywin32 ships no type stubs, so this call is Any and the
    # declared -> str was unenforced. Coercing makes the annotation true
    # at runtime instead of merely claimed.
    return str(win32security.ConvertSidToStringSid(sid))


def _harden_state_root_acl(state_root: Path) -> None:
    """Restrict ``state_root``'s own DACL to SYSTEM + Administrators + the
    calling process's own account (see :data:`_STATE_ROOT_SDDL_TEMPLATE`'s
    docstring for why the third grantee is required).

    Windows-only; a no-op on any other platform so this module keeps
    importing (and its pure logic keeps being testable) on Linux. ``pywin32``
    is imported LAZILY here for the same reason.

    Called on EVERY :func:`write_journal` (idempotent/self-healing, not just
    first creation) and deliberately FAIL-LOUD, mirroring
    ``civiccast.native.provision.journal._harden_state_root_acl``: a
    hardening failure here means the journal -- which still carries the
    plan/context/history and, on the halt path, the recovery document -- is
    about to be written into a directory this call could not actually
    secure. Silently continuing would leave that state readable by any other local user on a shared
    station without telling anyone.
    """

    if os.name != "nt":
        return

    import win32security

    sddl = _STATE_ROOT_SDDL_TEMPLATE.format(caller_sid=_current_process_sid_sddl())
    security_descriptor = win32security.ConvertStringSecurityDescriptorToSecurityDescriptor(
        sddl, win32security.SDDL_REVISION_1
    )
    try:
        win32security.SetFileSecurity(
            str(state_root),
            win32security.DACL_SECURITY_INFORMATION
            | win32security.PROTECTED_DACL_SECURITY_INFORMATION,
            security_descriptor,
        )
    except Exception as exc:  # pragma: no cover - real Win32 call; see docstring
        raise JournalError(
            f"could not harden ACLs on upgrade state root {state_root}: {exc}"
        ) from exc


def redact_database_url(url: str) -> str:
    """Return ``url`` with any password replaced by ``***``.

    ``postgresql://user:s3cret@host:5432/db`` -> ``postgresql://user:***@host:5432/db``.
    A ``password=`` query parameter is masked too. A URL with no password (or
    one that is not a URL) is returned unchanged.
    """

    try:
        parts = urlsplit(url)
        netloc = parts.netloc
        query = parts.query
        if "@" in netloc:
            userinfo, _, hostport = netloc.rpartition("@")
            if ":" in userinfo:
                user = userinfo.split(":", 1)[0]
                netloc = f"{user}:{REDACTED_PASSWORD}@{hostport}"
        if query:
            pairs = []
            for item in query.split("&"):
                key, sep, _value = item.partition("=")
                if sep and key.lower() in {"password", "passwd", "pwd"}:
                    item = f"{key}={REDACTED_PASSWORD}"
                pairs.append(item)
            query = "&".join(pairs)
        return urlunsplit((parts.scheme, netloc, parts.path, query, parts.fragment))
    except ValueError:
        return url


def _database_password(url: str) -> str | None:
    try:
        password = urlsplit(url).password
    except ValueError:
        return None
    return unquote(password) if password else None


def _scrub_text(text: str, url: str, password: str | None) -> str:
    """Remove the literal URL / password from a free-text journal field."""

    if url:
        text = text.replace(url, redact_database_url(url))
    if password:
        for variant in {password, quote(password, safe="")}:
            text = text.replace(variant, REDACTED_PASSWORD)
    return text


def _scrub(value: object, url: str, password: str | None) -> object:
    if isinstance(value, str):
        return _scrub_text(value, url, password)
    if isinstance(value, list):
        return [_scrub(item, url, password) for item in value]
    if isinstance(value, dict):
        return {key: _scrub(item, url, password) for key, item in value.items()}
    return value


def redacted_payload(journal: UpgradeJournal) -> dict[str, object]:
    """The JSON-able journal dict with the database password removed."""

    url = journal.context.database_url
    password = _database_password(url)
    data = journal.model_dump(mode="json")
    scrubbed = _scrub(data, url, password)
    assert isinstance(scrubbed, dict)
    context = scrubbed["context"]
    assert isinstance(context, dict)
    context["database_url"] = redact_database_url(url)
    return scrubbed


def with_live_database_url(journal: UpgradeJournal, live_database_url: str) -> UpgradeJournal:
    """Re-attach the caller's real database URL to a journal loaded from disk.

    The persisted URL has its password redacted, so anything that runs on a
    loaded journal (resume, the operator recovery document) must take the
    URL from the live config the caller passed, never from the file.
    """

    if journal.context.database_url == live_database_url:
        return journal
    return journal.model_copy(
        update={"context": journal.context.model_copy(update={"database_url": live_database_url})}
    )


class JournalError(RuntimeError):
    """Raised when a journal on disk cannot be parsed or an illegal phase
    transition is attempted. Both are fail-loud: the engine must not proceed
    on a journal it cannot trust."""


def journal_path(state_root: str | Path) -> Path:
    return Path(state_root) / JOURNAL_FILENAME


def load_journal(state_root: str | Path) -> UpgradeJournal | None:
    """Load the journal for ``state_root``; return None if none exists.

    Raises :class:`JournalError` for a present-but-unparseable journal -- an
    upgrade must never silently start fresh over a journal it failed to read,
    because that journal may describe an in-flight mutation whose recovery
    point (the backup) still matters.
    """

    path = journal_path(state_root)
    if not path.exists():
        return None
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:  # pragma: no cover - unreadable file is env-specific
        raise JournalError(f"cannot read journal at {path}: {exc}") from exc
    try:
        return UpgradeJournal.model_validate_json(raw)
    except ValidationError as exc:
        raise JournalError(f"journal at {path} is corrupt/unparseable: {exc}") from exc


def write_journal(journal: UpgradeJournal) -> Path:
    """Atomically persist ``journal`` under its own context's state root.

    Uses temp-file + fsync + ``os.replace`` so a kill mid-write never yields a
    torn journal (see module docstring). Also -- see the module docstring's
    security note -- hardens the state root's own ACL on every call (cheap
    and self-healing).
    """

    state_root = Path(journal.context.state_root)
    state_root.mkdir(parents=True, exist_ok=True)
    _harden_state_root_acl(state_root)
    path = journal_path(state_root)
    payload = json.dumps(redacted_payload(journal), indent=2, sort_keys=True)

    # Unique temp name so two racing writers cannot corrupt each other's temp.
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        fh.write(payload)
        fh.flush()
        os.fsync(fh.fileno())
    tmp.replace(path)
    return path


def advance(
    journal: UpgradeJournal,
    phase: UpgradePhase,
    detail: str,
    **updates: object,
) -> UpgradeJournal:
    """Return a new journal at ``phase`` with an appended history entry.

    Enforces the state machine: a forward move may only go to the immediately
    higher forward rank (no skipping a boundary), and a terminal state is
    reachable from any non-terminal phase (rollback/halt/refuse can be entered
    whenever a step fails). Attempting to advance FROM a terminal phase, or to
    skip a forward boundary, raises :class:`JournalError` -- the transition
    grammar is part of the safety proof, not a convention.

    This function does NOT write; the caller persists via :func:`write_journal`
    after the corresponding real action succeeds, so the on-disk phase never
    runs ahead of the world.
    """

    current = journal.phase
    if current.is_terminal:
        raise JournalError(f"cannot advance from terminal phase {current.value!r}")

    # A forward move (non-terminal target) may advance exactly one boundary;
    # terminal targets (rollback/halt/refuse) are reachable from any phase.
    if not phase.is_terminal and phase.rank != current.rank + 1:
        raise JournalError(
            f"illegal forward transition {current.value!r} -> {phase.value!r} "
            "(forward phases advance exactly one boundary)"
        )

    stamp = datetime.now(UTC).isoformat()
    history = [*journal.history, (phase.value, stamp, detail)]
    return journal.model_copy(update={"phase": phase, "history": history, **updates})
