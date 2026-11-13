# SPDX-License-Identifier: Apache-2.0
# Copyright (c) The CivicCast Authors
"""Task #57 D2: the OPTIONAL fourth supervisor child -- the local-AI runtime.

Grounded in installer/app ground truth, not in what the supervisor happened
to do:

* the reviewed ollama binary lands at ``<install_root>\\dependencies\\ollama\\
  ollama.exe`` and the composed model store at ``<install_root>\\models\\
  ollama`` (``apps/installer/src-tauri/src/native_activation.rs``,
  ``validate_staged_runtime_layout`` + ``compose_ollama_model_store``); the
  acquisition flow stages gemma4:12b at ``<install_root>\\packs\\
  local-ai-model\\models`` (``acquisition_catalog.rs`` ``local_ai_model_root``)
  and wires NO binary component at all;
* the app-side consumers dial ``http://127.0.0.1:11434``
  (``civiccast/ai_runtime/ollama_client.py`` ``DEFAULT_OLLAMA_BASE_URL``;
  ``summary/ollama.py`` and ``translate/ollama.py`` default their
  ``base_url`` to it), so the child must serve exactly that host:port;
* the launch/readiness shape mirrors the installer's production self-test
  (``main.rs`` ``NativeOllamaSelfTestServer``: ``ollama serve`` with
  ``OLLAMA_HOST``/``OLLAMA_MODELS`` in the SERVER's env, ``/api/version``
  readiness).

Every test here fails at the pre-change tree (the ollama child, its spec
factory, its decision model, and the provider wiring did not exist).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

import pytest

from civiccast.ai_runtime.ollama_client import DEFAULT_OLLAMA_BASE_URL
from civiccast.native.models import GuardDecision
from civiccast.native.runtime_guard import GuardMonitorStatus
from civiccast.native.supervisor.children import (
    DEFAULT_OLLAMA_HOST,
    DEFAULT_OLLAMA_PORT,
    DEFAULT_OLLAMA_READY_BUDGET_SECONDS,
    OllamaChildDecision,
    check_ollama_ready,
    ollama_child_spec,
)
from civiccast.native.supervisor.config import SupervisorConfig
from civiccast.native.supervisor.core import Supervisor
from civiccast.native.supervisor.install_layout import resolve_install_layout

# ---------------------------------------------------------------------------
# Fakes (mirroring tests/native/test_supervisor_wiring_batch.py)
# ---------------------------------------------------------------------------


@dataclass
class FakeHandle:
    pid: int


@dataclass
class FakeRunner:
    spawned: list = field(default_factory=list)
    terminated_pids: list[int] = field(default_factory=list)
    alive: dict[int, bool] = field(default_factory=dict)
    _next_pid: int = 1000

    def spawn(self, spec) -> FakeHandle:
        self._next_pid += 1
        self.spawned.append(spec)
        self.alive[self._next_pid] = True
        return FakeHandle(pid=self._next_pid)

    def open_existing(self, pid: int) -> FakeHandle:
        self.alive[pid] = True
        return FakeHandle(pid=pid)

    def is_alive(self, handle: FakeHandle) -> bool:
        return self.alive.get(handle.pid, False)

    def cpu_seconds(self, handle: FakeHandle) -> float | None:
        # U31: no process behind a fake handle -> "no evidence", so the
        # readiness poll keeps its plain wall-clock budget (see the same stub
        # in test_supervisor_core's FakeRunner).
        return None

    def send_ctrl_break(self, handle: FakeHandle) -> None:
        pass

    def terminate(self, handle: FakeHandle) -> None:
        self.terminated_pids.append(handle.pid)
        self.alive[handle.pid] = False

    def graceful_stop(self, handle: FakeHandle) -> object:
        # Structural parity with the ChildProcessRunner seam (postmaster
        # containment-rollback path); these suites never drive that path.
        self.alive[handle.pid] = False
        return "argv"

    @property
    def spawned_names(self) -> list[str]:
        return [spec.name for spec in self.spawned]


class FakeGuard:
    def __init__(self) -> None:
        self.decision = GuardDecision(
            action="start",
            named_probe=None,
            message="test",
            retry_seconds=None,
            state_name=None,
        )
        self.status = GuardMonitorStatus(last_decision=self.decision)

    def pre_child_start(self) -> GuardDecision:
        return self.decision

    def evaluate_once(self) -> GuardDecision:
        return self.decision


class FakeJobApi:
    def __init__(self) -> None:
        self.assigned_pids: list[int] = []

    def create_job(self, name: str) -> object:
        return object()

    def configure_kill_on_close_no_breakaway(self, handle: object) -> None:
        return None

    def assign_process(self, handle: object, pid: int) -> None:
        self.assigned_pids.append(pid)

    def is_process_in_job(self, handle: object, pid: int) -> bool:
        return pid in self.assigned_pids

    def is_process_in_any_job(self, pid: int) -> bool:
        return pid in getattr(self, "any_job_pids", set())

    def close_job(self, handle: object) -> None:
        return None

    def open_existing_job(self, name: str) -> object | None:
        return None

    def list_job_process_ids(self, handle: object) -> list[int]:
        return []

    def terminate_job(self, handle: object, exit_code: int) -> None:
        return None


class FakeClock:
    def __init__(self) -> None:
        self.t = 0.0

    def now(self) -> float:
        return self.t

    def sleep(self, dt: float) -> None:
        self.t += dt


class RecordingProvider:
    def __init__(self, decision: OllamaChildDecision) -> None:
        self.decision = decision
        self.calls = 0

    def __call__(self) -> OllamaChildDecision:
        self.calls += 1
        return self.decision


def _launch_decision() -> OllamaChildDecision:
    return OllamaChildDecision(
        spec=ollama_child_spec(
            ollama_exe_path=r"C:\install\dependencies\ollama\ollama.exe",
            models_dir=r"C:\install\models\ollama",
        ),
        detail=r"serving staged store C:\install\models\ollama",
    )


def _ollama_row(sup: Supervisor):
    """The ollama row out of the status read tier."""

    return next(c for c in sup.status_snapshot().children if c.name == "ollama")


def _postmaster_pids(start: int = 90000):
    counter = {"n": start}

    def reader() -> int | None:
        counter["n"] += 1
        return counter["n"]

    return reader


def make_supervisor(
    *,
    provider=None,
    probe=None,
    runner: FakeRunner | None = None,
    interlock: str = "free",
    job_api: FakeJobApi | None = None,
    config: SupervisorConfig | None = None,
):
    runner = runner or FakeRunner()
    job_api = job_api or FakeJobApi()
    clock = FakeClock()
    from civiccast.native.supervisor.children import ControlPlaneHealthProbe

    sup = Supervisor(
        config=config or SupervisorConfig(),
        guard=FakeGuard(),
        job_api=job_api,
        runner=runner,
        alert_outbox=type("O", (), {"fire": lambda self, **kw: None})(),
        postgres_probe=lambda: True,
        health_probe=lambda: ControlPlaneHealthProbe(status_code=200, mode="normal"),
        clock=clock.now,
        sleep=clock.sleep,
        interlock_reader=lambda: interlock,  # type: ignore[arg-type,return-value]
        rng=lambda: 0.5,
        postmaster_pid_reader=_postmaster_pids(),
        ollama_spec_provider=provider,
        ollama_probe=probe,
        program_data_root=r"C:\ProgramData",
        postgres_data_dir="pgdata",
    )
    return sup, runner, job_api, clock


# ---------------------------------------------------------------------------
# children.py: the spec shape and the readiness gate
# ---------------------------------------------------------------------------


def test_ollama_child_spec_mirrors_the_installer_self_test_shape() -> None:
    """argv/cwd/env mirror NativeOllamaSelfTestServer::start (main.rs): the
    staged binary run as `serve` from its own directory, OLLAMA_HOST +
    OLLAMA_MODELS + offline hardening in the SERVER's environment."""

    spec = ollama_child_spec(
        ollama_exe_path=r"C:\i\dependencies\ollama\ollama.exe",
        models_dir=r"C:\i\models\ollama",
    )
    assert spec.name == "ollama"
    assert spec.argv == [r"C:\i\dependencies\ollama\ollama.exe", "serve"]
    assert spec.cwd == str(Path(r"C:\i\dependencies\ollama\ollama.exe").parent)
    assert spec.env["OLLAMA_MODELS"] == r"C:\i\models\ollama"
    assert spec.env["OLLAMA_HOST"] == f"{DEFAULT_OLLAMA_HOST}:{DEFAULT_OLLAMA_PORT}"
    assert spec.env["OLLAMA_NO_CLOUD"] == "1"
    assert "OLLAMA_KEEP_ALIVE" not in spec.env, (
        "the self-test's one-shot KEEP_ALIVE=0 economy must NOT be copied to the "
        "runtime child (it would reload a multi-GB model per request)"
    )
    # D5 stop shape: CTRL_BREAK to the child's own process group, like the CP.
    assert spec.new_process_group is True
    assert spec.graceful_stop_kind == "ctrl_break_event"
    assert spec.readiness_budget_seconds == DEFAULT_OLLAMA_READY_BUDGET_SECONDS == 60.0


