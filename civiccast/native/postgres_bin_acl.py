# SPDX-License-Identifier: Apache-2.0
# Copyright (c) The CivicCast Authors
"""Grant the INSTALLING account read+execute on the PostgreSQL binary tree
before any install-time ``pg_ctl start`` (rc.6 -> rc.7 upgrade blocker on the
old Dell, 2026-11-19).

THE DEFECT THIS EXISTS TO PREVENT
=================================
On the old Dell, ``C:\\Program Files\\CivicCast (Native)`` carried only
``NT AUTHORITY\\SYSTEM``, ``BUILTIN\\Administrators`` and
``NT SERVICE\\CivicCastSupervisor`` -- no entry for ``BigDelli7``, the account
the rc.7 install ran as (the 11-16 rc.6 install had been run as SYSTEM, which
is why the tree was born SYSTEM-owned). The rc.7 upgrade died at
:func:`civiccast.native.upgrade.pg_lifecycle.real_start_postgres` with::

    pg_ctl start failed ... waiting for server to start....Access is denied.

THE MECHANISM
=============
:mod:`civiccast.native.pgdata_acl` documents it from primary source
(PostgreSQL 17 ``src/common/restricted_token.c``): ``pg_ctl`` launches the
postmaster under a restricted token in which ``BUILTIN\\Administrators`` is
DENY-ONLY, so only the caller's own user SID (and its non-dropped groups)
counts. That module normalizes ``pgdata`` -- but nothing ever made the
POSTGRES BINARY FOLDER reachable by the installing account. On the Dell the
postmaster's restricted token could not traverse ``CivicCast (Native)``
(no ``BUILTIN\\Users`` ACE either), so Windows refused to load
``postgres.exe`` and ``cmd.exe`` printed the bare ``Access is denied.``
The same token reaches the binaries' DLLs the same way, so the folder needs
read+execute, not just the ``.exe``.

THE FIX
=======
:func:`normalize_postgres_bin_acl` ADDS one explicit allow ACE for the
calling process's own user SID -- ``RX`` (``0x1200A9``:
``FILE_GENERIC_READ | FILE_GENERIC_EXECUTE``) -- to:

* the PostgreSQL PREFIX folder -- the PARENT of ``bin`` (the ``payload``
  folder, which also holds ``lib`` and ``share``), inheritable (``OI``/
  ``CI``). It is not enough to grant ``bin`` alone: the restricted-token
  postmaster also loads DLLs from ``lib`` and reads ``share`` (timezone sets
  and friends) at startup, so the whole sibling set those folders hang off
  must carry the inheritable ACE. Windows' propagation engine then covers
  ``bin``, ``lib``, ``share`` and every existing and future file in them.
  When ``bin``'s parent is not inside a ``packs`` tree, the inheritable ACE
  goes on ``bin`` itself;
* each ancestor above the prefix up to (and including) the install root,
  NON-inheritable: a directory only needs ``FILE_LIST_DIRECTORY``/
  ``FILE_TRAVERSE`` to be walked through, and granting nothing above the
  install root keeps every change inside the product's own tree.

Unlike :func:`civiccast.native.pgdata_acl.normalize_pgdata_acl` this is
ADDITIVE, never a replace: the merge (:func:`merge_dacl_ace`) keeps every
existing ACE and the target's own protected/inherited state untouched, so
SYSTEM's and the service's access and ``C:\\Program Files``'s inheritance are
unchanged. It does NOT widen access to ``BUILTIN\\Users``, ``Everyone`` or
anyone else -- the one principal named is the installing account, which is a
local administrator by construction and already held full control of these
files through ``BUILTIN\\Administrators``; the ACE only makes that access
reachable from a token in which Administrators is deny-only. Not a
privilege-escalation path.

It is idempotent (an ACE already present is not re-added) and fail-loud:
a folder that cannot be read or written raises
:class:`civiccast.native.pgdata_acl.PgDataAclError` naming this step, so the
install stops with a clear message instead of the opaque pg_ctl failure this
module exists to prevent.

Windows-only; a no-op returning ``None`` on any other platform, so the pure
logic stays testable on Linux (``pywin32`` is imported lazily for the same
reason, matching :mod:`civiccast.native.pgdata_acl`).
"""

from __future__ import annotations

import os
import re
from collections.abc import Callable
from pathlib import Path

from civiccast.native.pgdata_acl import _SID_RE, PgDataAclError, _current_process_sid

