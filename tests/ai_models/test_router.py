# SPDX-License-Identifier: Apache-2.0
# Copyright (c) The CivicCast Authors
"""S13 AI-model staff API — role gating, select persistence, invalid-model 400, 503.

A minimal FastAPI app mounts the real router, sets the operator identity via
middleware (so the real ``require_any_role`` gate runs), and overrides the DI seam
with a SQLite-backed ``AiModelStore`` + ``AiModelService``. Covers role-gating
(positive read/write / 403 / 401), select persistence, the adaptive default,
the invalid-model 400, the unknown-feature 404, and 503-when-unwired.
"""

from __future__ import annotations

import contextlib
from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager
from types import SimpleNamespace

from fastapi import FastAPI, Request, Response
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from civiccast.ai_models.models import AiModelAvailability, FeatureModelAvailability
from civiccast.ai_models.router import get_ai_model_service, get_model_availability, staff_router
from civiccast.ai_models.secrets import ProviderSecretStoreError
from civiccast.ai_models.service import AiModelService
from civiccast.ai_models.store import AiModelStore
from civiccast.auth.models import OperatorIdentity
from civiccast.captions.models import CaptionCue
from civiccast.captions.review import CaptionReviewItemCreate, InMemoryCaptionReviewStore
from civiccast.db import Base


def _build(
    scopes: tuple[str, ...] | None = ("setup_admin",),
    *,
    wire: bool = True,
    system_ram_total_gb: int = 8,
    has_gpu: bool = False,
    first_run_overrides: dict[str, str | None] | None = None,
    keyring: dict[str, str] | None = None,
    save_provider_secret: Callable[[str, str], None] | None = None,
    delete_provider_secret: Callable[[str], None] | None = None,
    load_provider_secret: Callable[[str], str | None] | None = None,
):
    engine = create_engine(
        "sqlite:///:memory:",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    with engine.connect() as conn:
        with contextlib.suppress(Exception):
            conn.exec_driver_sql("ATTACH DATABASE ':memory:' AS civiccast")
        Base.metadata.create_all(conn)
        conn.commit()

    @contextmanager
    def factory() -> Iterator[Session]:
        sess = Session(bind=engine)
        try:
            yield sess
        finally:
            sess.close()

    store = AiModelStore(factory)
    # An in-memory first-run seed so the override endpoint is testable without touching
    # the real station-state JSON; ``None`` means "not seeded yet" (writer raises).
    overrides: dict[str, str | None] | None = first_run_overrides

    def _read_override(feature: str) -> str | None:
        return overrides.get(feature) if overrides is not None else None

    def _write_override(feature: str, model_key: str | None) -> None:
        if overrides is None:
            raise ValueError("AI model default is not seeded yet; run commissioning first.")
        overrides[feature] = model_key

    # In-memory keyring so the credential endpoints are testable without a real OS
    # keyring and we can assert a stored key is NEVER returned in any response body.
    kr: dict[str, str] = keyring if keyring is not None else {}

    service = AiModelService(
        store,
        system_ram_total_gb=system_ram_total_gb,
        has_gpu=has_gpu,
        read_first_run_override=_read_override,
        write_first_run_override=_write_override,
        save_provider_secret=save_provider_secret
        or (lambda ref, secret: kr.__setitem__(ref, secret)),
        delete_provider_secret=delete_provider_secret or (lambda ref: kr.pop(ref, None)),
        load_provider_secret=load_provider_secret or (lambda ref: kr.get(ref)),
    )

    app = FastAPI()

    @app.middleware("http")
    async def _ident(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        if scopes is not None:
            request.state.operator_identity = OperatorIdentity(
                operator_id="dana", operator_display_name="Dana", scopes=scopes
            )
        return await call_next(request)

    app.include_router(staff_router)
    if wire:
        app.dependency_overrides[get_ai_model_service] = lambda: service
    return app, store, service


def _client(**kw) -> TestClient:
    return TestClient(_build(**kw)[0])


# --- role gate ---------------------------------------------------------------


def test_get_feature_allowed_for_meeting_operator() -> None:
    r = _client(scopes=("meeting_operator",)).get("/api/staff/ai-models/summary")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["feature"] == "summary"
    assert {tier["key"] for tier in body["available_tiers"]}


def test_get_feature_allowed_for_setup_admin() -> None:
    assert _client(scopes=("setup_admin",)).get("/api/staff/ai-models/summary").status_code == 200


def test_get_feature_forbidden_for_records_clerk() -> None:
    assert _client(scopes=("records_clerk",)).get("/api/staff/ai-models/summary").status_code == 403


def test_no_identity_is_unauthorized() -> None:
    assert _client(scopes=None).get("/api/staff/ai-models/summary").status_code == 401


def test_select_forbidden_for_meeting_operator() -> None:
    r = _client(scopes=("meeting_operator",)).post(
        "/api/staff/ai-models/summary/select",
        json={"model_key": "gemma4-e4b-ollama"},
    )
    assert r.status_code == 403


def test_select_allowed_for_setup_admin() -> None:
    assert (
        _client(scopes=("setup_admin",))
        .post(
            "/api/staff/ai-models/summary/select",
            json={"model_key": "gemma4-e4b-ollama"},
        )
        .status_code
        == 200
    )


def test_list_all_models_allowed_for_meeting_operator() -> None:
    r = _client(scopes=("meeting_operator",)).get("/api/staff/ai-models")
    assert r.status_code == 200, r.text
    body = r.json()
    assert set(body["features"]) == {"captions", "summary", "translation", "audio_description"}


def test_list_all_models_forbidden_for_records_clerk() -> None:
    assert _client(scopes=("records_clerk",)).get("/api/staff/ai-models").status_code == 403


def test_availability_reports_the_live_caption_runtime_selection() -> None:
    app = FastAPI()
    app.state.caption_live_model_key = "whisper-large-v3-faster"
    app.state.caption_live_runtime_model_loaded_at_startup = True
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/api/staff/ai-models/availability",
            "query_string": b"",
            "headers": [],
            "app": app,
        }
    )

    class Service:
        def get_availability(self) -> AiModelAvailability:
            return AiModelAvailability(
                features={
                    "captions": FeatureModelAvailability(
                        feature="captions",
                        effective_model_key="whisper-medium-faster",
                        band="local",
                        requires_network=False,
                    )
                }
            )

    availability = get_model_availability(request, Service())  # type: ignore[arg-type]

    captions = availability.features["captions"]
    assert captions.effective_model_key == "whisper-medium-faster"
    assert captions.live_runtime_model_key == "whisper-large-v3-faster"
    assert captions.live_runtime_model_loaded_at_startup is True