def test_ollama_child_uses_an_integrated_graphics_chip_with_a_pinned_context() -> None:
    """Beelink / R9 measurement 2026-11-12: ollama drops a Radeon 780M unless
    OLLAMA_IGPU_ENABLE=1; with it on, the default context must stay 4,096
    (ollama would pick 262,144 from shared memory counted as VRAM)."""

    spec = ollama_child_spec(
        ollama_exe_path=r"C:\i\dependencies\ollama\ollama.exe",
        models_dir=r"C:\i\models\ollama",
    )
    assert spec.env["OLLAMA_IGPU_ENABLE"] == "1"
    assert spec.env["OLLAMA_CONTEXT_LENGTH"] == "4096"


def test_ollama_child_integrated_graphics_can_be_turned_off_by_extra_env() -> None:
    spec = ollama_child_spec(
        ollama_exe_path=r"C:\i\dependencies\ollama\ollama.exe",
        models_dir=r"C:\i\models\ollama",
        extra_env={"OLLAMA_IGPU_ENABLE": "0", "OLLAMA_CONTEXT_LENGTH": "8192"},
    )
    assert spec.env["OLLAMA_IGPU_ENABLE"] == "0"
    assert spec.env["OLLAMA_CONTEXT_LENGTH"] == "8192"


def test_ollama_child_leaves_the_chip_out_when_a_discrete_card_is_present() -> None:
    """MSI laptop measurement 2026-11-12 (RTX 4050 + Intel UHD): with the chip
    enabled, ollama put translategemma:4b on the Intel chip while gemma4:e4b
    held the card -- 12.2 tokens/s against 57.3. The context pin stays."""

    spec = ollama_child_spec(
        ollama_exe_path=r"C:\i\dependencies\ollama\ollama.exe",
        models_dir=r"C:\i\models\ollama",
        use_integrated_gpu=False,
    )
    assert "OLLAMA_IGPU_ENABLE" not in spec.env
    assert spec.env["OLLAMA_CONTEXT_LENGTH"] == "4096"