#: Stable token prefixed onto every fault this module raises (see
#: :data:`civiccast.native.pgdata_acl.FAILURE_STEP`).
FAILURE_STEP = "postgres-bin-acl"

#: ``FILE_GENERIC_READ | FILE_GENERIC_EXECUTE``: read, list a directory,
#: traverse it, execute a file. Never write. Same value
#: ``install_service_identity._grant_tree_native_sid`` calls ``RX``.
RX_MASK_SDDL = "0x1200a9"

SidReader = Callable[[], str]
DaclReader = Callable[[str], str]
DaclApplier = Callable[[str, str], None]

#: One allow ACE in an SDDL DACL: ``(A;<flags>;<mask>;<object>;<inherit>;<sid>)``.
_ACE_RE = re.compile(r"\(A;([^;]*);([^;]*);[^;]*;[^;]*;([^)]*)\)")

#: The head of ANY ACE, ``(<type>;<flags>;`` -- enough to tell an explicit
#: deny (``D``/``OD`` without the inherited ``ID`` flag) from everything else.
_ACE_HEAD_RE = re.compile(r"\(([A-Z]{1,2});([^;()]*);")


def _mask_covers_rx(mask: str) -> bool:
    """Does an SDDL access mask grant read+execute?"""

    text = mask.strip().upper()
    if text in ("GA", "FA"):
        return True
    if text.startswith("0X"):
        try:
            value = int(text, 16)
        except ValueError:
            return False
        return value & 0x1200A9 == 0x1200A9
    return False


def has_rx_ace(dacl_sddl: str, caller_sid: str, *, inheritable: bool) -> bool:
    """Does ``dacl_sddl`` already grant ``caller_sid`` RX?

    Pure, and tolerant of how Windows re-renders a mask on read-back
    (``0x1200a9`` vs an alias like ``GA``). ``inheritable`` additionally
    requires ``OI`` AND ``CI``, so a stale non-inheritable ACE on the binary
    folder is upgraded rather than mistaken for the one this module wants.
    """

    wanted = caller_sid.strip().upper()
    for flags, mask, sid in _ACE_RE.findall(dacl_sddl):
        if sid.strip().upper() != wanted or not _mask_covers_rx(mask):
            continue
        upper_flags = flags.upper()
        if inheritable and not ("OI" in upper_flags and "CI" in upper_flags):
            continue
        return True
    return False


def bin_rx_ace(caller_sid: str, *, inheritable: bool) -> str:
    """Render the one allow ACE this module adds, for ``caller_sid``.

    Pure (no Windows, no filesystem). ``OICI`` only on the folder whose
    CHILDREN (``postgres.exe`` and its DLLs) the postmaster must execute;
    traverse-through ancestors get a non-inheritable ACE. Raises
    :class:`PgDataAclError` for anything that is not a textual SID, so a
    malformed or hostile value can never smuggle extra ACEs into a descriptor.
    """

    if not _SID_RE.match(caller_sid or ""):
        raise PgDataAclError(
            f"{FAILURE_STEP}: refusing to build an ACE from {caller_sid!r}, "
            "which is not a textual SID (expected 'S-1-...')"
        )
    flags = "OICI" if inheritable else ""
    return f"(A;{flags};{RX_MASK_SDDL};;;{caller_sid})"


def merge_dacl_ace(current_dacl_sddl: str, ace: str) -> str:
    """Return ``current_dacl_sddl`` with ``ace`` inserted in canonical order.

    Pure. The DACL header (``D:`` plus any ``P``/``AI``/``AR`` flags) is kept
    verbatim, so an object's protected/inherited state and every existing ACE
    survive; only the new entry is added.

    Windows evaluates ACEs top to bottom and stops at the first match, so
    order is the security decision, not a cosmetic one. ``ace`` is a new
    ALLOW, so it must land AFTER every leading explicit (non-inherited) deny
    ACE ``(D;...)``: inserting it first would let this allow beat an explicit
    deny and hand out access the descriptor forbids. It is placed immediately
    after the last leading explicit deny -- i.e. canonical order, explicit
    deny, explicit allow, inherited -- and therefore leaves inherited ACEs
    (``ID`` flag) after it, where Windows expects them.
    """

    if not current_dacl_sddl.startswith("D:"):
        raise PgDataAclError(
            f"{FAILURE_STEP}: cannot merge into {current_dacl_sddl!r}, "
            "which is not an SDDL DACL string (expected it to start with 'D:')"
        )
    first_ace = current_dacl_sddl.find("(")
    if first_ace < 0:
        return current_dacl_sddl + ace

    # Each ACE's start offset, in order. SDDL ACE fields (type, flags, mask,
    # object, inherit, SID) never contain a "(", so the parenthesised list is
    # unambiguous.
    starts = [m.start() for m in re.finditer(r"\(", current_dacl_sddl)]
    insert_at = first_ace
    for index, start in enumerate(starts):
        head = _ACE_HEAD_RE.match(current_dacl_sddl, start)
        if head is None:
            break
        ace_type = head.group(1).upper()
        flags = head.group(2).upper()
        explicit_deny = ace_type in ("D", "OD") and "ID" not in flags
        if not explicit_deny:
            break
        insert_at = starts[index + 1] if index + 1 < len(starts) else len(current_dacl_sddl)
    return current_dacl_sddl[:insert_at] + ace + current_dacl_sddl[insert_at:]


