# Copyright (c) The CivicCast Authors
# SPDX-License-Identifier: Apache-2.0
"""rc.6.1 item 7: the service's CTRL_BREAK must reach a windowless child.

Field log on every service stop:

    stop: control_plane overran the 15s graceful deadline; TerminateProcess
    stop: ollama overran the 15s graceful deadline; TerminateProcess

``GenerateConsoleCtrlEvent`` only reaches processes on the CALLER'S console.
The service has none, and each child is spawned ``CREATE_NO_WINDOW`` (its own
hidden console), so the old ``os.kill(pid, CTRL_BREAK_EVENT)`` never arrived.
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

from civiccast.native.supervisor import service as service_module


class _RecordingConsole:
    def __init__(self, *, attach_ok: bool = True, send_ok: bool = True) -> None:
        self.calls: list[tuple[object, ...]] = []
        self.attach_ok = attach_ok
        self.send_ok = send_ok

    def FreeConsole(self) -> int:
        self.calls.append(("FreeConsole",))
        return 1

    def AttachConsole(self, pid: int) -> int:
        self.calls.append(("AttachConsole", pid))
        return 1 if self.attach_ok else 0

    def SetConsoleCtrlHandler(self, handler: object, add: int) -> int:
        self.calls.append(("SetConsoleCtrlHandler", handler, add))
        return 1

    def GenerateConsoleCtrlEvent(self, event: int, group: int) -> int:
        self.calls.append(("GenerateConsoleCtrlEvent", event, group))
        return 1 if self.send_ok else 0


def test_sender_joins_the_child_console_for_the_break() -> None:
    console = _RecordingConsole()

    service_module._default_ctrl_break_sender(808, api=console)

    assert console.calls == [
        ("FreeConsole",),
        ("AttachConsole", 808),
        ("SetConsoleCtrlHandler", None, 1),
        ("GenerateConsoleCtrlEvent", 1, 808),
        ("FreeConsole",),
        ("SetConsoleCtrlHandler", None, 0),
    ]


def test_sender_raises_when_the_child_console_refuses_the_attach() -> None:
    console = _RecordingConsole(attach_ok=False)

    with pytest.raises(OSError, match="AttachConsole"):
        service_module._default_ctrl_break_sender(808, api=console)

    assert ("GenerateConsoleCtrlEvent", 1, 808) not in console.calls


def test_sender_leaves_the_child_console_even_when_the_send_is_refused() -> None:
    console = _RecordingConsole(send_ok=False)

    with pytest.raises(OSError, match="GenerateConsoleCtrlEvent"):
        service_module._default_ctrl_break_sender(808, api=console)

    assert console.calls[-2:] == [("FreeConsole",), ("SetConsoleCtrlHandler", None, 0)]


def test_graceful_stop_names_an_undelivered_break(caplog: pytest.LogCaptureFixture) -> None:
    def refused(_pid: int) -> None:
        raise OSError("AttachConsole(808) was refused")

    runner = service_module.Win32ChildProcessRunner(
        popen_factory=lambda *_a, **_k: None, ctrl_break_sender=refused
    )
    with caplog.at_level(logging.INFO, logger=service_module.LOGGER_NAME):
        runner._safe_ctrl_break(808, expect_alive=True)
        runner._safe_ctrl_break(808)

    levels = [r.levelno for r in caplog.records if "not delivered" in r.getMessage()]
    assert levels == [logging.WARNING, logging.INFO]


# --------------------------------------------------------------------------
# Real Win32: a windowless child in its own group, signalled from a process
# with NO console -- the service's exact situation.
# --------------------------------------------------------------------------

_CHILD = textwrap.dedent(
    """
    import signal, sys, time
    from pathlib import Path
    marker = Path(sys.argv[1])
    def on_break(signum, frame):
        marker.write_text("break", encoding="utf-8")
        raise SystemExit(0)
    signal.signal(signal.SIGBREAK, on_break)
    marker.write_text("ready", encoding="utf-8")
    for _ in range(600):
        time.sleep(0.1)
    marker.write_text("timeout", encoding="utf-8")
    """
)

_SENDER_NEW = textwrap.dedent(
    """
    import sys
    from civiccast.native.supervisor.service import _default_ctrl_break_sender
    _default_ctrl_break_sender(int(sys.argv[1]))
    """
)

_SENDER_OLD = textwrap.dedent(
    """
    import os, signal, sys
    try:
        os.kill(int(sys.argv[1]), signal.CTRL_BREAK_EVENT)
    except OSError:
        pass
    """
)


def _break_reaches_child(tmp_path: Path, sender_source: str) -> bool:
    marker = tmp_path / "marker.txt"
    child = subprocess.Popen(
        [sys.executable, "-c", _CHILD, str(marker)],
        creationflags=subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP,
    )
    try:
        deadline = time.monotonic() + 30
        while not (marker.exists() and marker.read_text(encoding="utf-8") == "ready"):
            assert time.monotonic() < deadline, "child never became ready"
            time.sleep(0.05)
        subprocess.run(
            [sys.executable, "-c", sender_source, str(child.pid)],
            creationflags=subprocess.DETACHED_PROCESS,
            check=True,
            timeout=60,
        )
        try:
            child.wait(timeout=5)
        except subprocess.TimeoutExpired:
            return False
        return marker.read_text(encoding="utf-8") == "break"
    finally:
        if child.poll() is None:
            child.kill()
            child.wait(timeout=10)


@pytest.mark.windows_only
@pytest.mark.skipif(os.name != "nt", reason="real Win32 console control events")
def test_real_break_reaches_a_windowless_child_from_a_consoleless_sender(
    tmp_path: Path,
) -> None:
    assert _break_reaches_child(tmp_path, _SENDER_NEW)


@pytest.mark.windows_only
@pytest.mark.skipif(os.name != "nt", reason="real Win32 console control events")
def test_real_old_os_kill_never_reached_the_child(tmp_path: Path) -> None:
    """Pins the root cause: the pre-fix sender, run the way the service runs."""

    assert not _break_reaches_child(tmp_path, _SENDER_OLD)
