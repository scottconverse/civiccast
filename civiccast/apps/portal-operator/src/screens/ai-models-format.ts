// SPDX-License-Identifier: Apache-2.0
// Copyright (c) The CivicCast Authors
//
// Plain-English labels + tones for the S13 Settings > AI Models console. Split out
// of the screen module so the screen file stays react-refresh-clean (components-only
// export) and these pure helpers are unit-testable on their own.
//
// Decisions encoded here (locked, S13 §10):
//   - cost display = $USD/token + a per-1M-token estimate (decision A);
//   - tier band carries a TEXT label (Local/Cloud/Frontier), never color alone (S20);
//   - cloud (non-private) tiers are the ones that require the TOS opt-in checkbox.

import type { FeatureModelAvailability, ModelTier } from '../types/api.generated'

export type Tone = 'neutral' | 'ok' | 'warn' | 'info'

// Fixed render order for the operator-controllable AI features.
export const FEATURE_ORDER = ['captions', 'summary', 'translation', 'audio_description'] as const
export type AiFeature = (typeof FEATURE_ORDER)[number]

const FEATURE_LABELS: Record<string, string> = {
  captions: 'Captions',
  summary: 'Summary',
  translation: 'Translation',
  audio_description: 'Audio description',
}

/** Human label for a feature; never throws on an unexpected value. */
export function featureLabel(feature: string): string {
  return FEATURE_LABELS[feature] ?? feature
}

/** A tier band — what the operator reasons about: where the model runs. faster-whisper
 *  ("external") runs ON the box, so it is Local for the privacy/cost story. */
export function tierBandLabel(tier: ModelTier | undefined): string {
  if (!tier) return 'Unknown'
  switch (tier.provider) {
    case 'ollama':
    case 'external':
      return 'Local'
    case 'ollama-cloud':
      return 'Cloud'
    case 'openrouter':
      return 'Frontier'
    default:
      return 'Unknown'
  }
}

/** Badge tone for the band: local is safe/free (ok), cloud is informational, frontier
 *  (paid 3rd-party route) is a caution. */
export function tierBandTone(tier: ModelTier | undefined): Tone {
  if (!tier) return 'neutral'
  switch (tier.provider) {
    case 'ollama':
    case 'external':
      return 'ok'
    case 'ollama-cloud':
      return 'info'
    case 'openrouter':
      return 'warn'
    default:
      return 'neutral'
  }
}

/** Whether a tier leaves the box (and so needs the cloud consent / TOS opt-in).
 *  A tier is cloud iff it is not private (cost/privacy flags are the source of truth,
 *  not the provider name). */
export function isCloudTier(tier: ModelTier | undefined): boolean {
  if (!tier) return false
  return tier.private === false
}

/** The cloud provider whose API key backs a tier — the value the
 *  `PUT /credentials/{provider}` endpoint accepts. Only the hosted providers
 *  (`ollama-cloud` / `openrouter`) have a credential; a local tier (ollama /
 *  faster-whisper "external") returns null, so the UI never offers a key field
 *  for an on-box model. */
export type CredentialProvider = 'ollama-cloud' | 'openrouter'

export function credentialProviderForTier(
  tier: ModelTier | undefined,
): CredentialProvider | null {
  if (!tier) return null
  if (tier.provider === 'ollama-cloud' || tier.provider === 'openrouter') {
    return tier.provider
  }
  return null
}

/** Format a positive USD rate with significant-figure logic so a *nonzero* rate is
 *  NEVER collapsed to "$0.0". Tiny values (below what 2 decimals can show) fall back
 *  to `toPrecision(2)`, which keeps the leading significant digits (e.g. 5e-8 →
 *  "$0.000000050"). Larger values use plain fixed-2 currency (e.g. 1.5 → "$1.50").
 *  The exponential form `toPrecision` may emit (e.g. "5.0e-8") is normalized to a
 *  plain decimal so the operator never sees scientific notation on a billing label. */
function formatUsd(value: number): string {
  if (value >= 0.01) return `$${value.toFixed(2)}`
  // 2 significant figures preserves the magnitude of sub-cent per-token rates.
  let s = value.toPrecision(2)
  if (s.includes('e') || s.includes('E')) {
    // Expand exponential to a plain decimal (Number→toFixed with enough places).
    const places = Math.max(0, -Math.floor(Math.log10(value)) + 1)
    s = value.toFixed(places + 1)
  }
  return `$${s}`
}

/** Cost label (decision A: $USD/token + a per-1M-token estimate). Local/free tiers
 *  say so plainly; metered tiers show both the per-token rate and a readable estimate,
 *  BOTH derived from the same per-token value so the two figures always agree. A
 *  nonzero rate is guaranteed never to render as "$0.0/token" or "Free" (U2). */