def postgres_prefix_dir(bin_dir: str | Path) -> Path:
    """The folder whose CHILDREN need inherited RX: ``bin_dir``'s parent when
    ``bin_dir`` sits inside a ``packs`` tree (the ``payload`` folder, which
    also holds ``lib`` and ``share``), else ``bin_dir`` itself.

    Pure. ``payload`` is the only folder whose inheritance reaches both
    ``bin`` and its siblings in one ACE, which is why the inheritable grant
    moves up from ``bin`` to it.
    """

    path = Path(bin_dir)
    for parent in path.parents:
        if parent.name.lower() == "packs":
            if parent == path.parent:
                # bin sits directly under packs: there is no intermediate
                # prefix folder, so bin is its own prefix.
                return path
            return path.parent
    return path


def _packs_install_root(bin_dir: str | Path) -> Path | None:
    """The install root -- the PARENT of the ``packs`` directory -- or
    ``None`` when ``bin_dir`` is not inside a ``packs`` tree."""

    for parent in Path(bin_dir).parents:
        if parent.name.lower() == "packs":
            return parent.parent
    return None


def server_bin_traversal_chain(bin_dir: str | Path) -> list[Path]:
    """Every folder :func:`normalize_postgres_bin_acl` grants, deepest first:
    the PostgreSQL prefix (see :func:`postgres_prefix_dir`) then each
    ancestor up to (and including) the install root; ``[bin_dir]`` alone when
    the folder is not inside a ``packs`` tree.

    The FIRST element is the one that gets the inheritable (``OI``/``CI``)
    ACE; the rest get a non-inheritable traverse ACE. In a ``packs`` tree
    that first element is ``bin``'s parent (``payload``), and ``bin``,
    ``lib`` and ``share`` are covered by inheritance from it.

    The install root is recognised the way the product itself lays the tree
    out (``<install_root>\\packs\\native-server-binaries\\payload\\bin`` --
    :func:`civiccast.native.provision.__main__.resolve_provision_paths` and
    :func:`civiccast.native.upgrade.pg_lifecycle.derive_pg_lifecycle_paths`):
    it is the PARENT of the ``packs`` directory. Nothing above it is ever
    touched. Pure, so the exact set of folders is unit-testable anywhere.
    """

    bin_path = Path(bin_dir)
    install_root = _packs_install_root(bin_path)
    if install_root is None:
        return [bin_path]

    prefix = postgres_prefix_dir(bin_path)
    chain = [prefix]
    for parent in prefix.parents:
        chain.append(parent)
        if parent == install_root:
            return chain
    return [bin_path]


def _read_dacl_sddl(path: str) -> str:
    """``path``'s DACL as an SDDL ``D:`` string (``pywin32``)."""

    import win32security

    descriptor = win32security.GetNamedSecurityInfo(
        path, win32security.SE_FILE_OBJECT, win32security.DACL_SECURITY_INFORMATION
    )
    if descriptor.GetSecurityDescriptorDacl() is None:
        raise PgDataAclError(
            f"{FAILURE_STEP}: {path!r} has a NULL DACL (everyone: full control); "
            "refusing to edit it, because writing a merge would silently replace "
            "that descriptor with a narrow one"
        )
    return str(
        win32security.ConvertSecurityDescriptorToStringSecurityDescriptor(
            descriptor,
            win32security.SDDL_REVISION_1,
            win32security.DACL_SECURITY_INFORMATION,
        )
    )