# --- select persistence + adaptive default -----------------------------------


def test_select_persists_choice_and_is_reflected() -> None:
    app, _store, _svc = _build(scopes=("setup_admin",))
    client = TestClient(app)
    sel = client.post(
        "/api/staff/ai-models/summary/select",
        json={"model_key": "gemma4-e4b-ollama"},
    )
    assert sel.status_code == 200, sel.text
    assert sel.json()["operator_selected_key"] == "gemma4-e4b-ollama"
    assert sel.json()["effective_model_key"] == "gemma4-e4b-ollama"

    again = client.get("/api/staff/ai-models/summary")
    assert again.json()["operator_selected_key"] == "gemma4-e4b-ollama"


def test_adaptive_default_below_16gb_is_e4b() -> None:
    body = (
        _client(scopes=("setup_admin",), system_ram_total_gb=8)
        .get("/api/staff/ai-models/summary")
        .json()
    )
    assert body["default_key"] == "gemma4-e4b-ollama"
    assert body["adaptive_default"] is True
    assert body["effective_model_key"] == "gemma4-e4b-ollama"


def test_adaptive_default_at_16gb_cpu_only_is_e4b() -> None:
    """Field evidence 2026-08-29: a CPU-only box (has_gpu default False) never gets
    12B, regardless of RAM -- see detect_summary_model_default."""
    body = (
        _client(scopes=("setup_admin",), system_ram_total_gb=16)
        .get("/api/staff/ai-models/summary")
        .json()
    )
    assert body["default_key"] == "gemma4-e4b-ollama"
    assert body["effective_model_key"] == "gemma4-e4b-ollama"


def test_adaptive_default_at_16gb_with_gpu_is_12b() -> None:
    body = (
        _client(scopes=("setup_admin",), system_ram_total_gb=16, has_gpu=True)
        .get("/api/staff/ai-models/summary")
        .json()
    )
    assert body["default_key"] == "gemma4-12b-ollama"
    assert body["effective_model_key"] == "gemma4-12b-ollama"