export function tierCostLabel(tier: ModelTier | undefined): string {
  if (!tier) return '—'
  const perToken = tier.cost_per_token_usd ?? 0
  if (perToken <= 0) return 'Free (local)'
  const perMillion = perToken * 1_000_000
  // e.g. $0.00000010/token (~$0.10 / 1M tokens) — both halves agree.
  return `${formatUsd(perToken)}/token (~${formatUsd(perMillion)} / 1M tokens)`
}

/** Latency label from a tier's p95 (U1): the operator trades latency, so show it.
 *
 *  Cloud/frontier tiers (network-bound, not local-hardware-bound) render a plain
 *  single number: sub-second as "≈900 ms typical", a second or more as "≈1.5 s
 *  typical".
 *
 *  On-box tiers (``ollama``/``external`` — real CPU-bound local inference) never
 *  render a single precise number here. Field measurement on the 32GB CPU-only
 *  reference station found the old fixed p95 figures wrong by ~30x (summary) and
 *  ~70x (captions) versus real hardware, because CPU inference time varies heavily
 *  with input length and concurrent load on the box — a false precision the
 *  operator would plan a live meeting against. Two on-box shapes need different
 *  honest phrasing:
 *   - ``external`` (faster-whisper): transcription time scales with the
 *     recording's own length, so ``latency_p95_ms`` is read as a x1000-scaled
 *     realtime multiplier (3300 -> "~3.3x") rather than a duration.
 *   - ``ollama`` (local LLM, e.g. summary): a roughly bounded per-request time
 *     that still varies a lot; rendered as a floor ("X s+") with the CPU-only
 *     caveat rather than an exact figure.
 */
export function tierLatencyLabel(tier: ModelTier | undefined): string {
  if (!tier || tier.latency_p95_ms == null) return '—'
  const ms = tier.latency_p95_ms
  if (ms <= 0) return '—'
  const isCpuBoundLocal = tier.provider === 'ollama' || tier.provider === 'external'
  if (!isCpuBoundLocal) {
    if (ms < 1000) return `≈${Math.round(ms)} ms typical`
    const seconds = ms / 1000
    const rendered = seconds >= 10 ? seconds.toFixed(0) : seconds.toFixed(1)
    return `≈${rendered} s typical`
  }
  if (tier.provider === 'external') {
    const multiple = ms / 1000
    const rendered = multiple >= 10 ? multiple.toFixed(0) : multiple.toFixed(1)
    return `~${rendered}x the recording's length, CPU-only (varies with station load)`
  }
  const seconds = Math.round(ms / 1000)
  return `${seconds} s+ on a typical CPU-only station (varies with input length)`
}

/** RAM-requirement label from a tier's `min_ram_gb` (U3). Empty string when the
 *  field is absent so callers can append it conditionally. */
export function tierRamLabel(tier: ModelTier | undefined): string {
  if (!tier || tier.min_ram_gb == null) return ''
  return `needs ${tier.min_ram_gb} GB`
}

/** Whether the box's RAM can run a tier. When `boxRamGb` is unknown (undefined) we
 *  do NOT gate — every tier stays selectable; we only annotate. */
export function tierFitsBox(tier: ModelTier | undefined, boxRamGb: number | undefined): boolean {
  if (boxRamGb == null) return true
  if (!tier || tier.min_ram_gb == null) return true
  return boxRamGb >= tier.min_ram_gb
}

/** Availability hint for a feature card (U4 / Q2 / M3, spec §6.3). Returns a TEXT
 *  warning (color is never the only signal, S20) when the effective model is not
 *  loadable — either the runtime is unreachable or the model is not installed —
 *  otherwise an empty string (no badge). */
export function availabilityWarning(av: FeatureModelAvailability | undefined): string {
  if (!av) return ''
  // A null/undefined probe result means "not measured" — do not warn.
  const reachable = av.runtime_reachable
  const present = av.model_present
  const isHosted = av.band === 'cloud' || av.band === 'frontier'
  if (reachable === false) {
    // Hosted tiers set runtime_reachable=false when NO provider key is stored —
    // the remedy is to save an API key, not to restart Ollama. Prefer the
    // backend's band-aware detail; fall back to the key-entry message.
    if (isHosted) {
      return (
        av.detail ??
        'Hosted tier selected but no provider credential is stored — feature will defer until a key is saved.'
      )
    }
    return 'Ollama unavailable — feature will defer until the runtime is back.'
  }
  if (present === false) {
    return `Not installed — “${av.effective_model_key}” is not on this box; feature will defer. Run the installer model step or pull the model, then retry.`
  }
  return ''
}

/** Privacy label: on-device vs sent to a cloud provider, noting network dependence. */
export function privacyLabel(tier: ModelTier | undefined): string {
  if (!tier) return '—'
  if (tier.private === false) {
    return tier.requires_network
      ? 'Sent to cloud provider — network required'
      : 'Sent to cloud provider'
  }
  return 'On-device — private'
}
