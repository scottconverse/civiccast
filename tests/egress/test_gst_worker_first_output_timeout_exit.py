# SPDX-License-Identifier: Apache-2.0
# Copyright (c) The CivicCast Authors
"""Item 84: ``worker.py``'s ``main()`` must return
``civiccast.egress.gst.exit_codes.GST_FIRST_OUTPUT_TIMEOUT_EXIT_CODE`` -- not the
generic crash code (1) -- when the engine's ``run_forever()`` returns a result whose
``error`` reason is ``("first-output-timeout", ...)``.

Unlike ``PrerollTimeoutError`` (item 82, ``test_gst_worker_preroll_timeout_exit.py``),
this exit is NOT raised as an exception -- ``GstPlayoutEngine._check_stall`` sets
``self._error`` and quits the run loop exactly like the ordinary post-first-buffer
stall does, so ``run_forever()`` returns its result dict normally and ``main()`` has to
tell this reason apart from every OTHER engine failure by inspecting the returned
``error`` tuple, not by catching a distinct exception type.

Mirrors ``test_gst_worker_preroll_timeout_exit.py``'s fixture exactly -- ``worker.py``
is import-safe with only stdlib + the sibling gi-free modules, so this drives
``main()`` directly against a FAKE ``civiccast.egress.gst.engine`` stubbed into
``sys.modules``, no real ``gi``/GStreamer install needed.
"""

from __future__ import annotations

import ast
import importlib
import sys
import types
from collections.abc import Iterator
from pathlib import Path

import pytest

from civiccast.egress.gst.graph import demo_test_graph, graph_to_json

_WORKER = "civiccast.egress.gst.worker"
_ALIASES = tuple(
    f"civiccast.egress.gst.{name}"
    for name in ("control", "audio_tap", "engine", "decode_policy", "reload_policy", "exit_codes")
)


class _FakePrerollTimeoutError(RuntimeError):
    """Stand-in for ``engine.PrerollTimeoutError`` -- worker.py's ``except``
    clause matches on ``enginemod.PrerollTimeoutError``, so the stub module
    must carry a real exception class under that attribute name even though
    this file never raises it (mirrors the fixture in
    ``test_gst_worker_preroll_timeout_exit.py``)."""


@pytest.fixture
def worker_module() -> Iterator[types.ModuleType]:
    """Import ``worker.py`` fresh with ``civiccast.egress.gst.engine`` stubbed
    (it needs real ``gi``, which this test environment does not have) --
    mirrors ``test_gst_worker_preroll_timeout_exit.py``'s ``worker_module``
    fixture exactly."""
    engine_stub = types.ModuleType("civiccast.egress.gst.engine")
    engine_stub.GstPlayoutEngine = object  # type: ignore[attr-defined]
    engine_stub.PrerollTimeoutError = _FakePrerollTimeoutError  # type: ignore[attr-defined]
    saved_engine = sys.modules.get("civiccast.egress.gst.engine")
    sys.modules["civiccast.egress.gst.engine"] = engine_stub
    for cached in (
        _WORKER,
        "graph",
        "engine",
        "control",
        "audio_tap",
        "decode_policy",
        "reload_policy",
        "exit_codes",
    ):
        sys.modules.pop(cached, None)
    already_aliased = {name: sys.modules.get(name) for name in _ALIASES}
    module = importlib.import_module(_WORKER)
    try:
        yield module
    finally:
        sys.modules.pop(_WORKER, None)
        for name, previous in already_aliased.items():
            if previous is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = previous
        if saved_engine is None:
            sys.modules.pop("civiccast.egress.gst.engine", None)
        else:
            sys.modules["civiccast.egress.gst.engine"] = saved_engine


