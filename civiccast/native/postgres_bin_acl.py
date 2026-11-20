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

* the ``bin`` folder that holds ``pg_ctl.exe``/``postgres.exe``, inheritable
  (``OI``/``CI``) so Windows' propagation engine covers every existing
  binary and DLL and every future one;
* each ancestor from that folder up to (and including) the install root,
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
    """Return ``current_dacl_sddl`` with ``ace`` inserted as its first ACE.

    Pure. The DACL header (``D:`` plus any ``P``/``AI``/``AR`` flags) is kept
    verbatim, so an object's protected/inherited state and every existing ACE
    survive; only the new entry is added. Deny ACEs normally sort before
    allow ACEs and are left where they are -- if an explicit deny for this
    account exists, it still wins and the install fails loud at the next
    ``pg_ctl``, exactly as the security descriptor says it should.
    """

    if not current_dacl_sddl.startswith("D:"):
        raise PgDataAclError(
            f"{FAILURE_STEP}: cannot merge into {current_dacl_sddl!r}, "
            "which is not an SDDL DACL string (expected it to start with 'D:')"
        )
    first_ace = current_dacl_sddl.find("(")
    if first_ace < 0:
        return current_dacl_sddl + ace
    return current_dacl_sddl[:first_ace] + ace + current_dacl_sddl[first_ace:]


def server_bin_traversal_chain(bin_dir: str | Path) -> list[Path]:
    """``bin_dir`` and each ancestor up to (and including) the install root,
    deepest first; ``[bin_dir]`` alone when the folder is not inside a
    ``packs`` tree.

    The install root is recognised the way the product itself lays the tree
    out (``<install_root>\\packs\\native-server-binaries\\payload\\bin`` --
    :func:`civiccast.native.provision.__main__.resolve_provision_paths` and
    :func:`civiccast.native.upgrade.pg_lifecycle.derive_pg_lifecycle_paths`):
    it is the PARENT of the ``packs`` directory. Nothing above it is ever
    touched. Pure, so the exact set of folders is unit-testable anywhere.
    """

    start = Path(bin_dir)
    chain = [start]
    for parent in start.parents:
        chain.append(parent)
        if parent.name.lower() == "packs":
            install_root = parent.parent
            if install_root != parent:
                chain.append(install_root)
            return chain
    return [start]


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
    """Grant the calling account ``RX`` on ``pg_ctl_path``'s folder and its
    ancestors up to the install root (module docstring).

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
    for target in server_bin_traversal_chain(bin_dir):
        inheritable = target == bin_dir
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
    "server_bin_traversal_chain",
]