def test_select_cloud_tier_is_accepted_and_functional() -> None:
    # The hosted Ollama Cloud tier is a real, selectable option (D13) — WITH consent.
    app, _store, _svc = _build(scopes=("setup_admin",))
    r = TestClient(app).post(
        "/api/staff/ai-models/summary/select",
        json={"model_key": "gemma4-31b-cloud", "consent_accepted": True},
    )
    assert r.status_code == 200, r.text
    assert r.json()["operator_selected_key"] == "gemma4-31b-cloud"


def test_select_cloud_tier_without_consent_is_400() -> None:
    # A billable, content-egressing tier must not persist without recorded TOS consent.
    app, _store, _svc = _build(scopes=("setup_admin",))
    r = TestClient(app).post(
        "/api/staff/ai-models/summary/select",
        json={"model_key": "gemma4-31b-cloud"},
    )
    assert r.status_code == 400, r.text
    assert "terms of service" in r.text


def test_consent_actor_is_the_authenticated_operator() -> None:
    # The persisted consent actor is the authenticated identity, not client-supplied.
    app, store, _svc = _build(scopes=("setup_admin",))
    r = TestClient(app).post(
        "/api/staff/ai-models/summary/select",
        json={"model_key": "gemma4-31b-cloud", "consent_accepted": True},
    )
    assert r.status_code == 200, r.text
    row = store.get_selection_row("summary")
    assert row is not None
    assert row.consent_accepted is True
    assert row.consent_actor == "dana"
    assert row.consent_at is not None


def test_availability_endpoint_reports_per_feature_state() -> None:
    # The availability read is role-gated and reports each feature's effective model.
    r = _client(scopes=("setup_admin",)).get("/api/staff/ai-models/availability")
    assert r.status_code == 200, r.text
    features = r.json()["features"]
    assert set(features) == {"captions", "summary", "translation", "audio_description"}
    assert features["summary"]["band"] == "local"


def test_availability_reports_persisted_translation_output_activity() -> None:
    app, _store, _service = _build(scopes=("setup_admin",))
    review_store = InMemoryCaptionReviewStore()
    app.state.store_bundle = SimpleNamespace(caption_review_store=lambda: review_store)
    client = TestClient(app)

    initial = client.get("/api/staff/ai-models/availability")
    assert initial.status_code == 200, initial.text
    assert initial.json()["features"]["translation"].get("translation_generated_cue_count") == 0

    review_store.create(
        CaptionReviewItemCreate(
            review_item_id="asset-1:cue-1:es:hash",
            asset_id="asset-1",
            cue=CaptionCue(
                cue_id="cue-1",
                start_seconds=1.0,
                end_seconds=2.0,
                text="La reunión comienza.",
                confidence=0.92,
            ),
            language="es",
        )
    )
    review_store.create(
        CaptionReviewItemCreate(
            review_item_id="asset-1:cue-2:en:hash",
            asset_id="asset-1",
            cue=CaptionCue(
                cue_id="cue-2",
                start_seconds=2.0,
                end_seconds=3.0,
                text="The meeting continues.",
                confidence=0.96,
            ),
            language="en",
        )
    )

    refreshed = client.get("/api/staff/ai-models/availability")
    assert refreshed.status_code == 200, refreshed.text
    translation = refreshed.json()["features"]["translation"]
    assert translation["translation_generated_cue_count"] == 1
    assert translation["translation_last_generated_at"]


def test_availability_endpoint_requires_a_read_role() -> None:
    r = _client(scopes=("records_clerk",)).get("/api/staff/ai-models/availability")
    assert r.status_code == 403


# --- invalid input -----------------------------------------------------------


def test_select_unknown_model_is_400() -> None:
    r = _client(scopes=("setup_admin",)).post(
        "/api/staff/ai-models/summary/select",
        json={"model_key": "no-such-model"},
    )
    assert r.status_code == 400
    assert "no-such-model" in r.text


def test_select_model_not_offered_for_feature_is_400() -> None:
    # whisper is a captions tier; it is not offered for summary.
    r = _client(scopes=("setup_admin",)).post(
        "/api/staff/ai-models/summary/select",
        json={"model_key": "whisper-large-v3-faster"},
    )
    assert r.status_code == 400