@pytest.mark.parametrize(("card", "expected"), [(True, None), (False, "1")])
def test_ollama_provider_enables_the_chip_only_without_a_discrete_card(
    tmp_path: Path, card: bool, expected: str | None
) -> None:
    from civiccast.native.supervisor.service import build_ollama_spec_provider

    layout = _make_layout(tmp_path, binary=True, composed=True, pack=False)
    decision = build_ollama_spec_provider(layout, discrete_gpu_probe=lambda: card)()
    assert decision.spec is not None
    assert decision.spec.env.get("OLLAMA_IGPU_ENABLE") == expected
    assert ("left out" in decision.detail) is card


def test_ollama_default_host_port_match_the_app_client_base_url() -> None:
    """The child serves EXACTLY where summary/translate dial by default --
    ai_runtime.ollama_client.DEFAULT_OLLAMA_BASE_URL -- so the control plane
    actually reaches it (task #57 D2)."""

    parsed = urlparse(DEFAULT_OLLAMA_BASE_URL)
    assert parsed.hostname == DEFAULT_OLLAMA_HOST
    assert parsed.port == DEFAULT_OLLAMA_PORT


def test_check_ollama_ready_outcomes() -> None:
    assert check_ollama_ready(lambda: True).outcome == "ready"
    assert check_ollama_ready(lambda: False).outcome == "not_ready"

    def _boom() -> bool:
        raise OSError("connection refused")

    result = check_ollama_ready(_boom)
    assert result.outcome == "not_ready"
    assert "raised" in result.detail


