# SPDX-License-Identifier: Apache-2.0
# Copyright (c) The CivicCast Authors
"""S13 AiModelService — assembles a FeatureModelRegistry from catalog + stored selection.

The service is the seam between the hard-coded catalog (decision A), the durable
operator selection (AiModelStore), and the adaptive local default. It validates a
selection against the catalog (unknown -> error), records the computed tier band, and
exposes the effective model key the runtime adapters consume.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from civiccast.ai_models.service import (
    AiModelService,
    UnknownFeatureError,
    UnknownModelError,
)
from civiccast.ai_models.store import AiModelStore
from civiccast.db import Base


@pytest.fixture
def service(tmp_path: Path) -> Iterator[AiModelService]:
    eng = create_engine(f"sqlite:///{tmp_path / 'svc.sqlite'}", future=True)
    Base.metadata.create_all(eng)

    @contextmanager
    def factory() -> Iterator[Session]:
        with Session(bind=eng) as session:
            yield session

    try:
        # No-op first-run reader so the fixture is hermetic (never reads ambient
        # station-state); the override-fallback path has its own dedicated tests.
        # has_gpu=True: this fixture models a box that qualifies for the 12B
        # adaptive default (16GB RAM + a real GPU). The CPU-only-box case
        # (has_gpu defaults False -> e4b regardless of RAM) has its own
        # dedicated tests in test_model_selection.py / test_catalog.py.
        yield AiModelService(
            AiModelStore(factory),
            system_ram_total_gb=16,
            has_gpu=True,
            read_first_run_override=lambda _feature: None,
        )
    finally:
        eng.dispose()


# --- registry assembly -------------------------------------------------------


def test_registry_defaults_to_local_when_nothing_selected(service: AiModelService) -> None:
    reg = service.get_registry("summary")
    # 16GB box -> adaptive 12B local default, no operator selection yet.
    assert reg.operator_selected_key is None
    assert reg.default_key == "gemma4-12b-ollama"
    assert reg.effective_model_key == "gemma4-12b-ollama"
    assert reg.adaptive_default is True


def test_registry_carries_every_catalog_tier(service: AiModelService) -> None:
    reg = service.get_registry("summary")
    keys = {t.key for t in reg.available_tiers}
    assert {"gemma4-12b-ollama", "gemma4-e4b-ollama", "gemma4-31b-cloud"} <= keys


def _service_with_seed(
    tmp_path: Path,
    seed: dict[str, str | None],
    *,
    writable: bool = True,
) -> AiModelService:
    """A service backed by an in-memory first-run ``seed`` dict (reader + writer)."""
    eng = create_engine(f"sqlite:///{tmp_path / 'ovr.sqlite'}", future=True)
    Base.metadata.create_all(eng)

    @contextmanager
    def factory() -> Iterator[Session]:
        with Session(bind=eng) as session:
            yield session

    def _write(feature: str, key: str | None) -> None:
        if not writable:
            raise ValueError("AI model default is not seeded yet; run commissioning first.")
        seed[feature] = key

    return AiModelService(
        AiModelStore(factory),
        system_ram_total_gb=16,
        # has_gpu=True: these tests model a box that qualifies for the 12B
        # adaptive default (see the `service` fixture above for why).
        has_gpu=True,
        read_first_run_override=lambda feature: seed.get(feature),
        write_first_run_override=_write,
    )


def _service_with_override(tmp_path: Path, override_for: dict[str, str | None]) -> AiModelService:
    """A read-only-seed service (writes are not exercised by the caller)."""
    return _service_with_seed(tmp_path, dict(override_for))


def test_first_run_override_is_honored_before_any_db_selection(tmp_path: Path) -> None:
    # §5.3/§6.1: the commissioning wizard override is the effective key before any
    # durable /select — the override slot is read by runtime, not dead.
    svc = _service_with_override(tmp_path, {"summary": "gemma4-e4b-ollama"})
    reg = svc.get_registry("summary")
    assert reg.operator_selected_key == "gemma4-e4b-ollama"
    assert reg.effective_model_key == "gemma4-e4b-ollama"
    # And the runtime tag the adapters consume reflects the override.
    assert svc.effective_model_tag("summary") == "gemma4:e4b"


def test_db_selection_takes_precedence_over_first_run_override(tmp_path: Path) -> None:
    svc = _service_with_override(tmp_path, {"summary": "gemma4-e4b-ollama"})
    svc.select_model("summary", "gemma4-12b-ollama")
    # A durable selection wins over the first-run seed override.
    assert svc.get_registry("summary").effective_model_key == "gemma4-12b-ollama"


def test_invalid_first_run_override_is_ignored(tmp_path: Path) -> None:
    # A stale/unknown seed value can never force an unknown model onto the runtime;
    # the registry falls back to the adaptive default.
    svc = _service_with_override(tmp_path, {"summary": "no-such-model"})
    reg = svc.get_registry("summary")
    assert reg.operator_selected_key is None
    assert reg.effective_model_key == "gemma4-12b-ollama"


def test_no_first_run_override_uses_adaptive_default(tmp_path: Path) -> None:
    svc = _service_with_override(tmp_path, {"summary": None})
    assert svc.get_registry("summary").effective_model_key == "gemma4-12b-ollama"


def test_set_first_run_override_writes_and_becomes_effective(tmp_path: Path) -> None:
    seed: dict[str, str | None] = {"summary": None}
    svc = _service_with_seed(tmp_path, seed)
    reg = svc.set_first_run_override("summary", "gemma4-e4b-ollama")
    assert seed["summary"] == "gemma4-e4b-ollama"
    assert reg.effective_model_key == "gemma4-e4b-ollama"


def test_set_first_run_override_rejects_hosted_tier(tmp_path: Path) -> None:
    from civiccast.ai_models.service import ConsentRequiredError

    seed: dict[str, str | None] = {"summary": None}
    svc = _service_with_seed(tmp_path, seed)
    with pytest.raises(ConsentRequiredError):
        svc.set_first_run_override("summary", "gemma4-31b-cloud")
    assert seed["summary"] is None  # nothing written


def test_set_first_run_override_rejects_unknown_model(tmp_path: Path) -> None:
    seed: dict[str, str | None] = {"summary": None}
    svc = _service_with_seed(tmp_path, seed)
    with pytest.raises(UnknownModelError):
        svc.set_first_run_override("summary", "no-such-model")
    assert seed["summary"] is None


def test_set_first_run_override_before_seed_raises_not_seeded(tmp_path: Path) -> None:
    from civiccast.ai_models.service import FirstRunNotSeededError

    svc = _service_with_seed(tmp_path, {"summary": None}, writable=False)
    with pytest.raises(FirstRunNotSeededError):
        svc.set_first_run_override("summary", "gemma4-e4b-ollama")


def test_registry_e4b_default_on_8gb_box(tmp_path: Path) -> None:
    eng = create_engine(f"sqlite:///{tmp_path / 's.sqlite'}", future=True)
    Base.metadata.create_all(eng)

    @contextmanager
    def factory() -> Iterator[Session]:
        with Session(bind=eng) as session:
            yield session

    svc = AiModelService(
        AiModelStore(factory),
        system_ram_total_gb=8,
        read_first_run_override=lambda _feature: None,
    )
    assert svc.get_registry("summary").default_key == "gemma4-e4b-ollama"
    eng.dispose()


# --- selection ---------------------------------------------------------------


def test_select_local_model_persists_and_becomes_effective(service: AiModelService) -> None:
    reg = service.select_model("summary", "gemma4-e4b-ollama")
    assert reg.operator_selected_key == "gemma4-e4b-ollama"
    assert reg.effective_model_key == "gemma4-e4b-ollama"
    # Survives a fresh read.
    assert service.get_registry("summary").operator_selected_key == "gemma4-e4b-ollama"


def test_select_cloud_model_is_functional(service: AiModelService) -> None:
    reg = service.select_model("summary", "gemma4-31b-cloud", consent_accepted=True)
    assert reg.effective_model_key == "gemma4-31b-cloud"
    tier = next(t for t in reg.available_tiers if t.key == "gemma4-31b-cloud")
    assert tier.requires_network is True
    assert tier.cost_per_token_usd > 0.0


def test_select_cloud_model_requires_consent(service: AiModelService) -> None:
    # A billable, content-egressing tier must not persist without recorded TOS consent.
    from civiccast.ai_models.service import ConsentRequiredError

    with pytest.raises(ConsentRequiredError):
        service.select_model("summary", "gemma4-31b-cloud")
    # Nothing was persisted; the effective model stays the local default.
    assert service.get_registry("summary").operator_selected_key is None


def test_consent_is_persisted_on_a_cloud_selection(service: AiModelService) -> None:
    service.select_model("summary", "gemma4-31b-cloud", consent_accepted=True, consent_actor="dana")
    row = service._store.get_selection_row("summary")
    assert row is not None
    assert row.consent_accepted is True
    assert row.consent_actor == "dana"
    assert row.consent_at is not None
    # The dispatch seam reads the persisted consent.
    sel = service.effective_selection("summary")
    assert sel.consent_accepted is True


def test_local_selection_records_no_consent(service: AiModelService) -> None:
    service.select_model("summary", "gemma4-e4b-ollama")
    row = service._store.get_selection_row("summary")
    assert row is not None
    assert row.consent_accepted is False
    assert row.consent_actor is None
    assert row.consent_at is None


def test_select_unknown_model_raises(service: AiModelService) -> None:
    with pytest.raises(UnknownModelError):
        service.select_model("summary", "no-such-model")


def test_select_model_not_offered_for_feature_raises(service: AiModelService) -> None:
    # whisper is a captions-only tier; it is not offered for summary.
    with pytest.raises(UnknownModelError):
        service.select_model("summary", "whisper-large-v3-faster")


def test_select_unknown_feature_raises(service: AiModelService) -> None:
    with pytest.raises(UnknownFeatureError):
        service.select_model("invalid", "gemma4-12b-ollama")


def test_get_unknown_feature_raises(service: AiModelService) -> None:
    with pytest.raises(UnknownFeatureError):
        service.get_registry("invalid")


def test_select_records_computed_tier_band(service: AiModelService) -> None:
    service.select_model("summary", "gemma4-31b-cloud", consent_accepted=True)
    rows = {r.feature: r.tier for r in service._store.list_selections()}
    assert rows["summary"] == "cloud"
    service.select_model("translation", "translategemma-4b-ollama")
    rows = {r.feature: r.tier for r in service._store.list_selections()}
    assert rows["translation"] == "local"


def test_frontier_provider_records_frontier_tier(service: AiModelService) -> None:
    service.select_model("summary", "gemini-2.5-flash-openrouter", consent_accepted=True)
    rows = {r.feature: r.tier for r in service._store.list_selections()}
    assert rows["summary"] == "frontier"


# --- whole configuration -----------------------------------------------------


def test_get_configuration_lists_all_features(service: AiModelService) -> None:
    cfg = service.get_configuration()
    assert set(cfg.features) == {"captions", "summary", "translation", "audio_description"}
    # OWNER-DECISION-caption-adaptive-tier.md (2026-07-30, BINDING): the caption FLOOR
    # tier (medium) is the mandatory CPU-only baseline and catalog default; large-v3 is
    # now the optional quality tier (auto-selected only when hardware allows).
    assert cfg.features["captions"].default_key == "whisper-medium-faster"
    assert cfg.created_at.tzinfo is not None


def test_configuration_reflects_a_selection(service: AiModelService) -> None:
    service.select_model("translation", "gemma4-31b-cloud", consent_accepted=True)
    cfg = service.get_configuration()
    assert cfg.features["translation"].effective_model_key == "gemma4-31b-cloud"


# --- effective tag (the runtime tag the adapters consume) --------------------


# --- availability (Q2/U4: the §6.3 degraded-state read) ----------------------


def test_availability_local_model_present(service: AiModelService) -> None:
    # 16GB box default summary tag is gemma4:12b; report it PRESENT + reachable.
    avail = service.get_availability(
        list_local_models=lambda: {"gemma4:12b", "translategemma:4b"},
        resolve_secret=lambda ref: None,
    )
    summary = avail.features["summary"]
    assert summary.band == "local"
    assert summary.runtime_reachable is True
    assert summary.model_present is True


def test_availability_local_model_absent_flags_defer(service: AiModelService) -> None:
    # Ollama reachable but the effective summary model is not installed -> defer hint.
    avail = service.get_availability(
        list_local_models=lambda: {"gemma4:e4b"},  # 12b missing
        resolve_secret=lambda ref: None,
    )
    summary = avail.features["summary"]
    assert summary.runtime_reachable is True
    assert summary.model_present is False
    assert "defer" in summary.detail


def test_availability_runtime_unreachable(service: AiModelService) -> None:
    avail = service.get_availability(
        list_local_models=lambda: None,  # Ollama down
        resolve_secret=lambda ref: None,
    )
    summary = avail.features["summary"]
    assert summary.runtime_reachable is False
    assert summary.model_present is None
    assert "unavailable" in summary.detail


def test_availability_cloud_tier_reports_credential_state(service: AiModelService) -> None:
    service.select_model("summary", "gemma4-31b-cloud", consent_accepted=True)
    # No stored credential -> hosted tier will defer.
    avail = service.get_availability(
        list_local_models=lambda: set(), resolve_secret=lambda ref: None
    )
    summary = avail.features["summary"]
    assert summary.band == "cloud"
    assert summary.requires_network is True
    assert summary.runtime_reachable is False
    assert summary.model_present is None
    # With a stored credential -> ready.
    avail2 = service.get_availability(
        list_local_models=lambda: set(), resolve_secret=lambda ref: "stored-key"
    )
    assert avail2.features["summary"].runtime_reachable is True


def test_effective_model_tag_resolves_slug_to_runtime_tag(service: AiModelService) -> None:
    assert service.effective_model_tag("summary") == "gemma4:12b"
    service.select_model("summary", "gemma4-31b-cloud", consent_accepted=True)
    assert service.effective_model_tag("summary") == "gemma4:31b-cloud"
    assert service.effective_model_tag("translation") == "translategemma:4b"
    # OWNER-DECISION-caption-adaptive-tier.md (2026-07-30, BINDING): the default
    # captions tag is now the floor tier (medium), not large-v3.
    assert service.effective_model_tag("captions") == "whisper-medium"