def _apply_dacl(path: str, sddl: str) -> None:
    """Write ``sddl``'s DACL to ``path``, preserving its protected/inherited
    state (the ``D:P`` flag the merge carried through)."""

    import win32security

    descriptor = win32security.ConvertStringSecurityDescriptorToSecurityDescriptor(
        sddl, win32security.SDDL_REVISION_1
    )
    control, _revision = descriptor.GetSecurityDescriptorControl()
    protection = (
        win32security.PROTECTED_DACL_SECURITY_INFORMATION
        if control & win32security.SE_DACL_PROTECTED
        else win32security.UNPROTECTED_DACL_SECURITY_INFORMATION
    )
    win32security.SetNamedSecurityInfo(
        path,
        win32security.SE_FILE_OBJECT,
        win32security.DACL_SECURITY_INFORMATION | protection,
        None,
        None,
        descriptor.GetSecurityDescriptorDacl(),
        None,
    )


def normalize_postgres_bin_acl(
    pg_ctl_path: str | Path,
    *,
    sid_reader: SidReader | None = None,
    read_dacl: DaclReader | None = None,
    apply_dacl: DaclApplier | None = None,
) -> list[tuple[str, str]] | None:
    """Grant the calling account ``RX`` on the PostgreSQL prefix folder --
    the parent of ``pg_ctl_path``'s ``bin`` folder, so ``bin``, ``lib`` and
    ``share`` all inherit it -- and on each ancestor up to the install root
    (module docstring).

    Returns the ``(path, merged SDDL)`` pairs actually written -- an empty
    list when every ACE was already present, ``None`` on a non-Windows host.
    Idempotent: an ACE already in a folder's DACL is not re-added, so N calls
    leave the same descriptors as one.

    ``sid_reader``/``read_dacl``/``apply_dacl`` are the injectable seams
    (production defaults: :func:`civiccast.native.pgdata_acl.
    _current_process_sid` / :func:`_read_dacl_sddl` / :func:`_apply_dacl`),
    so the decision logic is fully unit-testable without Windows.

    Every failure -- missing binary folder, unreadable token, refused DACL
    read or write -- raises :class:`PgDataAclError`; the caller does not run
    ``pg_ctl`` afterwards.
    """

    if os.name != "nt":
        return None

    pg_ctl = Path(pg_ctl_path)
    if not pg_ctl.is_absolute() and pg_ctl.parent.name == "":
        # A bare binary name resolved through PATH names no product tree;
        # there is nothing here to grant and this must never touch the
        # process's current directory.
        return []
    bin_dir = pg_ctl.parent
    if not bin_dir.is_dir():
        raise PgDataAclError(
            f"{FAILURE_STEP}: the PostgreSQL binary folder {str(bin_dir)!r} "
            "(parent of pg_ctl) does not exist (or is not a directory), so its "
            "ACL cannot be normalized"
        )

    read_sid = sid_reader if sid_reader is not None else _current_process_sid
    read = read_dacl if read_dacl is not None else _read_dacl_sddl
    apply = apply_dacl if apply_dacl is not None else _apply_dacl

    try:
        caller_sid = read_sid()
    except PgDataAclError:
        raise
    except Exception as exc:
        raise PgDataAclError(
            f"{FAILURE_STEP}: could not read the calling process's own user SID, so "
            f"the install-time postgres identity for {str(bin_dir)!r} is unknown: {exc}"
        ) from exc

    applied: list[tuple[str, str]] = []
    chain = server_bin_traversal_chain(bin_dir)
    for index, target in enumerate(chain):
        inheritable = index == 0
        ace = bin_rx_ace(caller_sid, inheritable=inheritable)
        try:
            current = read(str(target))
        except PgDataAclError:
            raise
        except Exception as exc:
            raise PgDataAclError(
                f"{FAILURE_STEP}: could not read the DACL of {str(target)!r}: {exc}"
            ) from exc
        if has_rx_ace(current, caller_sid, inheritable=inheritable):
            continue
        merged = merge_dacl_ace(current, ace)
        try:
            apply(str(target), merged)
        except Exception as exc:
            raise PgDataAclError(
                f"{FAILURE_STEP}: could not grant the install-time postgres account "
                f"read+execute on {str(target)!r}: {exc}"
            ) from exc
        applied.append((str(target), merged))
    return applied


__all__ = [
    "FAILURE_STEP",
    "RX_MASK_SDDL",
    "bin_rx_ace",
    "has_rx_ace",
    "merge_dacl_ace",
    "normalize_postgres_bin_acl",
    "postgres_prefix_dir",
    "server_bin_traversal_chain",
]
