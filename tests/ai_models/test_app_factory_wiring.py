# SPDX-License-Identifier: Apache-2.0
# Copyright (c) The CivicCast Authors
"""S13 T4/M5 — the app factory wires summary + translation runtimes through the service.

The most valuable S13 wiring is the DI override that makes the operator's selection
take effect on the live HTTP path. ``test_runtime_wiring.py`` exercises ``build_*``
directly with a hand-built service; nothing asserted that ``create_app()`` registers
``get_summary_model`` -> a service-backed adapter, or that a non-default selection
flows through. A refactor dropping the override (reverting summary to the dead loopback
default) would have passed the whole suite. These tests close that gap:

* select a non-default summary model in the durable store, resolve
  ``app.dependency_overrides[get_summary_model]()`` and assert the built adapter
  reflects the selection (not the default);
* with the caption tap enabled inline, assert the app factory built the tap worker with
  an operator-selected translation provider (T3 — translation is now wired too).
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest

from civiccast.ai_models.router import get_ai_model_service
from civiccast.ai_models.store import AiModelStore
from civiccast.summary.router import get_summary_model


def _migrate(db_path: Path) -> None:
    from alembic import command
    from alembic.config import Config

    repo_root = Path(__file__).resolve().parents[2]
    cfg = Config(str(repo_root / "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")
    command.upgrade(cfg, "head")


@pytest.fixture
def durable_app_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    db_path = tmp_path / "ai-wiring.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")
    monkeypatch.setenv("CIVICCAST_AUTH_ACK", "1")
    monkeypatch.setenv("CIVICCAST_FINALIZATION_WORKER", "off")
    monkeypatch.delenv("CIVICCAST_UPLOAD_DIR", raising=False)
    _migrate(db_path)
    # Avoid real Ollama network: the local adapters' for_release manifest probe is stubbed.
    _stub_manifest(monkeypatch)
    yield tmp_path


def _stub_manifest(monkeypatch: pytest.MonkeyPatch) -> None:
    from civiccast.ai_runtime.ollama_client import OllamaModelManifest
    from civiccast.summary import ollama as summary_module
    from civiccast.translate import ollama as translate_module

    def fake_manifest(model: str, *, base_url: str) -> OllamaModelManifest:
        return OllamaModelManifest(
            model=model,
            digest="sha256:" + ("a" * 64),
            runtime_version="ollama 0.9.0",
            manifest_source=f"http://127.0.0.1:11434/api/tags#{model}",
            details={},
        )

    monkeypatch.setattr(summary_module, "get_ollama_model_manifest", fake_manifest)
    monkeypatch.setattr(translate_module, "get_ollama_model_manifest", fake_manifest)


def _store_over_db() -> AiModelStore:
    """An AiModelStore over the same SQLite DB the app uses (DATABASE_URL)."""
    import os

    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    database_url = os.environ["DATABASE_URL"]
    engine = create_engine(database_url, future=True)
    if database_url.startswith("sqlite"):
        # Mirror the app's SQLite schema-translate (the ORM tables live under the
        # ``civiccast`` schema; SQLite has no schemas, so it maps to the default).
        engine = engine.execution_options(schema_translate_map={"civiccast": None})

    @contextmanager
    def factory() -> Iterator[Session]:
        with Session(bind=engine) as session:
            yield session

    return AiModelStore(factory)


def test_app_factory_wires_summary_model_through_service(durable_app_env: Path) -> None:
    from civiccast.app import create_app

    # Select a NON-default summary model (e4b instead of the box default) BEFORE the app
    # resolves the override, so a default-only wiring would visibly disagree.
    _store_over_db().set_selection("summary", model_key="gemma4-e4b-ollama", tier="local")

    app = create_app()
    # The app factory must have registered the service-backed summary override.
    assert get_summary_model in app.dependency_overrides
    assert get_ai_model_service in app.dependency_overrides

    adapter = app.dependency_overrides[get_summary_model]()
    # The built adapter reflects the operator's selection, not a hard-coded default.
    assert adapter.model_tag == "gemma4:e4b"


def test_app_factory_summary_override_returns_clean_503_when_ollama_unreachable(
    durable_app_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """rc.5 fast-follow: the durable-storage-wired _resolve_summary_model override
    is a SEPARATE path to the local-Ollama build from summary/router.py's own
    get_summary_model default (real bug found live on the rc.4 VM: the router's
    fix alone did not cover this path since app.py's dependency_overrides wins
    whenever a database is configured, which is the normal/production case).
    Both must convert OllamaRuntimeUnavailableError into the same clean 503."""
    from fastapi import HTTPException

    from civiccast.ai_runtime.ollama_client import OllamaRuntimeUnavailableError
    from civiccast.app import create_app
    from civiccast.summary import ollama as summary_module

    def unreachable_manifest(model: str, *, base_url: str) -> None:
        raise OllamaRuntimeUnavailableError(
            f"Local Ollama request failed for {base_url}/api/tags. Start Ollama and retry."
        )

    monkeypatch.setattr(summary_module, "get_ollama_model_manifest", unreachable_manifest)

    app = create_app()
    with pytest.raises(HTTPException) as excinfo:
        app.dependency_overrides[get_summary_model]()

    assert excinfo.value.status_code == 503
    assert "ollama" in excinfo.value.detail.lower()


def test_app_factory_summary_override_follows_a_cloud_selection(
    durable_app_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from civiccast.ai_models.dispatch import CloudSummaryModel
    from civiccast.app import create_app

    _store_over_db().set_selection(
        "summary", model_key="gemma4-31b-cloud", tier="cloud", consent_accepted=True
    )
    app = create_app()
    adapter = app.dependency_overrides[get_summary_model]()
    # A cloud selection builds the cloud-backed shim through the live override (E1/T1+T4).
    assert isinstance(adapter, CloudSummaryModel)
    assert adapter.model_tag == "gemma4:31b-cloud"


def test_app_factory_wires_translation_provider_into_tap_worker(
    durable_app_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from civiccast.app import create_app
    from civiccast.translate.ollama import OllamaSpanishTranslator

    tap_dir = durable_app_env / "captiontap"
    tap_dir.mkdir()
    monkeypatch.setenv("CIVICCAST_CAPTION_TAP", "inline")
    monkeypatch.setenv("CIVICCAST_CAPTION_TAP_DIR", str(tap_dir))

    app = create_app()
    worker = getattr(app.state, "caption_tap_worker", None)
    assert worker is not None, "create_app() must build the caption tap worker when inline"
    provider = worker._translation_provider
    # Translation is wired (T3): the worker got a service-built translator (the local
    # default here), not None.
    assert isinstance(provider, OllamaSpanishTranslator)
    assert provider.model_tag == "translategemma:4b"


def test_app_factory_starts_with_live_translation_off_when_the_translation_model_is_unavailable(
    durable_app_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """F10 (RC4 field run 2026-11-04, Beelink): another Ollama held 127.0.0.1:11434, so the
    station's own Ollama never bound; the translator's model probe reached the foreign Ollama
    (no translategemma) and create_app() raised, so the control plane never answered /health
    and the install rolled back after ~9 min. AI is optional ("degraded AI, service healthy"):
    the station must come up with live translation off and say why, not crash."""
    from civiccast.ai_runtime.ollama_client import OllamaRuntimeUnavailableError
    from civiccast.app import create_app
    from civiccast.translate import ollama as translate_module

    def missing_model(model: str, *, base_url: str) -> None:
        raise OllamaRuntimeUnavailableError(
            f"Ollama model '{model}' is not installed. Run `ollama pull {model}` and retry."
        )

    monkeypatch.setattr(translate_module, "get_ollama_model_manifest", missing_model)
    tap_dir = durable_app_env / "captiontap"
    tap_dir.mkdir()
    monkeypatch.setenv("CIVICCAST_CAPTION_TAP", "inline")
    monkeypatch.setenv("CIVICCAST_CAPTION_TAP_DIR", str(tap_dir))

    app = create_app()
    worker = getattr(app.state, "caption_tap_worker", None)
    assert worker is not None, "live captions must still run without translation"
    assert worker._translation_provider is None
    reason = app.state.caption_live_translation_unavailable
    assert "translategemma" in reason and "not installed" in reason


def test_app_factory_wires_translation_provider_factory_into_offline_caption_worker(
    durable_app_env: Path,
) -> None:
    """Recorded-Spanish (VOD): the offline caption worker must get a

    translation provider factory so the recorded-Spanish leg can run, and
    the leg must be on by default. Mirrors the LIVE tap assertion above,
    except the offline worker builds the translator lazily per attempt
    (nothing queued -> no model load), so the wiring proof is the factory,
    not an eagerly-built provider.
    """

    from civiccast.app import create_app
    from civiccast.translate.ollama import OllamaSpanishTranslator

    app = create_app()
    worker = getattr(app.state, "offline_caption_worker", None)
    assert worker is not None, "create_app() must build the offline caption worker"
    # Spanish is on by default for the native station requirement.
    assert worker._settings.spanish_enabled is True
    assert worker._settings.spanish_target_language == "es"
    # The factory is wired but not yet invoked (no model loaded at startup).
    assert worker._translation_provider is None
    assert worker._translation_provider_factory is not None
    provider = worker._translation_provider_factory()
    assert isinstance(provider, OllamaSpanishTranslator)
    assert provider.model_tag == "translategemma:4b"


def test_app_factory_wires_the_caption_cdn_republisher(durable_app_env: Path) -> None:
    """A CDN-served recording must get its caption tracks, not just the local copy.

    ``attach_reviewed_captions`` rewrites the LOCAL package. Without this
    seam wired, a station with ``CIVICCAST_CDN_PROVIDER`` set would mark the
    caption job complete while residents watching through the CDN still got
    the pre-caption manifest. The republisher is built unconditionally and
    resolves the adapter per call, so turning a CDN on later needs no
    restart -- and it is a no-op when no CDN is configured.
    """

    from civiccast.app import create_app
    from civiccast.captions.cdn_republish import VodPackageCdnRepublisher

    app = create_app()
    worker = getattr(app.state, "offline_caption_worker", None)
    assert worker is not None, "create_app() must build the offline caption worker"
    republisher = worker._cdn_republisher
    assert isinstance(republisher, VodPackageCdnRepublisher)
    # The adapter provider is app.state.resolve_cdn_adapter, the same seam
    # the surge switch and finalization worker resolve through -- with no
    # CDN configured in this environment it resolves to None.
    assert republisher._adapter_provider() is None


def test_native_station_startup_enables_caption_tap_feed_and_decode_back_proof(
    durable_app_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from civiccast.app import create_app
    from civiccast.captions.runtime import REQUIRED_LOCAL_MODEL_FILES
    from civiccast.native import station_runtime
    from civiccast.native.caption_tiers import LARGE_V3_TIER_ID

    tap_dir = durable_app_env / "caption-tap"
    model_dir = durable_app_env / "captions-large-v3"
    tap_dir.mkdir()
    model_dir.mkdir()
    for name in REQUIRED_LOCAL_MODEL_FILES:
        (model_dir / name).write_bytes(b"test-model-file")
    _store_over_db().set_selection("captions", model_key="whisper-large-v3-faster", tier="local")
    resolved: dict[str, str] = {}

    def resolve_model(_root: str, tier_id: str, *, acquisition_root: Path):
        resolved["tier"] = tier_id
        resolved["acquisition_root"] = str(acquisition_root)
        return model_dir, {"model.bin": {"bytes": 4, "sha256": "synthetic"}}

    monkeypatch.setattr(station_runtime, "resolve_caption_model_for_tier", resolve_model)
    monkeypatch.setenv("CIVICCAST_NATIVE_STATION", "1")
    monkeypatch.setenv("CIVICCAST_NATIVE_STATION_ROOT", str(durable_app_env / "station"))
    monkeypatch.setenv("CIVICCAST_CAPTION_TIER", "floor")
    monkeypatch.setenv("CIVICCAST_CAPTION_TAP", "inline")
    monkeypatch.setenv("CIVICCAST_CAPTION_TAP_DIR", str(tap_dir))
    monkeypatch.setenv("CIVICCAST_CAPTION_RUNTIME", "faster-whisper")
    monkeypatch.setenv("CIVICCAST_LIVE_CAPTION_ENGINE", "whisper")
    monkeypatch.setenv("CIVICCAST_WHISPER_MODEL_PATH", str(model_dir))
    monkeypatch.setenv("CIVICCAST_WHISPER_DEVICE", "cuda")
    monkeypatch.setenv("CIVICCAST_WHISPER_COMPUTE_TYPE", "int8_float16")
    monkeypatch.setenv("CIVICCAST_EGRESS_ENGINE", "gstreamer")
    monkeypatch.setenv("CIVICCAST_EGRESS_EMBED_CAPTIONS", "1")

    app = create_app()

    names = {getattr(supervisor, "_name", None) for supervisor in app.state.background_supervisors}
    assert {
        "civiccast-caption-tap-worker",
        "civiccast-caption-feed",
        "civiccast-caption-proof",
    } <= names
    worker = app.state.caption_tap_worker
    assert resolved["tier"] == LARGE_V3_TIER_ID
    assert worker._runtime.model_size_or_path == str(model_dir.resolve())
    assert worker._runtime.device == "cuda"
    assert worker._runtime.compute_type == "int8_float16"
