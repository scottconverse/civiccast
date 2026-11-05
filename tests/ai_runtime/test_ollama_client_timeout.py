# SPDX-License-Identifier: Apache-2.0
# Copyright (c) The CivicCast Authors
"""Timeout contract for the stdlib Ollama client (field evidence 2026-08-29).

Candidate #17 (32GB CPU-only reference station): POST /api/staff/summaries/generate
503'd at ~120.6s (cold) and ~120.2s (warm, model already loaded) even though Ollama's
own /api/completion succeeded and returned a real completion. The client's blanket
120s socket timeout was killing the HTTP read out from under a generation that Ollama
went on to finish -- a completed answer thrown away, not a hung request. These tests
pin the fix: a live generate call gets a much longer socket budget than a cheap
metadata call (/api/tags, /api/version), so a legitimate slow local CPU generation
(measured 94-366s+ on the same hardware class) has room to actually return.
"""

from __future__ import annotations

import io
import json
import threading
from typing import Any
from unittest.mock import patch

import pytest

from civiccast.ai_runtime.ollama_client import (
    DEFAULT_GENERATE_TIMEOUT_SECONDS,
    DEFAULT_TIMEOUT_SECONDS,
    OllamaRuntimeUnavailableError,
    generate_with_ollama,
    get_ollama_model_manifest,
)


class _FakeResponse(io.BytesIO):
    def __enter__(self) -> _FakeResponse:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


def _ok_response(payload: dict[str, Any]) -> _FakeResponse:
    return _FakeResponse(json.dumps(payload).encode("utf-8"))


class TestGenerateTimeoutBudget:
    def test_generate_uses_the_long_budget_by_default(self) -> None:
        captured: dict[str, Any] = {}

        def fake_urlopen(request: Any, timeout: float | None = None) -> _FakeResponse:
            captured["timeout"] = timeout
            return _ok_response({"response": "ok"})

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            text = generate_with_ollama(model="gemma4:e4b", prompt="hello")

        assert text == "ok"
        # The old fixed 120s socket timeout discarded a completion Ollama had
        # already finished computing on real CPU-only hardware (measured
        # 94-366s+). The default must be well above that measured range.
        assert captured["timeout"] == DEFAULT_GENERATE_TIMEOUT_SECONDS
        assert captured["timeout"] >= 300

    def test_generate_honors_an_explicit_override(self) -> None:
        captured: dict[str, Any] = {}

        def fake_urlopen(request: Any, timeout: float | None = None) -> _FakeResponse:
            captured["timeout"] = timeout
            return _ok_response({"response": "ok"})

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            generate_with_ollama(model="gemma4:e4b", prompt="hello", timeout=900)

        assert captured["timeout"] == 900

    def test_generate_requests_immediate_model_unload(self) -> None:
        captured: dict[str, Any] = {}

        def fake_urlopen(request: Any, timeout: float | None = None) -> _FakeResponse:
            captured["payload"] = json.loads(request.data.decode("utf-8"))
            return _ok_response({"response": "ok"})

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            assert generate_with_ollama(model="gemma4:e4b", prompt="hello") == "ok"

        assert captured["payload"].get("keep_alive") == 0

    def test_generate_requests_are_serialized(self) -> None:
        first_entered = threading.Event()
        second_entered = threading.Event()
        allow_first_to_finish = threading.Event()
        calls: list[int] = []
        errors: list[BaseException] = []

        def fake_urlopen(request: Any, timeout: float | None = None) -> _FakeResponse:
            call_number = len(calls) + 1
            calls.append(call_number)
            if call_number == 1:
                first_entered.set()
                if not allow_first_to_finish.wait(timeout=3):
                    raise TimeoutError("test did not release first request")
            else:
                second_entered.set()
            return _ok_response({"response": "ok"})

        def generate() -> None:
            try:
                generate_with_ollama(model="gemma4:e4b", prompt="hello")
            except BaseException as exc:  # captured for an assertion in the test thread
                errors.append(exc)

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            first = threading.Thread(target=generate)
            second = threading.Thread(target=generate)
            first.start()
            assert first_entered.wait(timeout=2)
            second.start()
            try:
                assert not second_entered.wait(timeout=0.1)
            finally:
                allow_first_to_finish.set()
            first.join(timeout=2)
            second.join(timeout=2)

        assert not first.is_alive() and not second.is_alive()
        assert errors == []
        assert calls == [1, 2]

    def test_generate_still_raises_the_unified_error_on_a_genuine_timeout(self) -> None:
        def fake_urlopen(request: Any, timeout: float | None = None) -> _FakeResponse:
            raise TimeoutError("timed out")

        with (
            patch("urllib.request.urlopen", side_effect=fake_urlopen),
            pytest.raises(OllamaRuntimeUnavailableError),
        ):
            generate_with_ollama(model="gemma4:e4b", prompt="hello")

    def test_cheap_metadata_calls_keep_the_short_budget(self) -> None:
        # /api/tags and /api/version must still fail fast when Ollama is down --
        # the long generate budget must not leak into the daemon-liveness checks.
        captured: list[float | None] = []

        def fake_urlopen(request: Any, timeout: float | None = None) -> _FakeResponse:
            captured.append(timeout)
            url = request.full_url
            if url.endswith("/api/tags"):
                return _ok_response(
                    {
                        "models": [
                            {
                                "name": "gemma4:e4b",
                                "digest": "a" * 64,
                                "details": {},
                            }
                        ]
                    }
                )
            return _ok_response({"version": "0.9.0"})

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            manifest = get_ollama_model_manifest("gemma4:e4b")

        assert manifest.model == "gemma4:e4b"
        assert captured == [DEFAULT_TIMEOUT_SECONDS, DEFAULT_TIMEOUT_SECONDS]
        assert all(t < DEFAULT_GENERATE_TIMEOUT_SECONDS for t in captured)


# F10 (RC4 final-build field run, 2026-11-05): another program held 127.0.0.1:11434
# and accepted connections but never answered. The control plane builds the live
# translator at startup, which reads /api/tags then /api/version -- at 120 s each it
# sat silent for minutes, the supervisor's 60 s no-progress detector halted it, and
# setup rolled the whole install back. The metadata budget must stay well inside the
# control plane's startup budget so a silent port degrades AI instead of the station.
CONTROL_PLANE_STALL_SECONDS = 60


def test_metadata_budget_fits_inside_control_plane_startup() -> None:
    # Two metadata calls per manifest probe, with headroom for the rest of startup.
    assert 2 * DEFAULT_TIMEOUT_SECONDS <= CONTROL_PLANE_STALL_SECONDS / 2


def test_silent_listener_fails_fast_instead_of_hanging_startup() -> None:
    import socket
    import time

    with socket.socket() as silent:
        silent.bind(("127.0.0.1", 0))
        silent.listen()
        port = silent.getsockname()[1]
        started = time.monotonic()
        with pytest.raises(OllamaRuntimeUnavailableError):
            get_ollama_model_manifest("translategemma:4b", base_url=f"http://127.0.0.1:{port}")
        elapsed = time.monotonic() - started
    assert elapsed < DEFAULT_TIMEOUT_SECONDS + 5, elapsed
