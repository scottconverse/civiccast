# SPDX-License-Identifier: Apache-2.0
# Copyright (c) The CivicCast Authors
"""S8-5 RuntimeSafeToAirStatus computation tests (spec §3.6/§3.7/§6.5)."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from civiccast.alerting.models import AlertEvent
from civiccast.alerting.runtime_status import (
    compute_channel_runtime_status,
    compute_runtime_safe_to_air,
    derive_live_caption_processing_status,
    live_caption_readiness,
)
from civiccast.egress.models import (
    EgressConfig,
    EgressHealthSample,
    EgressSinkSpec,
    EgressStateRow,
)

_NOW = datetime(2026, 6, 15, 12, 0, 0, tzinfo=UTC)


@pytest.mark.parametrize("provider", ["whistle-recovering", "whistle-recovery-cooldown"])
def test_whistle_recovery_state_reaches_operator_status(provider: str) -> None:
    status = derive_live_caption_processing_status(
        {
            "worker_heartbeat_at": _NOW.isoformat(),
            "last_processed_at": _NOW.isoformat(),
            "provider_state": provider,
            "provider_retry_in_seconds": 30,
        },
        egress_state="ON_AIR",
        captions_expected=True,
        now=_NOW,
    )
    assert status.provider_state == provider
    assert status.provider_retry_in_seconds == 30
    assert status.processing_state != "failed"


def test_missing_or_stale_worker_is_unknown_during_startup_then_stalled() -> None:
    startup = derive_live_caption_processing_status(
        None,
        egress_state="ON_AIR",
        captions_expected=True,
        now=_NOW,
        on_air_since=_NOW - timedelta(seconds=60),
    )
    assert startup.processing_state == "unknown"

    missing_worker = derive_live_caption_processing_status(
        None,
        egress_state="ON_AIR",
        captions_expected=True,
        now=_NOW,
        on_air_since=_NOW - timedelta(seconds=181),
    )
    assert missing_worker.processing_state == "stalled"

    stale_heartbeat = derive_live_caption_processing_status(
        {"worker_heartbeat_at": (_NOW - timedelta(seconds=91)).isoformat()},
        egress_state="ON_AIR",
        captions_expected=True,
        now=_NOW,
        on_air_since=_NOW - timedelta(seconds=300),
    )
    assert stale_heartbeat.processing_state == "stalled"

    recently_started = derive_live_caption_processing_status(
        {"worker_heartbeat_at": (_NOW - timedelta(seconds=91)).isoformat()},
        egress_state="ON_AIR",
        captions_expected=True,
        now=_NOW,
        on_air_since=_NOW - timedelta(seconds=30),
    )
    assert recently_started.processing_state == "unknown"


@pytest.mark.parametrize(
    "last_processed_at",
    [None, (_NOW - timedelta(seconds=600)).isoformat()],
    ids=["no-prior-completion", "prior-progress"],
)
def test_continuous_new_audio_does_not_mask_hung_inference(
    last_processed_at: str | None,
) -> None:
    """A fresh input timestamp cannot make a batch stalled for 10 minutes look healthy."""
    status = derive_live_caption_processing_status(
        {
            "worker_heartbeat_at": _NOW.isoformat(),
            "last_input_at": (_NOW - timedelta(seconds=5)).isoformat(),
            "last_processed_at": last_processed_at,
            "pending_since_at": (_NOW - timedelta(seconds=600)).isoformat(),
            "inference_started_at": (_NOW - timedelta(seconds=590)).isoformat(),
            "inference_inflight": True,
            "backlog_segments": 10,
        },
        egress_state="ON_AIR",
        captions_expected=True,
        now=_NOW,
    )

    assert status.processing_state == "stalled"


@pytest.mark.parametrize("prior_completion", [None, "2026-10-10T06:10:00+00:00"])
def test_failed_batch_retries_do_not_reset_pending_progress_deadline(
    tmp_path: Path, prior_completion: str | None
) -> None:
    """Replay the repaired guest: retries ran, but no input completed for minutes."""
    now = datetime.fromisoformat("2026-10-10T06:25:23.6411683+00:00")
    payload = {
        "state": "within-capacity",
        "worker_heartbeat_at": "2026-10-10T00:25:21.100786-06:00",
        "last_input_at": "2026-10-10T00:25:21.100786-06:00",
        "last_processed_at": prior_completion,
        "pending_since_at": "2026-10-10T00:21:47.378625-06:00",
        "inference_started_at": "2026-10-10T00:24:52.898708-06:00",
        "inference_inflight": True,
        "backlog_segments": 9,
        "max_backlog_segments": 2,
        "audio_signal": "unknown",
        "provider_state": "whisper-fallback",
    }
    path = tmp_path / "public" / "captions" / "runtime-status.json"
    path.parent.mkdir(parents=True)
    store = _FakeStore([_config("public")], {"public": _state("public", "ON_AIR")}, {})
    for retry_started in (payload["inference_started_at"], now.isoformat()):
        payload["inference_started_at"] = retry_started
        path.write_text(json.dumps(payload), encoding="utf-8")
        status = derive_live_caption_processing_status(
            payload, egress_state="ON_AIR", captions_expected=True, now=now
        )
        assert status.processing_state == "stalled"
        assert (
            live_caption_readiness(store, captions_expected=True, work_dir=tmp_path, now=now)
            == "degraded"
        )


@pytest.mark.parametrize("recent_anchor", ["last_processed_at", "pending_since_at"])
def test_recent_completion_or_new_pending_work_keeps_processing_allowance(
    recent_anchor: str,
) -> None:
    payload = {
        "worker_heartbeat_at": _NOW.isoformat(),
        "last_input_at": _NOW.isoformat(),
        "last_processed_at": (_NOW - timedelta(seconds=600)).isoformat(),
        "pending_since_at": (_NOW - timedelta(seconds=600)).isoformat(),
        "inference_started_at": _NOW.isoformat(),
        "inference_inflight": True,
        "backlog_segments": 1,
    }
    payload[recent_anchor] = (_NOW - timedelta(seconds=20)).isoformat()
    assert (
        derive_live_caption_processing_status(
            payload, egress_state="ON_AIR", captions_expected=True, now=_NOW
        ).processing_state
        == "processing"
    )


def test_recently_completed_audio_is_caught_up_and_readiness_healthy(tmp_path: Path) -> None:
    """A real guest probe briefly reported degraded after Whistle caught up."""
    now = datetime.fromisoformat("2026-10-10T01:40:52.034328+00:00")
    snapshot = {
        "state": "within-capacity",
        "worker_heartbeat_at": "2026-10-10T01:40:52.034328+00:00",
        "updated_at": "2026-10-10T01:40:52.034328+00:00",
        "last_input_at": "2026-10-10T01:40:47.977404+00:00",
        "last_processed_at": "2026-10-10T01:40:50.275040+00:00",
        "audio_signal": "audio-present",
        "provider_state": "whistle-primary",
        "inference_inflight": False,
        "inference_started_at": None,
        "backlog_segments": 0,
        "pending_since_at": None,
        "max_backlog_segments": 2,
    }

    status = derive_live_caption_processing_status(
        snapshot,
        egress_state="ON_AIR",
        captions_expected=True,
        now=now,
        on_air_since=now - timedelta(seconds=30),
    )
    assert status.processing_state == "caught-up"

    status_path = tmp_path / "public" / "captions" / "runtime-status.json"
    status_path.parent.mkdir(parents=True)
    status_path.write_text(json.dumps(snapshot), encoding="utf-8")
    store = _FakeStore(
        [_config("public")],
        {
            "public": _state("public", "ON_AIR").model_copy(
                update={"updated_at": now - timedelta(seconds=30)}
            )
        },
        {},
    )
    assert (
        live_caption_readiness(
            store,
            captions_expected=True,
            work_dir=tmp_path,
            now=now,
        )
        == "healthy"
    )


@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        ({"last_input_at": None}, "waiting"),
        ({"last_processed_at": None}, "waiting"),
        (
            {
                "last_input_at": (_NOW - timedelta(seconds=130)).isoformat(),
                "last_processed_at": (_NOW - timedelta(seconds=121)).isoformat(),
            },
            "waiting",
        ),
        (
            {
                "last_input_at": (_NOW - timedelta(seconds=1)).isoformat(),
                "last_processed_at": (_NOW - timedelta(seconds=2)).isoformat(),
            },
            "waiting",
        ),
        ({"inference_inflight": True}, "processing"),
        ({"backlog_segments": 1}, "processing"),
        ({"state": "overloaded"}, "overloaded"),
        ({"state": "delayed"}, "delayed"),
    ],
    ids=[
        "no-input",
        "never-completed",
        "stale-completion",
        "newer-unprocessed-input",
        "inference-pending",
        "backlog-pending",
        "capacity-failure",
        "temporary-delay",
    ],
)
def test_caught_up_requires_recent_completed_audio_without_failure_or_pending_work(
    overrides: dict[str, object], expected: str
) -> None:
    payload: dict[str, object] = {
        "state": "within-capacity",
        "worker_heartbeat_at": _NOW.isoformat(),
        "last_input_at": (_NOW - timedelta(seconds=5)).isoformat(),
        "last_processed_at": (_NOW - timedelta(seconds=1)).isoformat(),
        "audio_signal": "audio-present",
        "inference_inflight": False,
        "backlog_segments": 0,
    }
    payload.update(overrides)

    status = derive_live_caption_processing_status(
        payload,
        egress_state="ON_AIR",
        captions_expected=True,
        now=_NOW,
    )

    assert status.processing_state == expected


def test_digital_silence_is_not_caption_failure_and_disabled_or_stopped_is_inactive() -> None:
    silent = derive_live_caption_processing_status(
        {
            "state": "within-capacity",
            "worker_heartbeat_at": _NOW.isoformat(),
            "last_input_at": (_NOW - timedelta(seconds=5)).isoformat(),
            "last_processed_at": (_NOW - timedelta(seconds=1)).isoformat(),
            "audio_signal": "digital-silence",
            "inference_inflight": False,
            "backlog_segments": 0,
        },
        egress_state="ON_AIR",
        captions_expected=True,
        now=_NOW,
    )
    assert silent.processing_state == "silent"
    assert silent.audio_signal == "digital-silence"
    status = compute_channel_runtime_status(
        _config(),
        _state("public", "ON_AIR"),
        _sample("public", "ON_AIR", caption_status="not-verified"),
        now=_NOW,
        live_caption_status=silent,
    )
    assert status.color == "green"

    stopped = derive_live_caption_processing_status(
        {"state": "storage-refused"},
        egress_state="STOPPED",
        captions_expected=True,
        now=_NOW,
    )
    disabled = derive_live_caption_processing_status(
        {"state": "storage-refused"},
        egress_state="ON_AIR",
        captions_expected=False,
        now=_NOW,
    )
    assert stopped.processing_state == "inactive"
    assert disabled.processing_state == "disabled"


def test_readiness_ignores_stopped_channels_and_reads_each_state_once(
    tmp_path: Path,
) -> None:
    class CountingStore(_FakeStore):
        def __init__(self):
            super().__init__(
                [_config("public", auto_start=False)],
                {"public": _state("public", "STOPPED")},
                {},
            )
            self.state_reads = 0

        def read_state(self, channel_id):
            self.state_reads += 1
            return super().read_state(channel_id)

    store = CountingStore()
    status_path = tmp_path / "public" / "captions" / "runtime-status.json"
    status_path.parent.mkdir(parents=True)
    status_path.write_text(json.dumps({"state": "storage-refused"}), encoding="utf-8")
    assert (
        live_caption_readiness(
            store,
            captions_expected=True,
            work_dir=tmp_path,
            now=_NOW,
        )
        == "idle"
    )
    assert store.state_reads == 1


def _config(
    channel_id: str = "public", *, auto_start: bool = True, enabled: bool = True
) -> EgressConfig:
    return EgressConfig(
        channel_id=channel_id,
        enabled=enabled,
        auto_start=auto_start,
        slate_message="CivicCast is preparing the channel.",
        sinks=[EgressSinkSpec(kind="udp-ts", label="Cable headend", uri="udp://239.255.0.1:5000")],
    )


def _state(
    channel_id: str, state: str, *, age_s: int = 30, proof: str | None = "pf-1"
) -> EgressStateRow:
    return EgressStateRow(
        channel_id=channel_id,
        state=state,  # type: ignore[arg-type]
        current_proof_event_id=proof,
        updated_at=_NOW - timedelta(seconds=age_s),
    )


def _sample(
    channel_id: str,
    state: str,
    *,
    sinks: dict[str, bool] | None = None,
    fps: float | None = 29.97,
    bitrate: float | None = 8000.0,
    loudness: float | None = -24.0,
    caption_status: str = "on",
) -> EgressHealthSample:
    return EgressHealthSample(
        channel_id=channel_id,
        sampled_at=_NOW,
        state=state,  # type: ignore[arg-type]
        sink_connected=sinks if sinks is not None else {"Cable headend": True},
        encoder_fps=fps,
        encoder_bitrate_kbps=bitrate,
        last_loudness_lufs=loudness,
        caption_status=caption_status,  # type: ignore[arg-type]
    )


def _alert(severity: str, condition: str = "off-air") -> AlertEvent:
    return AlertEvent(
        event_id=f"a-{condition}-{severity}",
        rule_id=f"default:{condition}",
        condition=condition,  # type: ignore[arg-type]
        severity=severity,  # type: ignore[arg-type]
        state="firing",
        resource_ref="public",
        summary="x",
        source_section="S8",
        first_observed_at=_NOW,
        last_observed_at=_NOW,
    )


class _FakeStore:
    def __init__(self, configs, states, samples):
        self._configs = configs
        self._states = states
        self._samples = samples

    def list_configs(self):
        return self._configs

    def read_state(self, channel_id):
        return self._states.get(channel_id)

    def recent_health(self, channel_id, limit):
        s = self._samples.get(channel_id)
        return [s] if s is not None else []


# ---------------------------------------------------------------------------
# Per-channel status
# ---------------------------------------------------------------------------


class TestChannelRuntimeStatus:
    def test_on_air_all_sinks_healthy_is_green(self) -> None:
        c = compute_channel_runtime_status(
            _config(), _state("public", "ON_AIR"), _sample("public", "ON_AIR"), now=_NOW
        )
        assert c.color == "green"
        assert c.on_air is True
        assert c.on_healthy_slate is False
        assert c.seconds_in_state == 30
        assert c.last_proof_event_id == "pf-1"

    def test_on_air_with_sink_down_is_yellow(self) -> None:
        c = compute_channel_runtime_status(
            _config(),
            _state("public", "ON_AIR"),
            _sample("public", "ON_AIR", sinks={"Cable headend": False}),
            now=_NOW,
        )
        assert c.color == "yellow"
        assert c.on_air is True

    def test_on_air_loudness_out_of_tolerance_is_yellow(self) -> None:
        c = compute_channel_runtime_status(
            _config(),
            _state("public", "ON_AIR"),
            _sample("public", "ON_AIR", loudness=-18.0),  # +6 LU over target
            now=_NOW,
        )
        assert c.color == "yellow"

    def test_stopped_is_red(self) -> None:
        c = compute_channel_runtime_status(_config(), _state("public", "STOPPED"), None, now=_NOW)
        assert c.color == "red"
        assert c.on_air is False

    def test_error_is_red(self) -> None:
        c = compute_channel_runtime_status(_config(), _state("public", "ERROR"), None, now=_NOW)
        assert c.color == "red"

    def test_healthy_slate_is_green_and_flagged(self) -> None:
        c = compute_channel_runtime_status(
            _config(),
            _state("public", "FALLBACK_SLATE"),
            _sample("public", "FALLBACK_SLATE", fps=0.0, bitrate=0.0),
            now=_NOW,
        )
        assert c.color == "green"
        assert c.on_healthy_slate is True
        assert c.on_air is False

    def test_slate_with_sink_down_is_yellow_not_healthy_slate(self) -> None:
        c = compute_channel_runtime_status(
            _config(),
            _state("public", "FALLBACK_SLATE"),
            _sample("public", "FALLBACK_SLATE", sinks={"Cable headend": False}),
            now=_NOW,
        )
        assert c.color == "yellow"
        assert c.on_healthy_slate is False

    def test_transient_states_are_yellow(self) -> None:
        for state in ("STARTING", "TRANSITIONING"):
            c = compute_channel_runtime_status(
                _config(), _state("public", state), _sample("public", state), now=_NOW
            )
            assert c.color == "yellow", state

    def test_missing_state_treated_as_dark_red(self) -> None:
        c = compute_channel_runtime_status(_config(), None, None, now=_NOW)
        assert c.color == "red"
        assert c.egress_state == "STOPPED"
        assert c.seconds_in_state == 0

    @pytest.mark.parametrize(
        "caption_status",
        ["not-verified", "failed", "expired", "overloaded"],
    )
    def test_on_air_without_verified_captions_is_red(
        self,
        caption_status: str,
    ) -> None:
        sample = _sample("public", "ON_AIR").model_copy(update={"caption_status": caption_status})

        c = compute_channel_runtime_status(
            _config(),
            _state("public", "ON_AIR"),
            sample,
            now=_NOW,
        )

        assert c.color == "red"

    def test_on_air_without_a_health_sample_is_red(self) -> None:
        c = compute_channel_runtime_status(
            _config(),
            _state("public", "ON_AIR"),
            None,
            now=_NOW,
        )

        assert c.color == "red"


# ---------------------------------------------------------------------------
# Overall safe-to-air
# ---------------------------------------------------------------------------


class TestRuntimeSafeToAir:
    def _store(self, specs):
        # specs: list of (channel_id, state, sinks, auto_start)
        configs, states, samples = [], {}, {}
        for cid, state, sinks, auto in specs:
            configs.append(_config(cid, auto_start=auto))
            states[cid] = _state(cid, state)
            samples[cid] = _sample(cid, state, sinks=sinks)
        return _FakeStore(configs, states, samples)

    def test_all_green_no_alerts(self) -> None:
        store = self._store([("public", "ON_AIR", {"Cable headend": True}, True)])
        r = compute_runtime_safe_to_air(store, [], now=_NOW)
        assert r.color == "green"
        assert r.label == "On air"
        assert len(r.channels) == 1

    def test_off_air_channel_is_red_and_named(self) -> None:
        store = self._store([("gov-ch12", "STOPPED", None, True)])
        r = compute_runtime_safe_to_air(store, [], now=_NOW)
        assert r.color == "red"
        assert r.label == "OFF AIR"
        assert "gov-ch12" in r.operator_message

    def test_critical_alert_escalates_green_to_red(self) -> None:
        store = self._store([("public", "ON_AIR", {"Cable headend": True}, True)])
        r = compute_runtime_safe_to_air(store, [_alert("critical")], now=_NOW)
        assert r.color == "red"
        assert r.active_critical_alerts == 1

    def test_warning_alert_with_degraded_channel_is_yellow(self) -> None:
        store = self._store([("public", "ON_AIR", {"Cable headend": False}, True)])
        r = compute_runtime_safe_to_air(store, [_alert("warning", "encoder-death")], now=_NOW)
        assert r.color == "yellow"
        assert r.active_warning_alerts == 1
        assert "warning" in r.operator_message

    def test_non_auto_start_channels_excluded(self) -> None:
        store = self._store(
            [
                ("public", "ON_AIR", {"Cable headend": True}, True),
                ("adhoc", "STOPPED", None, False),  # not auto_start -> excluded
            ]
        )
        r = compute_runtime_safe_to_air(store, [], now=_NOW)
        assert [c.channel_id for c in r.channels] == ["public"]
        assert r.color == "green"

    def test_no_auto_start_channels_is_idle_green(self) -> None:
        store = self._store([("adhoc", "STOPPED", None, False)])
        r = compute_runtime_safe_to_air(store, [], now=_NOW)
        assert r.color == "green"
        assert r.label == "Idle"
        assert r.channels == []

    def test_worst_channel_color_wins(self) -> None:
        store = self._store(
            [
                ("public", "ON_AIR", {"Cable headend": True}, True),  # green
                ("edu", "ON_AIR", {"Cable headend": False}, True),  # yellow
            ]
        )
        r = compute_runtime_safe_to_air(store, [], now=_NOW)
        assert r.color == "yellow"


# ---------------------------------------------------------------------------
# Live captions switched OFF by the operator (beta.5 default)
# ---------------------------------------------------------------------------


class TestCaptionGateFollowsTheOperatorSwitch:
    """Round-2 review BLOCKER 1: with live captions off, no embed leg is built
    and the tap blanks every sidecar, so the decode-back proof can never PASS.
    The caption readiness gate must therefore follow the operator's switch --
    otherwise every auto_start channel's banner is pinned red forever."""

    def test_captions_off_with_healthy_sinks_is_green(self) -> None:
        c = compute_channel_runtime_status(
            _config(),
            _state("public", "ON_AIR"),
            _sample("public", "ON_AIR", caption_status="not-verified"),
            now=_NOW,
            expect_captions=False,
        )
        assert c.color == "green"
        assert c.captions_expected is False
        assert c.captions_verified is False

    def test_captions_off_still_reports_sink_and_loudness_degradation(self) -> None:
        c = compute_channel_runtime_status(
            _config(),
            _state("public", "ON_AIR"),
            _sample(
                "public", "ON_AIR", sinks={"Cable headend": False}, caption_status="not-verified"
            ),
            now=_NOW,
            expect_captions=False,
        )
        assert c.color == "yellow"

    def test_captions_off_healthy_slate_is_green_and_flagged(self) -> None:
        c = compute_channel_runtime_status(
            _config(),
            _state("public", "FALLBACK_SLATE"),
            _sample(
                "public", "FALLBACK_SLATE", fps=0.0, bitrate=0.0, caption_status="not-verified"
            ),
            now=_NOW,
            expect_captions=False,
        )
        assert c.color == "green"
        assert c.on_healthy_slate is True

    def test_captions_on_and_unverified_is_still_red(self) -> None:
        c = compute_channel_runtime_status(
            _config(),
            _state("public", "ON_AIR"),
            _sample("public", "ON_AIR", caption_status="not-verified"),
            now=_NOW,
            expect_captions=True,
        )
        assert c.color == "red"
        assert c.captions_expected is True

    def test_captions_on_and_verified_reports_verified(self) -> None:
        c = compute_channel_runtime_status(
            _config(),
            _state("public", "ON_AIR"),
            _sample("public", "ON_AIR", caption_status="on"),
            now=_NOW,
            expect_captions=True,
        )
        assert c.color == "green"
        assert c.captions_verified is True

    def _unverified_store(self) -> _FakeStore:
        return _FakeStore(
            [_config("public"), _config("edu")],
            {"public": _state("public", "ON_AIR"), "edu": _state("edu", "ON_AIR")},
            {
                "public": _sample("public", "ON_AIR", caption_status="not-verified"),
                "edu": _sample("edu", "ON_AIR", caption_status="not-verified"),
            },
        )

    def test_safe_to_air_reads_the_operator_switch_once(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        reads: list[int] = []

        def _resolver() -> bool:
            reads.append(1)
            return False

        monkeypatch.setattr(
            "civiccast.installer.station_state.resolve_live_captions_enabled", _resolver
        )
        r = compute_runtime_safe_to_air(self._unverified_store(), [], now=_NOW)
        assert r.color == "green"
        assert len(reads) == 1  # once per computation, not once per channel
        assert all(c.captions_expected is False for c in r.channels)

    def test_safe_to_air_stays_red_when_captions_are_on_and_unverified(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "civiccast.installer.station_state.resolve_live_captions_enabled", lambda: True
        )
        r = compute_runtime_safe_to_air(self._unverified_store(), [], now=_NOW)
        assert r.color == "red"
        assert all(c.captions_expected is True for c in r.channels)

    def test_explicit_expect_captions_overrides_the_resolver(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "civiccast.installer.station_state.resolve_live_captions_enabled", lambda: True
        )
        r = compute_runtime_safe_to_air(
            self._unverified_store(), [], now=_NOW, expect_captions=False
        )
        assert r.color == "green"

    def test_an_unreadable_switch_never_takes_the_banner_down(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from civiccast.installer.models import LIVE_CAPTIONS_DEFAULT

        def _boom() -> bool:
            raise PermissionError("station-state.json is locked")

        monkeypatch.setattr(
            "civiccast.installer.station_state.resolve_live_captions_enabled", _boom
        )
        r = compute_runtime_safe_to_air(self._unverified_store(), [], now=_NOW)
        assert all(c.captions_expected is LIVE_CAPTIONS_DEFAULT for c in r.channels)


def test_status_read_survives_a_transient_windows_replace_race(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """RC4 soak, r7 2026-11-09: reading runtime-status.json while the worker's
    atomic replace lands raised ``PermissionError`` (Errno 13) about once per
    5,000 reads, and one failed read made ``/health`` report degraded. The writer
    already retries its replace; the reader must retry its read too.
    """
    from civiccast.alerting import runtime_status

    status_path = tmp_path / "public" / "captions" / "runtime-status.json"
    status_path.parent.mkdir(parents=True)
    status_path.write_text(
        json.dumps(
            {
                "state": "within-capacity",
                "worker_heartbeat_at": _NOW.isoformat(),
                "last_input_at": (_NOW - timedelta(seconds=3)).isoformat(),
                "last_processed_at": (_NOW - timedelta(seconds=2)).isoformat(),
                "audio_signal": "audio-present",
                "inference_inflight": False,
                "backlog_segments": 0,
            }
        ),
        encoding="utf-8",
    )
    original = Path.read_text
    failures = {"left": 2}

    def flaky_read_text(self: Path, *args: object, **kwargs: object) -> str:
        if self == status_path and failures["left"] > 0:
            failures["left"] -= 1
            raise PermissionError(13, "Permission denied", str(self))
        return original(self, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(Path, "read_text", flaky_read_text)

    status = runtime_status.read_live_caption_processing_status(
        tmp_path,
        "public",
        egress_state="ON_AIR",
        captions_expected=True,
        now=_NOW,
        on_air_since=_NOW - timedelta(hours=1),
    )
    assert failures["left"] == 0
    assert status.processing_state == "caught-up"