# ---------------------------------------------------------------------------
# core.py: launch, skip, recovery, snapshot visibility
# ---------------------------------------------------------------------------


def test_start_launches_the_ollama_child_and_contains_it() -> None:
    provider = RecordingProvider(_launch_decision())
    sup, runner, job_api, _clock = make_supervisor(provider=provider, probe=lambda: True)

    sup.start()

    assert sup.state == "ready", "children_ready must not depend on the optional child"
    assert "ollama" in runner.spawned_names
    ollama_handle = sup.handles()["ollama"]
    assert ollama_handle.pid in job_api.assigned_pids, "D3: contained in the Job Object"
    snap = sup.status_snapshot()
    row = next(c for c in snap.children if c.name == "ollama")
    assert row.state == "ready"
    assert row.pid == ollama_handle.pid
    assert row.blocked_detail is None


def test_skip_decision_leaves_service_healthy_and_visible() -> None:
    provider = RecordingProvider(
        OllamaChildDecision(spec=None, detail="ollama binary absent at C:\\i\\...")
    )
    sup, runner, _job, _clock = make_supervisor(provider=provider, probe=lambda: True)

    sup.start()

    assert sup.state == "ready", "degraded AI must leave the station serving"
    assert "ollama" not in runner.spawned_names
    snap = sup.status_snapshot()
    row = next(c for c in snap.children if c.name == "ollama")
    assert row.state == "stopped"
    assert row.blocked_detail is not None
    assert "skipped" in row.blocked_detail
    assert "binary absent" in row.blocked_detail


def test_a_skipped_child_starts_once_the_model_store_lands() -> None:
    """B5 field regression (TESTER2 2026-08-12,
    ``b5-failed-supervisor-ollama-reconciliation-timeout``).

    The station booted with no staged model store, so the supervisor logged one
    ``ollama child skipped ... no staged ollama model store at ...`` WARNING at
    service registration. The first-run acquisition flow then downloaded a
    COMPLETE store to the third candidate path
    (``C:\\ProgramData\\CivicCast\\packs\\local-ai-model\\models``) four hours
    later -- and the supervisor sat at 0 ollama processes / 0 listeners on
    :11434 anyway, because the skip had latched the child into ``stopped`` and
    ``_needs_restart`` exempts ``stopped`` from retry. Only an (out-of-protocol)
    reboot -- a fresh process re-running the same decision against the same disk
    -- ever started it.

    The tick loop must now do what the reboot did, WITHOUT the reboot."""

    provider = RecordingProvider(
        OllamaChildDecision(spec=None, detail="no staged ollama model store at ...")
    )
    sup, runner, _job, clock = make_supervisor(provider=provider, probe=lambda: True)

    sup.start()
    assert "ollama" not in runner.spawned_names, "no store at boot -> nothing spawned"
    assert _ollama_row(sup).state == "stopped"

    # ... the acquisition flow finishes and the store appears on disk.
    provider.decision = _launch_decision()

    clock.sleep(SupervisorConfig().optional_child_recheck_seconds)
    sup.tick(now=clock.now())

    assert "ollama" in runner.spawned_names, (
        "a store that lands after bring-up must start the runtime without a reboot"
    )
    row = _ollama_row(sup)
    assert row.state == "ready"
    assert row.blocked_detail is None
    assert sup.handles()["ollama"].pid in _job.assigned_pids, "D3 containment still applies"


def test_a_recheck_spawns_exactly_once_per_tick_when_readiness_fails() -> None:
    """The re-check flips the child out of ``stopped`` and lets the ONE existing
    restart path do the spawning. A spawn whose readiness then fails must not be
    followed by a SECOND spawn inside the same tick -- that would burn two 60s
    readiness budgets back to back and count two restarts into the D5 storm
    window for a single real attempt. The failed attempt must instead arm the
    backoff, exactly as any other child's would."""

    provider = RecordingProvider(OllamaChildDecision(spec=None, detail="no staged store"))
    sup, runner, _job, clock = make_supervisor(provider=provider, probe=lambda: False)
    sup.start()
    assert runner.spawned_names.count("ollama") == 0

    provider.decision = _launch_decision()
    clock.sleep(60.0)
    sup.tick(now=clock.now())

    assert runner.spawned_names.count("ollama") == 1, "one real attempt, one spawn"
    assert _ollama_row(sup).state == "failed"

    # And the invariant holds on the retry ticks too: a failed optional child is
    # retried by the ordinary restart path at ONE attempt per tick, never two.
    clock.sleep(60.0)
    sup.tick(now=clock.now())
    assert runner.spawned_names.count("ollama") == 2


