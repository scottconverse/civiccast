# SPDX-License-Identifier: Apache-2.0
# Copyright (c) The CivicCast Authors
"""Shared test bootstrap for deterministic staff-route authentication."""

from __future__ import annotations

import asyncio
import gc
import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import event
from sqlalchemy.pool import Pool

from tests.support.hermetic_state import (
    INSTALLED_PATHS_MARKER,
    changed_entries,
    hermetic_environment,
    real_state_roots,
    snapshot,
)

#: The operator's real CivicCast state roots, resolved from the environment as
#: it was BEFORE any fixture redirected it. Module-level on purpose: the
#: hermetic fixture below rewrites LOCALAPPDATA per test, so resolving lazily
#: would guard the temp copy instead of the real one.
_REAL_STATE_ROOTS: tuple[Path, ...] = real_state_roots(os.environ)


#: Set when the running test opened a PostgreSQL connection through SQLAlchemy.
_POSTGRES_CONNECTION_OPENED = {"in_this_test": False}


@event.listens_for(Pool, "connect")
def _note_postgres_connection(dbapi_connection: object, _record: object) -> None:
    if type(dbapi_connection).__module__.startswith("psycopg"):
        _POSTGRES_CONNECTION_OPENED["in_this_test"] = True


@pytest.fixture(autouse=True)
def _fail_the_test_that_leaks_a_postgres_connection() -> Iterator[None]:
    """Make a leaked PostgreSQL connection fail the test that leaked it (#311).

    psycopg reports a connection that is garbage-collected while still open as
    a ``ResourceWarning`` from ``__del__``, and ``filterwarnings = ["error"]``
    escalates it. Without this fixture the collector finalizes the connection
    at some later, arbitrary moment, so the failure lands on an unrelated test
    and moves with the test order -- the randomized lane failed on a different
    innocent test each time.

    Forcing one collection here, after this test's own fixtures have finished,
    reports the leak in the teardown of the test that caused it, in every
    order. A connection that a wider-scoped fixture still holds is reachable,
    so it is not collected and not reported. The collection runs only for
    tests that opened a PostgreSQL connection, so the rest of the suite pays
    nothing.
    """

    _POSTGRES_CONNECTION_OPENED["in_this_test"] = False
    yield
    if _POSTGRES_CONNECTION_OPENED["in_this_test"]:
        gc.collect()


def pytest_configure() -> None:
    """Enable the documented deterministic staff token for local tests."""

    os.environ.setdefault("CIVICCAST_ALLOW_DETERMINISTIC_STAFF_TOKEN", "1")
    os.environ.setdefault("CIVICCAST_ALLOW_DETERMINISTIC_SUBSCRIBE_SECRETS", "1")
    os.environ.setdefault("CIVICCAST_ALLOW_EPHEMERAL_STORES", "1")


@pytest.fixture(autouse=True)
def _hermetic_civiccast_state(request: pytest.FixtureRequest, tmp_path: Path) -> Iterator[None]:
    """Point every CivicCast state default beneath this test's ``tmp_path``.

    ``create_app()`` and the installer/egress/caption modules resolve their
    default state, lock, upload, managed-storage, egress, TSDuck, certificate
    and secrets locations from ``%LOCALAPPDATA%``, the XDG roots, or
    ``~/.civiccast``. Before this fixture existed, any test that built the
    production app without overriding each of those wrote a real
    ``civiccast.sqlite3``, lock files and secrets into the developer's own
    profile (and broke outright on a runner where that profile is read-only).

    Tests that deliberately verify the installed-path contract opt out with
    ``@pytest.mark.installed_paths``; they still get the write guard below.
    A test may still override any single variable itself -- its own
    ``monkeypatch.setenv`` runs after this fixture and wins.

    The teardown half is the guard: if the test (or the product code it
    exercised) created, removed or rewrote anything under the REAL state
    roots, the test fails at teardown naming the paths. That is what keeps an
    unknown resolver, or a hard-coded path, from silently editing the
    operator's station.
    """

    # A private MonkeyPatch, not the shared ``monkeypatch`` fixture: requesting
    # that fixture would make it tear down AFTER this one, so a test's own
    # ``monkeypatch.setattr(os, "name", "posix")`` would still be in force
    # while the guard below walks the real Windows roots.
    patch = pytest.MonkeyPatch()
    if request.node.get_closest_marker(INSTALLED_PATHS_MARKER) is None:
        for name, value in hermetic_environment(tmp_path).items():
            patch.setenv(name, value)
    before = snapshot(_REAL_STATE_ROOTS)
    try:
        yield
    finally:
        patch.undo()
    changed = changed_entries(before, snapshot(_REAL_STATE_ROOTS))
    if changed:
        listed = "\n  ".join(changed)
        pytest.fail(
            "test touched the operator's real CivicCast state instead of tmp_path "
            f"(add the '{INSTALLED_PATHS_MARKER}' marker only if that is the contract "
            f"under test, and never write there):\n  {listed}",
            pytrace=False,
        )


