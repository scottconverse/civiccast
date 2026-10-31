// SPDX-License-Identifier: Apache-2.0
// Copyright (c) The CivicCast Authors
import { describe, expect, it } from 'vitest'

import type { FeatureModelAvailability, ModelTier } from '../types/api.generated'
import {
  availabilityWarning,
  credentialProviderForTier,
  FEATURE_ORDER,
  featureLabel,
  isCloudTier,
  privacyLabel,
  tierBandLabel,
  tierBandTone,
  tierCostLabel,
  tierFitsBox,
  tierLatencyLabel,
  tierRamLabel,
} from './ai-models-format'

function tier(overrides: Partial<ModelTier> = {}): ModelTier {
  return {
    key: 'gemma4-12b-ollama',
    provider: 'ollama',
    model_id: 'gemma4:12b',
    cost_per_token_usd: 0,
    latency_p95_ms: 4200,
    private: true,
    requires_network: false,
    min_ram_gb: 16,
    license_url: null,
    notes: '',
    ...overrides,
  }
}

describe('ai-models-format', () => {
  it('orders the supported feature entries', () => {
    expect(FEATURE_ORDER).toEqual(['captions', 'summary', 'translation', 'audio_description'])
  })

  it('labels features in plain English', () => {
    expect(featureLabel('captions')).toBe('Captions')
    expect(featureLabel('summary')).toBe('Summary')
    expect(featureLabel('translation')).toBe('Translation')
    // unknown feature falls back to the raw value (never throws)
    expect(featureLabel('mystery')).toBe('mystery')
  })

  it('maps provider to a Local/Cloud/Frontier band (text, not just color)', () => {
    expect(tierBandLabel(tier({ provider: 'ollama' }))).toBe('Local')
    // faster-whisper "external" runs on-box, so it is Local for privacy purposes
    expect(tierBandLabel(tier({ provider: 'external' }))).toBe('Local')
    expect(tierBandLabel(tier({ provider: 'ollama-cloud' }))).toBe('Cloud')
    expect(tierBandLabel(tier({ provider: 'openrouter' }))).toBe('Frontier')
    expect(tierBandLabel(undefined)).toBe('Unknown')
  })

  it('tones the band (local=ok, cloud=info, frontier=warn)', () => {
    expect(tierBandTone(tier({ provider: 'ollama' }))).toBe('ok')
    expect(tierBandTone(tier({ provider: 'external' }))).toBe('ok')
    expect(tierBandTone(tier({ provider: 'ollama-cloud' }))).toBe('info')
    expect(tierBandTone(tier({ provider: 'openrouter' }))).toBe('warn')
    expect(tierBandTone(undefined)).toBe('neutral')
  })

  it('identifies cloud (non-private) tiers — the ones that need TOS opt-in', () => {
    expect(isCloudTier(tier({ provider: 'ollama', private: true }))).toBe(false)
    expect(isCloudTier(tier({ provider: 'external', private: true }))).toBe(false)
    expect(isCloudTier(tier({ provider: 'ollama-cloud', private: false }))).toBe(true)
    expect(isCloudTier(tier({ provider: 'openrouter', private: false }))).toBe(true)
    expect(isCloudTier(undefined)).toBe(false)
  })

  it('maps a hosted tier to its credential provider; local tiers have none', () => {
    expect(credentialProviderForTier(tier({ provider: 'ollama-cloud' }))).toBe('ollama-cloud')
    expect(credentialProviderForTier(tier({ provider: 'openrouter' }))).toBe('openrouter')
    // local on-box tiers carry no API key — the UI must not offer a key field
    expect(credentialProviderForTier(tier({ provider: 'ollama' }))).toBeNull()
    expect(credentialProviderForTier(tier({ provider: 'external' }))).toBeNull()
    expect(credentialProviderForTier(undefined)).toBeNull()
  })

  it('shows free for local tiers and $USD/token + estimate for metered tiers (decision A)', () => {
    expect(tierCostLabel(tier({ cost_per_token_usd: 0 }))).toBe('Free (local)')
    // 1e-7 USD/token -> per-million-token estimate of $0.10
    const metered = tierCostLabel(tier({ provider: 'ollama-cloud', private: false, cost_per_token_usd: 1e-7 }))
    expect(metered).toContain('/token')
    expect(metered).toContain('$0.10')
    expect(metered).toContain('1M tokens')
    expect(tierCostLabel(undefined)).toBe('—')
  })

  it('labels privacy clearly and notes network for cloud tiers', () => {
    expect(privacyLabel(tier({ private: true, requires_network: false }))).toBe('On-device — private')
    const cloud = privacyLabel(tier({ provider: 'ollama-cloud', private: false, requires_network: true }))
    expect(cloud).toContain('Sent to cloud provider')
    expect(cloud).toContain('network required')
    expect(privacyLabel(undefined)).toBe('—')
  })

  // --- U2: cost copy never shows a paid model as $0.0/token or Free -----------
  it('U2: a positive rate below the 7-decimal floor (5e-8) is NOT $0.0/token and NOT Free', () => {
    const label = tierCostLabel(
      tier({ provider: 'ollama-cloud', private: false, cost_per_token_usd: 5e-8 }),
    )
    expect(label).not.toContain('$0.0/token')
    // the per-token figure must carry the significant digits (5) — never collapse to zero
    expect(label).toMatch(/\$0\.0+5\d*\/token/)
    expect(label).not.toContain('Free')
    expect(label).toContain('/token')
    expect(label).toContain('1M tokens')
    // both halves derive from the same value: 5e-8/token → ~$0.050 / 1M tokens
    expect(label).toContain('0.05')
  })

  it('U2: per-token and per-1M figures agree (no internal contradiction) for 1.5e-7', () => {
    const label = tierCostLabel(
      tier({ provider: 'openrouter', private: false, cost_per_token_usd: 1.5e-7 }),
    )
    // per-1M of 1.5e-7 is $0.15; the per-token figure must be a real nonzero number too
    expect(label).toContain('0.15')
    expect(label).not.toContain('$0.0/token')
    expect(label).not.toContain('Free')
  })

  it('U2: a current-catalog rate (1e-7) still reads clearly', () => {
    const label = tierCostLabel(
      tier({ provider: 'ollama-cloud', private: false, cost_per_token_usd: 1e-7 }),
    )
    expect(label).toContain('$0.10')
    expect(label).toContain('1M tokens')
  })

  it('U2: a >= 1 cent per-token rate uses plain fixed-2 currency', () => {
    const label = tierCostLabel(
      tier({ provider: 'openrouter', private: false, cost_per_token_usd: 0.5 }),
    )
    expect(label).toContain('$0.50/token')
  })

  // --- U1: latency label ------------------------------------------------------
  it('U1: cloud/frontier tiers render sub-second latency in ms and >=1s in seconds', () => {
    expect(
      tierLatencyLabel(tier({ provider: 'openrouter', latency_p95_ms: 900 })),
    ).toBe('≈900 ms typical')
    expect(
      tierLatencyLabel(tier({ provider: 'openrouter', latency_p95_ms: 1500 })),
    ).toBe('≈1.5 s typical')
    expect(
      tierLatencyLabel(tier({ provider: 'ollama-cloud', latency_p95_ms: 4200 })),
    ).toBe('≈4.2 s typical')
    expect(tierLatencyLabel(tier({ provider: 'openrouter', latency_p95_ms: 0 }))).toBe('—')
    expect(tierLatencyLabel(undefined)).toBe('—')
  })

  it('U1 (field evidence 2026-08-29): on-box ollama tiers never render a bare precise number', () => {
    // The old fixed "≈4.2 s typical" for gemma4-12b was ~30x wrong on the
    // 32GB CPU-only reference station (measured 366s). A local LLM tier
    // renders a floor + CPU-only caveat instead of false precision.
    const label = tierLatencyLabel(tier({ provider: 'ollama', latency_p95_ms: 366_000 }))
    expect(label).toContain('366 s+')
    expect(label).toContain('CPU-only')
    expect(label).not.toBe('≈366.0 s typical')
  })

  it('U1 (field evidence 2026-08-29): external (faster-whisper) tiers render a realtime multiple', () => {
    // The old fixed "≈500 ms typical" for whisper-medium was ~70x wrong in
    // the field (measured ~3.3x real time for an 11s clip). Transcription
    // time scales with recording length, so it is never a fixed duration.
    const label = tierLatencyLabel(tier({ provider: 'external', latency_p95_ms: 3_300 }))
    expect(label).toContain('3.3x')
    expect(label).toContain("recording's length")
    expect(label).toContain('CPU-only')
  })

  // --- U3: RAM requirement + fit ---------------------------------------------
  it('U3: renders the RAM requirement text and gates by box RAM', () => {
    expect(tierRamLabel(tier({ min_ram_gb: 16 }))).toBe('needs 16 GB')
    expect(tierRamLabel(tier({ min_ram_gb: 8 }))).toBe('needs 8 GB')
    // a 16GB-only tier does not fit an 8GB box, but fits a 25GB box
    expect(tierFitsBox(tier({ min_ram_gb: 16 }), 8)).toBe(false)
    expect(tierFitsBox(tier({ min_ram_gb: 16 }), 25)).toBe(true)
    // unknown box RAM never gates (do-no-harm)
    expect(tierFitsBox(tier({ min_ram_gb: 16 }), undefined)).toBe(true)
  })

  // --- U4/Q2/M3: availability warning text -----------------------------------
  it('U4/M3: warns (TEXT) when the runtime is unreachable or the model is absent', () => {
    const base: FeatureModelAvailability = {
      feature: 'summary',
      effective_model_key: 'gemma4-12b-ollama',
      band: 'local',
      requires_network: false,
      runtime_reachable: true,
      model_present: true,
    }
    expect(availabilityWarning({ ...base, runtime_reachable: false })).toContain('Ollama unavailable')
    const absent = availabilityWarning({ ...base, model_present: false })
    expect(absent).toContain('Not installed')
    expect(absent).toContain('feature will defer')
    expect(absent).toContain('gemma4-12b-ollama')
    // present + reachable → no warning; unmeasured (null) → no warning
    expect(availabilityWarning(base)).toBe('')
    expect(availabilityWarning({ ...base, model_present: null, runtime_reachable: null })).toBe('')
    expect(availabilityWarning(undefined)).toBe('')
  })

  // --- UI-1: hosted tier with no key advises the right remedy ----------------
  it('UI-1: a hosted tier with no key advises saving a key, not restarting Ollama', () => {
    const cloud: FeatureModelAvailability = {
      feature: 'summary',
      effective_model_key: 'gemma4-31b-cloud',
      band: 'cloud',
      requires_network: true,
      runtime_reachable: false,
      model_present: null,
      detail:
        'Hosted tier selected but no provider credential is stored — this feature will defer until a key is saved.',
    }
    const warn = availabilityWarning(cloud)
    expect(warn).toContain('credential')
    expect(warn).not.toContain('Ollama')
    // frontier band, no backend detail → falls back to the key-entry message
    const frontier = availabilityWarning({ ...cloud, band: 'frontier', detail: undefined })
    expect(frontier.toLowerCase()).toContain('key')
    expect(frontier).not.toContain('Ollama')
  })
})