class ClosureTrackingJobApi(FakeJobApi):
    """Records Job Object closure so an assignment AFTER close is detectable."""

    def __init__(self) -> None:
        super().__init__()
        self.closed = False
        self.assignments_after_close: list[int] = []

    def assign_process(self, handle: object, pid: int) -> None:
        if self.closed:
            self.assignments_after_close.append(pid)
        super().assign_process(handle, pid)

    def close_job(self, handle: object) -> None:
        self.closed = True
        super().close_job(handle)


class DrainInjectingRunner(FakeRunner):
    """Fires an authorized operator ``drain`` through the REAL
    :class:`AdminCommandRouter` at the instant the reconciliation loop begins
    examining children -- i.e. after the optional-child recheck has admitted and
    written ``starting``, and before the loop reaches the Ollama restart.

    This models TESTER4's interleaving: the pipe's CommandQueue worker thread
    runs ``drain`` concurrently with an in-flight tick that already passed its
    outer state gate. One-shot, so ``graceful_stop``'s own drain loop (which
    calls ``is_alive`` on the control plane) cannot re-enter it."""

    def __init__(self) -> None:
        super().__init__()
        self.supervisor = None
        self.fired = False

    def is_alive(self, handle: FakeHandle) -> bool:
        sup = self.supervisor
        if sup is not None and not self.fired:
            states = {c.name: c.state for c in sup.status_snapshot().children}
            if states.get("ollama") == "starting":
                self.fired = True
                from civiccast.native.supervisor.admin import AdminCommandRouter

                router = AdminCommandRouter(
                    supervisor=sup,
                    selector_reader=lambda: "native",
                    selector_writer=lambda _value: None,
                )
                router.handle("drain", {})
        return super().is_alive(handle)


def test_an_operator_drain_mid_tick_never_spawns_the_optional_child() -> None:
    """TESTER4 adversarial review of PR #389, vector 1 (REFUTED at review time).

    The control-pipe ``stop``/``drain`` verb reaches ``Supervisor.graceful_stop``
    directly via ``AdminCommandRouter._drain`` and does NOT set the service stop
    event that ``_should_abort`` reads. So an in-flight tick that had already
    passed its outer state gate would run the optional-child recheck, see
    ``stopped`` + ``_start_blocked``, write ``starting``, and then spawn Ollama
    into a supervisor the operator had already moved to ``stopping`` -- racing
    the Job Object close.

    With the shared stop-intent boundary the drain is visible to every
    reconciliation path the moment it begins, whichever mechanism asked."""

    provider = RecordingProvider(OllamaChildDecision(spec=None, detail="no staged store"))
    injecting = DrainInjectingRunner()
    job_api = ClosureTrackingJobApi()
    sup, runner, _job, clock = make_supervisor(
        provider=provider, probe=lambda: True, runner=injecting, job_api=job_api
    )
    injecting.supervisor = sup

    sup.start()
    assert "ollama" not in runner.spawned_names

    # The store lands, so the recheck WILL admit on the next due tick -- and the
    # operator drains the station in the middle of that very tick.
    provider.decision = _launch_decision()
    clock.sleep(SupervisorConfig().optional_child_recheck_seconds)
    sup.tick(now=clock.now())  # must not raise

    assert injecting.fired, "the interleaving under test never actually occurred"
    assert sup.state == "stopping"
    assert "ollama" not in runner.spawned_names, (
        "an operator stop must win over the optional-child recheck"
    )
    assert job_api.assignments_after_close == [], "no Job Object assignment after close"


