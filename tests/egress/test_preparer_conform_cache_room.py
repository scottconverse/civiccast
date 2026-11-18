# SPDX-License-Identifier: Apache-2.0
# Copyright (c) The CivicCast Authors
"""The conform cache must stay inside the disk it shares.

Live evidence (2026-11-17):

* R9 ``government`` was pinned on slate with ``[Errno 28] No space left on
  device`` in the preparer log from 19:12 to 19:33. Its conform cache was
  26.5 GB inside a 20.1 GB budget on a volume with 6.0 GB free -- the budget
  was compared against the CONFIGURED number only, never against the disk, so
  "under the cap" meant nothing.
* MSI ``government`` runs a 5.3-hour program against a 0.6 GB cache and
  3.2 GB of free disk; every rollover conforms cold and the channel sits on
  slate for ~25 minutes.

What these tests pin:

* ``_cache_budget_bytes`` is the SMALLER of ``CIVICCAST_CONFORM_CACHE_GB`` and
  (free disk on the cache volume minus the safety reserve), and the env var
  still works exactly as before.
* A conform whose output cannot fit evicts by the existing oldest-first rule
  FIRST, and only gives up when the disk still cannot hold it.
* Giving up is a single clear warning and a "did not populate the cache"
  return -- never an exception on the air path, and never a second encode.

The disk is faked: no station is touched and no multi-gigabyte file is
written. ``_conform_output_bytes_estimate`` is pinned small in the eviction
tests so the arithmetic is readable; the estimate itself is checked against
the canonical profile's own bitrates.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

import civiccast.egress.preparer as preparer_module
from civiccast.egress.models import (
    CanonicalProfile,
    EgressConfig,
    EgressSourceSegment,
)
from civiccast.egress.preparer import SourcePreparer
from tests.egress import test_preparer as PreparerTests


class _Usage:
    """``shutil.disk_usage``'s shape, with only ``free`` meaningful here."""

    def __init__(self, free: float) -> None:
        self.total = 0
        self.used = 0
        self.free = free


def _config() -> EgressConfig:
    return PreparerTests._config()


def _preparer(tmp_path: Path, *, calls: list[list[str]] | None = None) -> SourcePreparer:
    calls = [] if calls is None else calls
    return SourcePreparer(
        work_dir=tmp_path / "work",
        ffmpeg_runner=PreparerTests._counting_runner(calls),
        loudness_checker=lambda **_kwargs: PreparerTests._loudness(),
    )


def _fake_disk(
    monkeypatch: pytest.MonkeyPatch, cache_dir: Path, *, base_free: float
) -> dict[str, float]:
    """Free space that grows as cache entries are deleted, as a real disk does.

    ``base_free`` is the free space with the cache directory exactly as it is
    when this helper is called; every later deletion adds its bytes back.
    """

    cache_dir.mkdir(parents=True, exist_ok=True)
    start = sum(p.stat().st_size for p in cache_dir.iterdir() if p.is_file())

    def usage(_path: object) -> _Usage:
        current = (
            sum(p.stat().st_size for p in cache_dir.iterdir() if p.is_file())
            if cache_dir.exists()
            else 0
        )
        return _Usage(base_free + (start - current))

    monkeypatch.setattr(preparer_module.shutil, "disk_usage", usage)
    return {"free": base_free, "initial": start}


def _cached_entry(preparer: SourcePreparer, name: str, size: int) -> Path:
    """A cache entry in the shape ``_evict_cache_over_budget`` recognises."""

    cache_dir = preparer._cache_dir()
    cache_dir.mkdir(parents=True, exist_ok=True)
    entry = cache_dir / f"{name:0<32}.ts"
    entry.write_bytes(b"x" * size)
    entry.with_suffix(".json").write_text("{}", encoding="utf-8")
    return entry


# ---------------------------------------------------------------------------
# The budget is the disk's, not only the registry's
# ---------------------------------------------------------------------------


def test_the_cache_budget_is_capped_by_free_disk_minus_the_reserve(tmp_path, monkeypatch):
    cache_dir = tmp_path / "conform-cache"
    cache_dir.mkdir()
    monkeypatch.delenv("CIVICCAST_CONFORM_CACHE_GB", raising=False)
    monkeypatch.delenv("CIVICCAST_CONFORM_CACHE_RESERVE_GB", raising=False)
    monkeypatch.setattr(preparer_module.shutil, "disk_usage", lambda _p: _Usage(3.2e9))

    budget = preparer_module._cache_budget_bytes(cache_dir)

    assert budget == pytest.approx(3.2e9 - preparer_module._DEFAULT_CACHE_DISK_RESERVE_GB * 1e9)
    assert budget < preparer_module._DEFAULT_CACHE_GB * 1e9


