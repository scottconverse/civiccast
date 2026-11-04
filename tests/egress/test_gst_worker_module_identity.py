# SPDX-License-Identifier: Apache-2.0
# Copyright (c) The CivicCast Authors
"""The worker and the engine must bind ONE copy of the graph dataclasses —
without the worker importing the ``civiccast`` package.

Gate A T4 root cause (2026-09): ``worker.py`` imported its sibling modules BY
PATH (``import graph``) while ``engine.py`` prefers the package form
(``from civiccast.egress.gst.graph import ...``). On the native Windows line the
bundled GStreamer runtime makes the engine's ``gi`` import succeed, so the two
halves bound two DISTINCT ``PlaylistLeg`` classes compiled from the same file.
``engine._instantiate_source_leg``'s ``isinstance(leg, PlaylistLeg)`` dispatch
then missed on every program leg (``bridge.graph_from_config`` always builds a
``PlaylistLeg``), fell through to the ``SourceLeg`` branch and raised
``AttributeError: 'PlaylistLeg' object has no attribute 'elements'`` inside
``GstPlayoutEngine.__init__`` — before the pipeline ever reached PLAYING, so the
udp-ts sink never emitted a single MPEG-TS packet.

``worker._sibling_module`` fixes that by publishing each by-path module under its
package name in ``sys.modules``, which the engine's package-form import then
resolves to. The two properties that matter are tested here:

* **identity** — both halves see one ``PlaylistLeg``;
* **isolation** — ``worker.py``'s import block still imports NO ``civiccast``
  package module, so ``civiccast/egress/__init__.py`` (771 modules, sqlalchemy +
  pydantic) never lands in the worker process. A package-first import would have
  fixed the identity split and broken exactly this.

Scope note, measured rather than assumed: this covers ``worker.py``'s OWN
imports. The worker process as a whole is not pydantic-free today — ``engine.py``
imports ``civiccast.native.gstreamer_runtime`` to bootstrap the bundled closure
and ``civiccast/native/__init__.py`` re-exports a pydantic module. That is
engine.py's import, it predates this seam, and it is out of scope here.

The engine module imports ``gi``, which is not installed in this test
environment (it lives only in the shipped GStreamer closure), so the engine is
stubbed in ``sys.modules`` under its PACKAGE name — which is also exactly the
"already imported, adopt it" branch of ``_sibling_module``.
"""

from __future__ import annotations

import importlib
import json
import subprocess
import sys
import textwrap
import types
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

import civiccast.egress.gst.graph as pkg_graph

_WORKER = "civiccast.egress.gst.worker"
_WORKER_PATH = Path(pkg_graph.__file__).resolve().parent / "worker.py"
_ALIASES = tuple(f"civiccast.egress.gst.{name}" for name in ("control", "audio_tap", "engine"))


@pytest.fixture
def imported_worker(monkeypatch: pytest.MonkeyPatch) -> Iterator[types.ModuleType]:
    engine_stub = types.ModuleType("civiccast.egress.gst.engine")
    engine_stub.GstPlayoutEngine = object  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "civiccast.egress.gst.engine", engine_stub)
    for cached in (_WORKER, "graph", "engine", "control", "audio_tap"):
        monkeypatch.delitem(sys.modules, cached, raising=False)
    already_aliased = {name: sys.modules.get(name) for name in _ALIASES}
    module = importlib.import_module(_WORKER)
    try:
        yield module
    finally:
        # The by-path imports publish real package names; restore what was there.
        sys.modules.pop(_WORKER, None)
        for name, previous in already_aliased.items():
            if previous is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = previous


def test_worker_adopts_an_already_imported_package_module(
    imported_worker: types.ModuleType,
) -> None:
    """``_sibling_module`` adopts a module already present under its package name
    rather than loading a second copy of the same file."""
    assert imported_worker.graphmod is pkg_graph
    assert imported_worker.graphmod.PlaylistLeg is pkg_graph.PlaylistLeg
    assert imported_worker.enginemod is sys.modules["civiccast.egress.gst.engine"]


def test_worker_deserialized_program_leg_is_the_engines_playlist_leg(
    imported_worker: types.ModuleType,
) -> None:
    """A graph deserialized by the worker passes the engine's ``isinstance``
    dispatch. This is the exact predicate that missed in Gate A T4."""
    graph = pkg_graph.PlayoutGraph(
        sources=(
            pkg_graph.PlaylistLeg(
                label="program",
                subchains=((pkg_graph.ElementSpec("filesrc", props={"location": "slate.ts"}),),),
            ),
            pkg_graph.SourceLeg(
                label="slate",
                elements=(pkg_graph.ElementSpec("videotestsrc", props={"pattern": 2}),),
            ),
        ),
        encoder=(pkg_graph.ElementSpec("x264enc"),),
        mux=pkg_graph.ElementSpec("mpegtsmux", name="mux"),
        sinks=(
            (
                pkg_graph.ElementSpec("queue"),
                pkg_graph.ElementSpec("udpsink", props={"host": "127.0.0.1", "port": 19003}),
            ),
        ),
    )
    round_tripped = imported_worker.graphmod.graph_from_json(pkg_graph.graph_to_json(graph))
    program_leg = round_tripped.sources[0]
    assert isinstance(program_leg, pkg_graph.PlaylistLeg)