def test_a_pipe_drain_is_visible_to_reconciliation_like_an_scm_stop() -> None:
    """The two stop mechanisms must present the SAME abort signal. The SCM path
    sets the service event; the pipe path calls ``graceful_stop`` directly. After
    either, no later tick may spawn a child or promote one to ``starting``."""

    provider = RecordingProvider(OllamaChildDecision(spec=None, detail="no staged store"))
    sup, runner, _job, clock = make_supervisor(provider=provider, probe=lambda: True)
    sup.start()

    sup.graceful_stop()  # what AdminCommandRouter._drain does; no service event
    spawns_at_stop = list(runner.spawned_names)

    provider.decision = _launch_decision()
    for _ in range(5):
        clock.sleep(120.0)
        sup.tick(now=clock.now())

    assert runner.spawned_names == spawns_at_stop, "a stopped supervisor spawns nothing"
    assert "ollama" not in runner.spawned_names
    assert _ollama_row(sup).state != "starting", "no child may be promoted after a stop"


def test_the_recheck_beats_the_field_reconciliation_gate() -> None:
    """TESTER2's controller gate allowed five minutes between a complete model
    store and a supervisor-owned listener. The default re-check cadence must
    leave room for the child's readiness budget inside that window, so the
    shipped default is not merely 'eventually'."""

    budget = DEFAULT_OLLAMA_READY_BUDGET_SECONDS
    assert SupervisorConfig().optional_child_recheck_seconds + budget < 300.0


def test_the_recheck_is_throttled_rather_than_per_tick() -> None:
    """The original design's real concern stands: a station that will never have
    a local AI runtime must not re-stat the install tree once a second. The
    re-check is throttled, so a 5-minute run of 1-second ticks costs a handful
    of provider calls, not 300."""

    provider = RecordingProvider(OllamaChildDecision(spec=None, detail="no staged store"))
    sup, _runner, _job, clock = make_supervisor(provider=provider, probe=lambda: True)

    sup.start()
    calls_after_start = provider.calls

    for _ in range(300):
        clock.sleep(1.0)
        sup.tick(now=clock.now())

    rechecks = provider.calls - calls_after_start
    assert rechecks <= 300 / SupervisorConfig().optional_child_recheck_seconds + 1
    assert rechecks >= 1, "but it must actually re-check"


def test_an_unsatisfied_recheck_leaves_the_station_serving_and_quiet() -> None:
    """A permanently AI-less station: every re-check re-skips. Nothing is
    spawned, the machine keeps serving, and the D5 restart-storm window is never
    armed -- lacking an optional runtime must never demote a station to
    ``degraded``."""

    provider = RecordingProvider(OllamaChildDecision(spec=None, detail="no staged store"))
    sup, runner, _job, clock = make_supervisor(provider=provider, probe=lambda: True)

    sup.start()
    for _ in range(20):
        clock.sleep(60.0)
        sup.tick(now=clock.now())

    assert sup.state == "ready", "a missing optional runtime is not a restart storm"
    assert "ollama" not in runner.spawned_names
    row = _ollama_row(sup)
    assert row.state == "stopped"
    assert "skipped" in (row.blocked_detail or "")


def test_the_recheck_reports_a_changed_skip_reason() -> None:
    """The store arrives but the binary is still absent: the child stays
    stopped and the operator-visible reason is the CURRENT one, not the stale
    boot-time one."""

    provider = RecordingProvider(OllamaChildDecision(spec=None, detail="no staged store"))
    sup, _runner, _job, clock = make_supervisor(provider=provider, probe=lambda: True)
    sup.start()

    provider.decision = OllamaChildDecision(spec=None, detail="ollama binary absent at C:\\i\\...")
    clock.sleep(120.0)
    sup.tick(now=clock.now())

    row = _ollama_row(sup)
    assert row.state == "stopped"
    assert "binary absent" in (row.blocked_detail or "")


def test_the_recheck_is_inert_without_the_optional_child() -> None:
    """A supervisor with no ollama seams configured never touches the path."""

    sup, runner, _job, clock = make_supervisor()
    sup.start()
    for _ in range(5):
        clock.sleep(120.0)
        sup.tick(now=clock.now())

    assert "ollama" not in runner.spawned_names
    assert sup.state == "ready"