def test_get_unknown_feature_is_404() -> None:
    assert _client(scopes=("setup_admin",)).get("/api/staff/ai-models/bogus").status_code == 404


def test_select_unknown_feature_is_404() -> None:
    r = _client(scopes=("setup_admin",)).post(
        "/api/staff/ai-models/bogus/select",
        json={"model_key": "gemma4-e4b-ollama"},
    )
    assert r.status_code == 404


# --- first-run override (wizard) ---------------------------------------------


def test_first_run_override_endpoint_sets_and_reflects() -> None:
    app, _store, _svc = _build(scopes=("setup_admin",), first_run_overrides={"summary": None})
    client = TestClient(app)
    r = client.put(
        "/api/staff/ai-models/summary/first-run-override",
        json={"model_key": "gemma4-e4b-ollama"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["effective_model_key"] == "gemma4-e4b-ollama"
    # Reflected on a fresh GET (no DB selection yet — the override drives it).
    again = client.get("/api/staff/ai-models/summary")
    assert again.json()["effective_model_key"] == "gemma4-e4b-ollama"


def test_first_run_override_clear_returns_to_default() -> None:
    app, _store, _svc = _build(
        scopes=("setup_admin",), first_run_overrides={"summary": "gemma4-e4b-ollama"}
    )
    client = TestClient(app)
    r = client.put("/api/staff/ai-models/summary/first-run-override", json={"model_key": None})
    assert r.status_code == 200, r.text
    # 8GB box adaptive default.
    assert r.json()["effective_model_key"] == "gemma4-e4b-ollama"
    assert r.json()["operator_selected_key"] is None


def test_first_run_override_forbidden_for_meeting_operator() -> None:
    r = _client(scopes=("meeting_operator",), first_run_overrides={"summary": None}).put(
        "/api/staff/ai-models/summary/first-run-override",
        json={"model_key": "gemma4-e4b-ollama"},
    )
    assert r.status_code == 403


def test_first_run_override_rejects_hosted_tier_400() -> None:
    r = _client(scopes=("setup_admin",), first_run_overrides={"summary": None}).put(
        "/api/staff/ai-models/summary/first-run-override",
        json={"model_key": "gemma4-31b-cloud"},
    )
    assert r.status_code == 400, r.text
    assert "consent" in r.text.lower()


def test_first_run_override_unknown_model_400() -> None:
    r = _client(scopes=("setup_admin",), first_run_overrides={"summary": None}).put(
        "/api/staff/ai-models/summary/first-run-override",
        json={"model_key": "no-such-model"},
    )
    assert r.status_code == 400


def test_first_run_override_unknown_feature_404() -> None:
    r = _client(scopes=("setup_admin",), first_run_overrides={"summary": None}).put(
        "/api/staff/ai-models/bogus/first-run-override",
        json={"model_key": "gemma4-e4b-ollama"},
    )
    assert r.status_code == 404


def test_first_run_override_before_commissioning_409() -> None:
    # overrides=None means the seed does not exist yet -> the writer raises -> 409.
    r = _client(scopes=("setup_admin",), first_run_overrides=None).put(
        "/api/staff/ai-models/summary/first-run-override",
        json={"model_key": "gemma4-e4b-ollama"},
    )
    assert r.status_code == 409, r.text


# --- cloud provider credentials (DONE-10) ------------------------------------


def test_save_provider_key_stores_and_reports_stored() -> None:
    kr: dict[str, str] = {}
    app, _store, _svc = _build(scopes=("setup_admin",), keyring=kr)
    client = TestClient(app)
    r = client.put(
        "/api/staff/ai-models/credentials/openrouter",
        json={"api_key": "sk-or-supersecret"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body == {"provider": "openrouter", "stored": True}
    # The key is written to the keyring under the provider handle...
    assert kr["openrouter-key"] == "sk-or-supersecret"
    # ...and NEVER appears anywhere in the response body.
    assert "sk-or-supersecret" not in r.text


def test_get_provider_key_status_reports_boolean_only() -> None:
    kr: dict[str, str] = {"ollama-cloud-key": "oc-secret"}
    app, _store, _svc = _build(scopes=("meeting_operator",), keyring=kr)
    client = TestClient(app)
    r = client.get("/api/staff/ai-models/credentials/ollama-cloud")
    assert r.status_code == 200, r.text
    assert r.json() == {"provider": "ollama-cloud", "stored": True}
    assert "oc-secret" not in r.text
    # A provider with no stored key reports stored=False.
    r2 = client.get("/api/staff/ai-models/credentials/openrouter")
    assert r2.json() == {"provider": "openrouter", "stored": False}


def test_get_provider_key_status_missing_keyring_reports_not_stored() -> None:
    def load_secret(_ref: str) -> str | None:
        raise ProviderSecretStoreError("No keyring backend available.")

    client = _client(scopes=("meeting_operator",), load_provider_secret=load_secret)
    r = client.get("/api/staff/ai-models/credentials/openrouter")

    assert r.status_code == 200, r.text
    assert r.json() == {"provider": "openrouter", "stored": False}


def test_save_provider_key_missing_keyring_is_503() -> None:
    def save_secret(_ref: str, _secret: str) -> None:
        raise ProviderSecretStoreError("No keyring backend available.")

    client = _client(scopes=("setup_admin",), save_provider_secret=save_secret)
    r = client.put(
        "/api/staff/ai-models/credentials/openrouter",
        json={"api_key": "sk-or-supersecret"},
    )

    assert r.status_code == 503, r.text
    assert "credential store is unavailable" in r.text


def test_delete_provider_key_clears_it() -> None:
    kr: dict[str, str] = {"openrouter-key": "sk-or-secret"}
    app, _store, _svc = _build(scopes=("setup_admin",), keyring=kr)
    client = TestClient(app)
    r = client.delete("/api/staff/ai-models/credentials/openrouter")
    assert r.status_code == 200, r.text
    assert r.json() == {"provider": "openrouter", "stored": False}
    assert "openrouter-key" not in kr
    # Idempotent: deleting again is still a clean stored=False.
    r2 = client.delete("/api/staff/ai-models/credentials/openrouter")
    assert r2.json()["stored"] is False


def test_delete_provider_key_missing_keyring_is_503() -> None:
    def load_secret(_ref: str) -> str | None:
        raise ProviderSecretStoreError("No keyring backend available.")

    client = _client(scopes=("setup_admin",), load_provider_secret=load_secret)
    r = client.delete("/api/staff/ai-models/credentials/openrouter")

    assert r.status_code == 503, r.text
    assert "credential store is unavailable" in r.text


def test_save_provider_key_forbidden_for_meeting_operator() -> None:
    r = _client(scopes=("meeting_operator",)).put(
        "/api/staff/ai-models/credentials/openrouter",
        json={"api_key": "sk-or-secret"},
    )
    assert r.status_code == 403


def test_delete_provider_key_forbidden_for_meeting_operator() -> None:
    r = _client(scopes=("meeting_operator",)).delete("/api/staff/ai-models/credentials/openrouter")
    assert r.status_code == 403


def test_get_provider_key_status_forbidden_for_records_clerk() -> None:
    r = _client(scopes=("records_clerk",)).get("/api/staff/ai-models/credentials/openrouter")
    assert r.status_code == 403


def test_save_provider_key_unknown_provider_404() -> None:
    r = _client(scopes=("setup_admin",)).put(
        "/api/staff/ai-models/credentials/not-a-provider",
        json={"api_key": "x"},
    )
    assert r.status_code == 404


def test_save_provider_key_blank_is_rejected() -> None:
    # A whitespace-only key is rejected (422 from min_length OR 400 from the service).
    r = _client(scopes=("setup_admin",)).put(
        "/api/staff/ai-models/credentials/openrouter",
        json={"api_key": "   "},
    )
    assert r.status_code in (400, 422), r.text


def test_save_provider_key_empty_string_is_422() -> None:
    r = _client(scopes=("setup_admin",)).put(
        "/api/staff/ai-models/credentials/openrouter",
        json={"api_key": ""},
    )
    assert r.status_code == 422


# --- unwired -> 503 ----------------------------------------------------------


def test_unwired_returns_503() -> None:
    app, *_ = _build(scopes=("setup_admin",), wire=False)
    assert TestClient(app).get("/api/staff/ai-models/summary").status_code == 503