class _FakeEngineInstanceReturnsResult:
    """Duck-typed stand-in for ``GstPlayoutEngine`` whose ``run_forever``
    returns a result dict normally (never raises) -- exactly how the real
    engine reports a first-output timeout (and an ordinary stall) via
    ``self._error`` + ``loop.quit()``, unlike ``PrerollTimeoutError``."""

    def __init__(self, result: dict[str, object]) -> None:
        self._result = result

    def set_caption_delivery_clock(self, clock: object) -> None:
        self.caption_delivery_clock = clock

    def __call__(self, *_a: object, **_kw: object) -> _FakeEngineInstanceReturnsResult:
        return self

    def run_forever(
        self,
        *,
        control_fifo: str | None = None,
        hold_slate_at_plan_eos: bool = False,
    ) -> dict[str, object]:
        del hold_slate_at_plan_eos
        return self._result


def _write_graph_file(tmp_path: Path) -> Path:
    graph_path = tmp_path / "playout-graph.json"
    graph_path.write_text(graph_to_json(demo_test_graph()), encoding="utf-8")
    return graph_path


def test_main_returns_the_first_output_timeout_exit_code(
    worker_module: types.ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    graph_path = _write_graph_file(tmp_path)
    monkeypatch.setattr(sys, "argv", ["worker.py", str(graph_path)])
    fake = _FakeEngineInstanceReturnsResult(
        {
            "error": ("first-output-timeout", "no output buffers observed within bound"),
            "teardown_clean": True,
        }
    )
    monkeypatch.setattr(worker_module.enginemod, "GstPlayoutEngine", fake)
    monkeypatch.delenv("SWAPS", raising=False)

    exit_code = worker_module.main()

    assert exit_code == worker_module.exit_codes_mod.GST_FIRST_OUTPUT_TIMEOUT_EXIT_CODE
    assert exit_code != 0
    assert exit_code != 1  # the generic crash code every OTHER engine failure uses
    assert exit_code != worker_module.exit_codes_mod.GST_PREROLL_TIMEOUT_EXIT_CODE


def test_main_emits_the_worker_result_receipt_on_the_first_output_timeout_exit(
    worker_module: types.ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    graph_path = _write_graph_file(tmp_path)
    monkeypatch.setattr(sys, "argv", ["worker.py", str(graph_path)])
    fake = _FakeEngineInstanceReturnsResult(
        {
            "error": ("first-output-timeout", "no output buffers observed within bound"),
            "teardown_clean": True,
        }
    )
    monkeypatch.setattr(worker_module.enginemod, "GstPlayoutEngine", fake)
    monkeypatch.delenv("SWAPS", raising=False)

    worker_module.main()

    out = capsys.readouterr().out
    line = next((line for line in out.splitlines() if line.startswith("WORKER_RESULT ")), None)
    assert line is not None, "must emit a WORKER_RESULT receipt on this exit path too"
    result = ast.literal_eval(line.removeprefix("WORKER_RESULT "))
    assert isinstance(result, dict)
    error = result.get("error")
    assert error is not None
    assert error[0] == "first-output-timeout"


def test_main_still_returns_the_generic_crash_code_for_an_ordinary_stall(
    worker_module: types.ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Contrast case: the ordinary post-first-buffer ``("stall", ...)`` reason
    (unchanged by item 84) must keep returning the GENERIC crash code (1) --
    only the NEW first-output-timeout reason gets its own distinct code."""
    graph_path = _write_graph_file(tmp_path)
    monkeypatch.setattr(sys, "argv", ["worker.py", str(graph_path)])
    fake = _FakeEngineInstanceReturnsResult(
        {"error": ("stall", "output stalled"), "teardown_clean": True}
    )
    monkeypatch.setattr(worker_module.enginemod, "GstPlayoutEngine", fake)
    monkeypatch.delenv("SWAPS", raising=False)

    exit_code = worker_module.main()

    assert exit_code == 1
    assert exit_code != worker_module.exit_codes_mod.GST_FIRST_OUTPUT_TIMEOUT_EXIT_CODE


def test_main_returns_zero_on_a_clean_run(
    worker_module: types.ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    graph_path = _write_graph_file(tmp_path)
    monkeypatch.setattr(sys, "argv", ["worker.py", str(graph_path)])
    fake = _FakeEngineInstanceReturnsResult({"error": None, "teardown_clean": True})
    monkeypatch.setattr(worker_module.enginemod, "GstPlayoutEngine", fake)
    monkeypatch.delenv("SWAPS", raising=False)

    assert worker_module.main() == 0