def test_dead_ollama_child_is_restarted_by_the_tick_loop() -> None:
    provider = RecordingProvider(_launch_decision())
    sup, runner, _job, clock = make_supervisor(provider=provider, probe=lambda: True)
    sup.start()
    first_pid = sup.handles()["ollama"].pid

    # Kill it and reconcile.
    runner.alive[first_pid] = False
    clock.sleep(120.0)
    sup.tick(now=clock.now())

    assert sup.state in ("ready", "degraded")
    replacement = sup.handles()["ollama"]
    assert replacement.pid != first_pid
    assert runner.spawned_names.count("ollama") == 2


def test_ollama_death_never_moves_the_machine_out_of_ready() -> None:
    """The optional child's death is NOT a D6 dependency loss: the machine
    stays serving (postgres/CP untouched) while it is restarted."""

    provider = RecordingProvider(_launch_decision())
    sup, runner, _job, clock = make_supervisor(provider=provider, probe=lambda: True)
    sup.start()
    cp_pid = sup.handles()["control_plane"].pid

    runner.alive[sup.handles()["ollama"].pid] = False
    clock.sleep(120.0)
    sup.tick(now=clock.now())

    assert sup.state == "ready"
    assert sup.handles()["control_plane"].pid == cp_pid, "the CP must not be touched"


def test_unconfigured_supervisor_keeps_the_two_child_snapshot() -> None:
    sup, _runner, _job, _clock = make_supervisor(provider=None, probe=None)
    sup.start()
    snap = sup.status_snapshot()
    assert [c.name for c in snap.children] == ["postgres", "control_plane"]


def test_provider_and_probe_must_be_paired() -> None:
    with pytest.raises(ValueError, match="together"):
        make_supervisor(provider=RecordingProvider(_launch_decision()), probe=None)
    with pytest.raises(ValueError, match="together"):
        make_supervisor(provider=None, probe=lambda: True)


def test_boot_held_interlock_still_starts_or_skips_ollama_cleanly() -> None:
    """Pre-activation/maintenance boot (interlock HELD): the optional child
    is handled before the writer gate and skips cleanly -- the maintenance
    posture is unaffected (task #57 D2's pre-activation requirement)."""

    provider = RecordingProvider(OllamaChildDecision(spec=None, detail="station not activated"))
    sup, runner, _job, _clock = make_supervisor(
        provider=provider, probe=lambda: True, interlock="held"
    )

    sup.start()

    assert sup.state == "maintenance"
    assert "ollama" not in runner.spawned_names
    row = next(c for c in sup.status_snapshot().children if c.name == "ollama")
    assert row.blocked_detail is not None and "station not activated" in row.blocked_detail


# ---------------------------------------------------------------------------
# service.py: the layout-gated spec provider and the stop chain
# ---------------------------------------------------------------------------


def _make_layout(tmp_path: Path, *, binary: bool, composed: bool, pack: bool):
    install_root = tmp_path / "install"
    (install_root / "runtime").mkdir(parents=True)
    (install_root / "runtime" / "python.exe").write_bytes(b"")
    if binary:
        exe = install_root / "dependencies" / "ollama" / "ollama.exe"
        exe.parent.mkdir(parents=True)
        exe.write_bytes(b"")
    if composed:
        (install_root / "models" / "ollama" / "manifests").mkdir(parents=True)
    if pack:
        (install_root / "packs" / "local-ai-model" / "models" / "manifests").mkdir(parents=True)
    return resolve_install_layout(
        executable=install_root / "runtime" / "python.exe",
        program_data_root=tmp_path / "ProgramData",
    )


def test_spec_provider_launches_only_with_binary_and_store(tmp_path: Path) -> None:
    from civiccast.native.supervisor.service import build_ollama_spec_provider

    layout = _make_layout(tmp_path, binary=True, composed=True, pack=False)
    decision = build_ollama_spec_provider(layout)()
    assert decision.spec is not None
    assert decision.spec.argv[0] == str(layout.ollama_exe_path)
    assert decision.spec.env["OLLAMA_MODELS"] == str(layout.ollama_models_dir)