# The production configuration is a FRESH process that loads worker.py by path
# with no civiccast package imported at all — the two assertions below can only
# be made there, so this runs the real thing in a subprocess. The engine is
# stubbed via a sys.modules entry (a bare key needs no parent package), which is
# what keeps `gi` out of it; everything else is the worker's own import block.
_PROBE = textwrap.dedent(
    """
    import importlib.util, json, sys, types

    sys.modules["civiccast.egress.gst.engine"] = types.ModuleType(
        "civiccast.egress.gst.engine"
    )
    spec = importlib.util.spec_from_file_location("worker_under_test", sys.argv[1])
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    # A key under a civiccast name is only an ALIAS the worker published if the
    # module behind it does not know itself by that name (its __name__ is the bare
    # sibling, or it is the injected engine stub). Anything else means the real
    # package was imported.
    aliases, heavy = [], []
    for name, module in sorted(sys.modules.items()):
        if name.split(".")[0] not in {"civiccast", "sqlalchemy", "pydantic", "fastapi"}:
            continue
        if name.startswith("civiccast.egress.gst.") and getattr(module, "__name__", "") != name:
            aliases.append(name)
        elif name == "civiccast.egress.gst.engine":
            aliases.append(name)  # the stub this probe injected
        else:
            heavy.append(name)
    print(json.dumps({
        "heavy": heavy,
        "aliases": aliases,
        "graph_alias_is_by_path_module": (
            sys.modules.get("civiccast.egress.gst.graph") is sys.modules.get("graph")
        ),
        "graph_file": getattr(sys.modules.get("graph"), "__file__", None),
    }))
    """
)


@pytest.fixture(scope="module")
def worker_probe() -> dict[str, Any]:
    completed = subprocess.run(
        [sys.executable, "-c", _PROBE, str(_WORKER_PATH)],
        capture_output=True,
        text=True,
        check=False,
        cwd=str(Path(__file__).resolve().parents[2]),
    )
    assert completed.returncode == 0, completed.stderr
    result: dict[str, Any] = json.loads(completed.stdout.strip().splitlines()[-1])
    return result


def test_worker_imports_no_civiccast_package_module(worker_probe: dict[str, Any]) -> None:
    """Loading worker.py pulls in NO ``civiccast`` package module — and therefore
    no sqlalchemy/pydantic. ``civiccast/egress/__init__.py`` alone is 771 modules;
    the worker's contract is stdlib + ``gi`` + its siblings, nothing more."""
    assert worker_probe["heavy"] == []
    # ...and the only civiccast-named entries are the aliases the worker published
    # itself (plus the engine stub this probe injected), never real package modules.
    assert sorted(worker_probe["aliases"]) == [
        "civiccast.egress.gst.audio_tap",
        "civiccast.egress.gst.control",
        # engine.py imports the gi-free CPU-decode policy from this sibling at module
        # scope, so the worker must publish it under the package name too or the
        # engine's package-form import would drag in the real civiccast.egress package.
        "civiccast.egress.gst.decode_policy",
        "civiccast.egress.gst.engine",
        # Item 82: gi-free exit-code contract shared with the daemon. Not
        # imported by engine.py itself (no identity-split risk), but published
        # the same way as every other sibling for consistency.
        "civiccast.egress.gst.exit_codes",
        "civiccast.egress.gst.graph",
        # engine.py imports the gi-free optional-property rule (mfh26xenc gop-size)
        # at module scope -- same identity reasoning as decode_policy.
        "civiccast.egress.gst.optional_props",
        # B3 fix: engine.py also imports the gi-free reload-switch-mode decoder
        # from this sibling at module scope, same reasoning as decode_policy.
        "civiccast.egress.gst.reload_policy",
    ]


def test_worker_publishes_its_by_path_siblings_under_the_package_names(
    worker_probe: dict[str, Any],
) -> None:
    """In the production configuration the sibling is loaded by path AND published
    as ``civiccast.egress.gst.graph`` — one object under both names, which is what
    makes the engine's package-form import resolve to the worker's module."""
    assert worker_probe["graph_alias_is_by_path_module"] is True
    graph_file = worker_probe["graph_file"]
    assert isinstance(graph_file, str)
    assert Path(graph_file).resolve() == Path(pkg_graph.__file__).resolve()