def test_a_roomy_disk_leaves_the_configured_budget_alone(tmp_path, monkeypatch):
    cache_dir = tmp_path / "conform-cache"
    cache_dir.mkdir()
    monkeypatch.delenv("CIVICCAST_CONFORM_CACHE_GB", raising=False)
    monkeypatch.setattr(preparer_module.shutil, "disk_usage", lambda _p: _Usage(900e9))

    assert preparer_module._cache_budget_bytes(cache_dir) == preparer_module._DEFAULT_CACHE_GB * 1e9


def test_the_configured_budget_still_wins_when_it_is_the_smaller_one(tmp_path, monkeypatch):
    cache_dir = tmp_path / "conform-cache"
    cache_dir.mkdir()
    monkeypatch.setenv("CIVICCAST_CONFORM_CACHE_GB", "0.6")
    monkeypatch.setattr(preparer_module.shutil, "disk_usage", lambda _p: _Usage(900e9))

    assert preparer_module._cache_budget_bytes(cache_dir) == pytest.approx(0.6e9)


def test_the_env_var_still_caps_a_roomy_disk_and_disables_caching_at_zero(tmp_path, monkeypatch):
    cache_dir = tmp_path / "conform-cache"
    cache_dir.mkdir()
    monkeypatch.setattr(preparer_module.shutil, "disk_usage", lambda _p: _Usage(900e9))

    monkeypatch.setenv("CIVICCAST_CONFORM_CACHE_GB", "5")
    assert preparer_module._cache_budget_bytes(cache_dir) == pytest.approx(5e9)

    monkeypatch.setenv("CIVICCAST_CONFORM_CACHE_GB", "0")
    assert preparer_module._cache_budget_bytes(cache_dir) == 0


def test_an_unreadable_volume_keeps_the_configured_budget(tmp_path, monkeypatch):
    """Fail open: a disk probe that raises must not disable the cache."""

    cache_dir = tmp_path / "conform-cache"
    cache_dir.mkdir()
    monkeypatch.delenv("CIVICCAST_CONFORM_CACHE_GB", raising=False)

    def boom(_path: object) -> _Usage:
        raise OSError("volume unavailable")

    monkeypatch.setattr(preparer_module.shutil, "disk_usage", boom)

    assert preparer_module._cache_budget_bytes(cache_dir) == preparer_module._DEFAULT_CACHE_GB * 1e9


# ---------------------------------------------------------------------------
# The estimate is the canonical profile's own bitrates
# ---------------------------------------------------------------------------


def test_the_output_estimate_follows_the_canonical_profile_bitrates() -> None:
    profile = CanonicalProfile(video_bitrate_kbps=6000, audio_bitrate_kbps=192)

    estimate = preparer_module._conform_output_bytes_estimate(profile, 3600.0)

    # (6000 + 192) kbit/s over one hour, plus the headroom factor.
    assert estimate == pytest.approx(
        3600.0 * 6192 * 1000 / 8 * preparer_module._CONFORM_OUTPUT_MARGIN
    )


# ---------------------------------------------------------------------------
# Evict first; give up only when the disk still cannot hold the output
# ---------------------------------------------------------------------------


def test_eviction_frees_space_for_the_next_conform(tmp_path, monkeypatch):
    preparer = _preparer(tmp_path)
    monkeypatch.setenv("CIVICCAST_CONFORM_CACHE_GB", "1000")
    monkeypatch.setenv("CIVICCAST_CONFORM_CACHE_RESERVE_GB", "0")
    monkeypatch.setattr(preparer_module, "_conform_output_bytes_estimate", lambda _p, _s: 500.0)
    oldest = _cached_entry(preparer, "a" * 32, 300)
    newer = _cached_entry(preparer, "b" * 32, 300)
    _fake_disk(monkeypatch, preparer._cache_dir(), base_free=100.0)

    fits = preparer._ensure_room_for_conform(estimated_bytes=500.0, description="a 3600s asset")

    assert fits is True
    assert not oldest.exists()  # oldest-first, exactly as the budget pass does
    assert not newer.exists()  # both had to go: 100 + 300 was still short of 500


def test_no_room_keeps_the_current_air_and_returns_without_writing(tmp_path, monkeypatch, caplog):
    """The live R9 shape: the output cannot fit however much is evicted."""

    calls: list[list[str]] = []
    preparer = _preparer(tmp_path, calls=calls)
    monkeypatch.setenv("CIVICCAST_CONFORM_CACHE_GB", "1000")
    monkeypatch.setenv("CIVICCAST_CONFORM_CACHE_RESERVE_GB", "0")
    monkeypatch.setattr(preparer_module, "_conform_output_bytes_estimate", lambda _p, _s: 5_000.0)
    _cached_entry(preparer, "c" * 32, 300)
    _fake_disk(monkeypatch, preparer._cache_dir(), base_free=10.0)
    source = tmp_path / "long-recording.mp4"
    source.write_text("fake long media", encoding="utf-8")
    config = _config()
    key = preparer._cache_key(source, config)
    assert key is not None

    with caplog.at_level(logging.WARNING):
        populated = preparer._conform_full_asset_into_cache(
            key,
            source,
            config,
            PreparerTests._loudness(),
            normalized=False,
            media_duration_seconds=3600.0,
        )

    assert populated is None  # the documented "did not populate" signal
    assert calls == []  # nothing was encoded, so nothing hit ENOSPC
    warnings = [record for record in caplog.records if record.levelno >= logging.WARNING]
    assert len(warnings) == 1, [record.getMessage() for record in warnings]
    message = warnings[0].getMessage()
    assert "disk" in message.lower() or "space" in message.lower()
    assert "36" in message or "cache" in message.lower()