@pytest.fixture(autouse=True)
def _clear_external_database_probe_cache() -> None:
    """Keep the durable-storage probe memo from leaking across tests.

    ``civiccast.installer.storage`` memoizes its external-``DATABASE_URL``
    connectivity + schema-currency probe for a few seconds so a GUI polling
    ``/installer/summary`` cannot pile up abandoned connect threads. That memo
    is process-global and keyed only on the URL, so without this a test that
    injects one database outcome could be answered by a neighbouring test's
    cached result for the same URL.
    """

    from civiccast.installer.storage import reset_external_database_probe_cache

    reset_external_database_probe_cache()


@pytest.fixture(autouse=True)
def _restore_os_environ_after_every_test() -> Iterator[None]:
    """Restore the whole process environment after every test.

    Verified cross-suite pollution: ``civiccast.native.supervisor.
    service_env.ensure_database_url_env`` writes ``os.environ["DATABASE_URL"]``
    directly -- by design, so the production service can bridge the
    installer-persisted registry value into its process env (see
    ``service_env.py``'s module docstring). Tests that exercise it first call
    ``monkeypatch.delenv(DATABASE_URL_ENV_VAR, raising=False)`` to start from
    a clean slate, but pytest's own ``MonkeyPatch.delitem``/``delenv`` is a
    documented no-op -- it records NO undo entry -- when the key is already
    absent. The first time a whole pytest process runs one of those tests,
    ``DATABASE_URL`` is unset, so the guarding ``delenv`` registers nothing to
    undo, and the raw write that follows (bypassing monkeypatch entirely) is
    never reverted: the value survives every later test in the same process.

    ``tests/native`` and ``tests/installer`` running together is exactly the
    case that exposes this. ``tests/native/test_service_env.py`` leaves
    ``DATABASE_URL=postgresql://from-registry/db`` (its fixture value) set for
    the rest of the session; every later installer test that calls
    ``civiccast.app.create_app()`` then picks up that ambient URL and tries to
    actually connect, breaking dozens of unrelated installer tests (auth,
    durable storage, ...). Each suite run alone never notices, because
    ``tests/installer`` alone never runs after the leaking native test.

    Rather than special-case ``DATABASE_URL``, this is a general backstop: it
    snapshots ``os.environ`` before each test and restores it byte-for-byte
    after, so ANY direct environment write left unrestored by test code (or
    by production code under test) cannot outlive the test that caused it.
    ``PYTEST_CURRENT_TEST`` is left alone -- pytest's own runner maintains it
    across the very setup/call/teardown phases this fixture wraps, and
    touching it here would race pytest's own bookkeeping for no benefit.
    """

    before = dict(os.environ)
    try:
        yield
    finally:
        after = dict(os.environ)
        if after != before:
            for key in after.keys() - before.keys():
                if key != "PYTEST_CURRENT_TEST":
                    del os.environ[key]
            for key, value in before.items():
                if os.environ.get(key) != value:
                    os.environ[key] = value


def _installed_event_loop() -> asyncio.AbstractEventLoop | None:
    """Return the loop installed for this thread WITHOUT constructing one.

    ``asyncio.get_event_loop()`` creates a loop when none is set, which is the
    opposite of what this needs.
    """

    policy = asyncio.get_event_loop_policy()
    local = getattr(policy, "_local", None)
    loop = getattr(local, "_loop", None)
    return loop if isinstance(loop, asyncio.AbstractEventLoop) else None


@pytest.hookimpl(trylast=True)
def pytest_runtest_teardown() -> None:
    """Close the event loop pytest-asyncio abandons, so it cannot leak.

    pytest-asyncio 1.3.0 can leave a function-scoped loop installed in the
    thread's policy slot without closing it. Nothing then references that loop
    except the slot itself, so the next ``asyncio.run()`` -- which ends by
    calling ``set_event_loop(None)`` -- drops the last reference. The garbage
    collector finalizes the loop while it is still open, and Python reports
    "unclosed event loop" plus BOTH halves of the loop's socketpair self-pipe
    (on Windows a ProactorEventLoop self-pipe is a real AF_INET loopback pair)
    as *unraisable* ResourceWarnings. ``filterwarnings = ["error", ...]``
    escalates those onto whichever unrelated test the collection interrupted,
    which is why the randomized-order lane failed on a different innocent test
    every run.

    Every async test here is function-scoped (see ``asyncio_default_*_loop_scope``
    in pyproject.toml and the absence of any ``loop_scope`` marker), so a loop
    still installed at teardown is finished with. A running loop is never
    touched: several background workers (``civiccast.platform.worker_runtime``
    and its consumers) park one on a daemon thread deliberately.
    """

    loop = _installed_event_loop()
    if loop is None or loop.is_running() or loop.is_closed():
        return
    loop.close()
    asyncio.set_event_loop(None)
