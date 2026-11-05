"""F10 (RC4 field run 2026-11-04, Beelink): a separately installed Ollama held 127.0.0.1:11434.

The station's own Ollama child looped on "bind: forbidden", yet readiness (GET /api/version on
11434) succeeded -- against the FOREIGN Ollama -- so the supervisor reported local AI ready and
the station used an Ollama it does not own. Readiness must also prove the listener is ours.
"""

from __future__ import annotations

import logging

import pytest

from civiccast.native.supervisor import service


@pytest.fixture
def version_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(service, "_ollama_version_answers", lambda: True)


def test_ollama_is_ready_when_our_own_child_holds_the_port(
    version_ok: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(service, "_ollama_port_listener_pid", lambda: 4242)
    monkeypatch.setattr(service, "_is_supervisor_descendant", lambda pid: pid == 4242)
    assert service._default_ollama_version_probe() is True


def test_a_foreign_ollama_on_the_port_is_not_ready_and_is_named_in_the_log(
    version_ok: None, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(service, "_ollama_port_listener_pid", lambda: 20000)
    monkeypatch.setattr(service, "_is_supervisor_descendant", lambda pid: False)
    monkeypatch.setattr(
        service,
        "_process_label",
        lambda pid: r"C:\Users\x\AppData\Local\Programs\Ollama\ollama.exe",
    )
    service._FOREIGN_OLLAMA_WARNED.clear()
    with caplog.at_level(logging.WARNING, logger=service.LOGGER_NAME):
        assert service._default_ollama_version_probe() is False
        assert service._default_ollama_version_probe() is False
    warnings = [r.getMessage() for r in caplog.records if "11434" in r.getMessage()]
    assert len(warnings) == 1, "one clear line, not one per readiness poll"
    assert "Programs\\Ollama\\ollama.exe" in warnings[0] and "20000" in warnings[0]


def test_no_listener_is_not_ready(version_ok: None, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(service, "_ollama_port_listener_pid", lambda: None)
    assert service._default_ollama_version_probe() is False


def test_version_failure_is_not_ready_without_looking_at_ownership(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(service, "_ollama_version_answers", lambda: False)

    def must_not_be_called() -> int:
        raise AssertionError("ownership is only checked once something answers")

    monkeypatch.setattr(service, "_ollama_port_listener_pid", must_not_be_called)
    assert service._default_ollama_version_probe() is False


def test_ownership_lookup_errors_fail_closed(
    version_ok: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom() -> int:
        raise OSError("connection table unavailable")

    monkeypatch.setattr(service, "_ollama_port_listener_pid", boom)
    assert service._default_ollama_version_probe() is False
