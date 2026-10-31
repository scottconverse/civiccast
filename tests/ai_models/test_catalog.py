# SPDX-License-Identifier: Apache-2.0
# Copyright (c) The CivicCast Authors
"""S13 hard-coded model catalog (decision A) — the §3.1.1 key->tag->provider rows.

Locks the canonical mapping: the local default per feature, the functional cloud
tiers (Ollama Cloud + OpenRouter), and the cost/privacy/network flags that drive
"default OFF, operator opts in" behavior.
"""

from __future__ import annotations

from civiccast.ai_models.catalog import (
    CATALOG_FEATURES,
    build_feature_registry,
    catalog_tier,
    catalog_tier_for_feature,
    catalog_tiers_for,
)
from civiccast.ai_models.models import ModelTier


def test_catalog_covers_the_supported_features() -> None:
    assert set(CATALOG_FEATURES) == {"captions", "summary", "translation", "audio_description"}


def test_section_3_1_1_key_to_tag_mapping_is_exact() -> None:
    expected = {
        "gemma4-12b-ollama": ("gemma4:12b", "ollama"),
        "gemma4-e4b-ollama": ("gemma4:e4b", "ollama"),
        "gemma4-31b-cloud": ("gemma4:31b-cloud", "ollama-cloud"),
        "translategemma-4b-ollama": ("translategemma:4b", "ollama"),
        "whisper-large-v3-faster": ("whisper-large-v3", "external"),
    }
    for key, (tag, provider) in expected.items():
        tier = catalog_tier(key)
        assert tier.model_id == tag, key
        assert tier.provider == provider, key


def test_translation_tag_is_translategemma_never_gemma4() -> None:
    assert catalog_tier("translategemma-4b-ollama").model_id == "translategemma:4b"


def test_local_default_per_feature_is_a_local_zero_cost_tier() -> None:
    # The default must always be the local (zero cloud fee) tier.
    for feature in CATALOG_FEATURES:
        reg = build_feature_registry(feature)
        default = catalog_tier(reg.default_key)
        assert default.provider in ("ollama", "external"), feature
        assert default.cost_per_token_usd == 0.0, feature
        assert default.private is True, feature
        assert default.requires_network is False, feature


def test_cloud_tiers_are_functional_default_off_priced() -> None:
    # Cloud tiers ship functional, default OFF, with a real per-token cost.
    cloud_keys = ("gemma4-31b-cloud",)
    for key in cloud_keys:
        tier = catalog_tier(key)
        assert tier.requires_network is True, key
        assert tier.private is False, key
        assert tier.cost_per_token_usd > 0.0, key


def test_summary_offers_local_and_cloud_tiers() -> None:
    keys = {t.key for t in catalog_tiers_for("summary")}
    assert {"gemma4-12b-ollama", "gemma4-e4b-ollama", "gemma4-31b-cloud"} <= keys
    # An OpenRouter mid-tier (frontier) is offered for summary, too.
    assert any(t.provider == "openrouter" for t in catalog_tiers_for("summary"))


def test_summary_default_is_adaptive_12b_at_16gb_with_a_real_gpu() -> None:
    reg = build_feature_registry("summary", system_ram_total_gb=16, has_gpu=True)
    assert reg.default_key == "gemma4-12b-ollama"
    assert reg.adaptive_default is True


def test_summary_default_degrades_to_e4b_below_16gb() -> None:
    reg = build_feature_registry("summary", system_ram_total_gb=8)
    assert reg.default_key == "gemma4-e4b-ollama"
    assert reg.adaptive_default is True


def test_summary_default_is_e4b_on_a_cpu_only_32gb_box() -> None:
    """Field evidence 2026-08-29: a 32GB CPU-only box (has_gpu defaults False)
    must default to e4b, not 12B -- see detect_summary_model_default for the
    measured timings (12B: 366s then two failures; e4b: 94-128s, every attempt)."""
    reg = build_feature_registry("summary", system_ram_total_gb=32)
    assert reg.default_key == "gemma4-e4b-ollama"
    assert reg.adaptive_default is True


def test_non_summary_features_are_not_adaptive() -> None:
    assert build_feature_registry("captions").adaptive_default is False
    assert build_feature_registry("translation").adaptive_default is False


def test_default_key_is_always_one_of_the_available_tiers() -> None:
    for feature in CATALOG_FEATURES:
        reg = build_feature_registry(feature)
        keys = {t.key for t in reg.available_tiers}
        assert reg.default_key in keys, feature


def test_catalog_tiers_are_model_tier_instances() -> None:
    for feature in CATALOG_FEATURES:
        for tier in catalog_tiers_for(feature):
            assert isinstance(tier, ModelTier)


def test_shared_cloud_tier_is_defined_once_for_summary_and_translation() -> None:
    # gemma4-31b-cloud must carry IDENTICAL cost / tag / privacy flags for both
    # features (it is one shared catalog constant, not two drifting copies — M6).
    summary_tier = catalog_tier_for_feature("summary", "gemma4-31b-cloud")
    translation_tier = catalog_tier_for_feature("translation", "gemma4-31b-cloud")
    assert summary_tier is not None
    assert translation_tier is not None
    assert summary_tier.model_dump() == translation_tier.model_dump()
    assert summary_tier.cost_per_token_usd == translation_tier.cost_per_token_usd
    assert summary_tier.model_id == translation_tier.model_id


def test_catalog_tier_for_feature_scopes_to_the_feature() -> None:
    # The OpenRouter frontier tier is offered for summary but not translation.
    assert catalog_tier_for_feature("summary", "gemini-2.5-flash-openrouter") is not None
    assert catalog_tier_for_feature("translation", "gemini-2.5-flash-openrouter") is None