def test_spec_provider_prefers_the_composed_store_over_the_acquisition_pack(
    tmp_path: Path,
) -> None:
    from civiccast.native.supervisor.service import build_ollama_spec_provider

    layout = _make_layout(tmp_path, binary=True, composed=True, pack=True)
    decision = build_ollama_spec_provider(layout)()
    assert decision.spec is not None
    assert decision.spec.env["OLLAMA_MODELS"] == str(layout.ollama_models_dir)

    layout_pack_only = _make_layout(tmp_path / "b", binary=True, composed=False, pack=True)
    decision = build_ollama_spec_provider(layout_pack_only)()
    assert decision.spec is not None
    assert decision.spec.env["OLLAMA_MODELS"] == str(layout_pack_only.local_ai_pack_models_dir)


def test_spec_provider_skips_without_binary_or_store(tmp_path: Path) -> None:
    from civiccast.native.supervisor.service import build_ollama_spec_provider

    no_binary = build_ollama_spec_provider(
        _make_layout(tmp_path / "a", binary=False, composed=True, pack=False)
    )()
    assert no_binary.spec is None
    assert "binary absent" in no_binary.detail

    no_store = build_ollama_spec_provider(
        _make_layout(tmp_path / "b", binary=True, composed=False, pack=False)
    )()
    assert no_store.spec is None
    assert "model store" in no_store.detail


def test_spec_provider_logs_a_skip_once_per_reason(tmp_path: Path) -> None:
    import logging

    from civiccast.native.supervisor.service import build_ollama_spec_provider

    records: list[str] = []

    class _Capture(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record.getMessage())

    logger = logging.getLogger("test.ollama.skip.once")
    logger.addHandler(_Capture())
    logger.setLevel(logging.DEBUG)

    provider = build_ollama_spec_provider(
        _make_layout(tmp_path, binary=False, composed=False, pack=False), logger=logger
    )
    provider()
    provider()
    provider()

    assert len(records) == 1, "one clear line, not one per restart attempt"
    assert "degraded AI" in records[0]


def test_graceful_stop_chain_stops_ollama_after_the_control_plane() -> None:
    """RAT-004 ordering with the optional child: CP drains first (it is the
    ollama consumer), then ollama, then postgres."""

    from civiccast.native.supervisor.service import SupervisorService

    provider = RecordingProvider(_launch_decision())
    sup, runner, _job, clock = make_supervisor(provider=provider, probe=lambda: True)
    sup.start()
    pid_to_name = {handle.pid: name for name, handle in sup.handles().items()}

    stop_sequence: list[str] = []

    class ServiceRunner:
        def graceful_stop(self, handle):
            stop_sequence.append(pid_to_name[handle.pid])
            runner.alive[handle.pid] = False
            return "argv"

        def is_alive(self, handle) -> bool:
            return runner.alive.get(handle.pid, False)

        def terminate(self, handle) -> None:
            runner.alive[handle.pid] = False

    service = SupervisorService(
        supervisor=sup,
        runner=ServiceRunner(),
        config=SupervisorConfig(),
        clock=clock.now,
        sleep=clock.sleep,
    )
    results = service.graceful_stop_all()

    assert stop_sequence == ["control_plane", "ollama", "postgres"]
    assert [r.name for r in results] == stop_sequence
    assert all(r.outcome == "exited" for r in results)


def test_build_production_service_wires_the_optional_child(tmp_path: Path) -> None:
    """The production assembly carries the ollama provider + probe: the
    snapshot exposes the fourth row (skipped cleanly on a tree without the
    local-AI pack -- the pre-activation shape)."""

    import logging

    from civiccast.native.supervisor.service import build_production_service

    layout = _make_layout(tmp_path, binary=False, composed=False, pack=False)

    class _Guard:
        status = GuardMonitorStatus(last_decision=None)

    class _Outbox:
        def fire(self, *, summary: str, detail: str) -> None:
            pass

    service = build_production_service(
        logging.getLogger("test.ollama.wiring"),
        guard=_Guard(),  # type: ignore[arg-type]
        alert_outbox=_Outbox(),
        postgres_probe=lambda: True,
        health_probe=lambda: pytest.fail("probe must not run at wiring time"),  # type: ignore[arg-type,return-value]
        ollama_probe=lambda: True,
        layout=layout,
        program_data_root=str(tmp_path / "ProgramData"),
    )

    snap = service._supervisor.status_snapshot()
    assert [c.name for c in snap.children] == ["postgres", "control_plane", "ollama"]