def test_the_no_room_warning_is_logged_once_per_asset(tmp_path, monkeypatch, caplog):
    preparer = _preparer(tmp_path)
    monkeypatch.setenv("CIVICCAST_CONFORM_CACHE_GB", "1000")
    monkeypatch.setenv("CIVICCAST_CONFORM_CACHE_RESERVE_GB", "0")
    monkeypatch.setattr(preparer_module, "_conform_output_bytes_estimate", lambda _p, _s: 5_000.0)
    _fake_disk(monkeypatch, preparer._cache_dir(), base_free=10.0)
    source = tmp_path / "long-recording.mp4"
    source.write_text("fake long media", encoding="utf-8")
    config = _config()
    key = preparer._cache_key(source, config)
    assert key is not None

    with caplog.at_level(logging.WARNING):
        for _ in range(3):
            assert (
                preparer._conform_full_asset_into_cache(
                    key,
                    source,
                    config,
                    PreparerTests._loudness(),
                    normalized=False,
                    media_duration_seconds=3600.0,
                )
                is None
            )

    warnings = [record for record in caplog.records if record.levelno >= logging.WARNING]
    assert len(warnings) == 1, [record.getMessage() for record in warnings]


def test_a_conform_that_fits_is_not_delayed_and_does_not_evict(tmp_path, monkeypatch):
    calls: list[list[str]] = []
    preparer = _preparer(tmp_path, calls=calls)
    monkeypatch.setenv("CIVICCAST_CONFORM_CACHE_GB", "1000")
    monkeypatch.setenv("CIVICCAST_CONFORM_CACHE_RESERVE_GB", "0")
    monkeypatch.setattr(preparer_module, "_conform_output_bytes_estimate", lambda _p, _s: 500.0)
    survivor = _cached_entry(preparer, "d" * 32, 100)
    _fake_disk(monkeypatch, preparer._cache_dir(), base_free=10_000.0)
    source = tmp_path / "long-recording.mp4"
    source.write_text("fake long media", encoding="utf-8")
    config = _config()
    key = preparer._cache_key(source, config)
    assert key is not None

    populated = preparer._conform_full_asset_into_cache(
        key,
        source,
        config,
        PreparerTests._loudness(),
        normalized=False,
        media_duration_seconds=3600.0,
    )

    assert populated is not None
    assert survivor.exists()
    assert calls  # the conform actually ran


def test_a_segment_that_a_whole_program_would_not_fit_does_not_starve_the_air_path(
    tmp_path, monkeypatch
) -> None:
    """The MSI shape: the cache cannot hold the program, so nothing is cached.

    The air path must still prepare: it falls through to the bounded
    per-segment conform exactly as it does for a held conform lock. No new
    exception reaches the daemon, so no new route to the slate is opened.
    """

    calls: list[list[str]] = []
    preparer = SourcePreparer(
        work_dir=tmp_path / "work",
        ffmpeg_runner=PreparerTests._counting_runner(calls),
        loudness_checker=lambda **_kwargs: PreparerTests._loudness(),
        playout_trim_supported=True,
    )
    monkeypatch.setattr(preparer_module, "probe_media_duration_seconds", lambda *_a, **_k: 3600.0)
    monkeypatch.setattr(preparer_module, "_probe_prepared_segment_decodability", lambda _p: True)
    monkeypatch.setattr(preparer_module, "_conform_output_bytes_estimate", lambda _p, _s: 5_000.0)
    monkeypatch.setenv("CIVICCAST_CONFORM_CACHE_GB", "1000")
    monkeypatch.setenv("CIVICCAST_CONFORM_CACHE_RESERVE_GB", "0")
    _fake_disk(monkeypatch, preparer._cache_dir(), base_free=10.0)

    from civiccast.egress.models import EgressSourcePlan

    source = tmp_path / "long-recording.mp4"
    source.write_text("fake long media", encoding="utf-8")
    plan = EgressSourcePlan(
        channel_id="gov",
        segments=[
            EgressSourceSegment(
                label="Council meeting",
                path=str(source),
                duration_seconds=3600.0,
                kind="program",
                source_ref="gov-1",
            )
        ],
    )

    report = preparer.prepare(plan, _config())

    assert report.source_plan.segments  # the air path still has a segment to air
    assert Path(report.source_plan.segments[0].path).is_file()
