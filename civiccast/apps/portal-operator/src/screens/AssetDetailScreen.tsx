import { EmbedCopyPanel } from './EmbedCopyPanel'
import { useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  ApiError,
  getStaffAsset,
  getStaffIdentity,
  listStaffAssets,
  unpublishStaffAsset,
  updateStaffAsset,
} from '../api/client'
import type {
  AssetMetadataUpdate,
  AssetRow,
  RetentionPolicy,
  RetentionTermUnit,
} from '../types/asset'
import type { StaffIdentityResponse } from '../types/api.generated'
import { ConfirmDialog } from '../components/ConfirmDialog'
import { RadioCardGroup } from '../components/RadioCardGroup'
import { AssetCustomFieldsEditor } from './AssetCustomFieldsEditor'
import { OfflineCaptionJobsPanel } from './OfflineCaptionJobsPanel'
import { AudioDescriptionPanel } from './AudioDescriptionPanel'
import { GenerateSummaryPanel } from './GenerateSummaryPanel'
import { MediaLifecyclePanel } from './MediaLifecyclePanel'
import { hasRole } from './contribution-format'

// Spec §4: meeting_operator / records_clerk / setup_admin may set custom-field values.
const CUSTOM_FIELD_WRITE_ROLES = ['setup_admin', 'meeting_operator', 'records_clerk']

interface Props {
  assetId: string
  onClose: () => void
  onEditTrim: (assetId: string) => void
}

const RETENTION_OPTIONS: ReadonlyArray<{
  id: RetentionPolicy
  label: string
  description: string
}> = [
  {
    id: 'default',
    label: 'Default',
    description: 'Use the channel default or selected state preset.',
  },
  {
    id: 'permanent',
    label: 'Permanent',
    description: 'Held indefinitely; never flagged for disposition.',
  },
  {
    id: 'meeting',
    label: 'Meeting (long)',
    description: 'Retained per public-meeting rules.',
  },
  {
    id: 'short',
    label: 'Short',
    // Honest copy (Stage F decision): nothing auto-purges. Expired assets
    // are flagged into the records-clerk disposition queue for review.
    description: 'Brief retention window; flagged for records review at expiry.',
  },
]

// WP-08: value/unit/forever retention-term authoring, additive to the
// legacy RETENTION_OPTIONS radio group above (unchanged; still the only
// contract a never-converted "legacy" row has). Order matters for the
// <select>: shortest-lived to longest-lived, forever last.
const RETENTION_TERM_UNIT_OPTIONS: ReadonlyArray<{
  id: RetentionTermUnit
  label: string
}> = [
  { id: 'days', label: 'Day(s)' },
  { id: 'weeks', label: 'Week(s)' },
  { id: 'months', label: 'Month(s)' },
  { id: 'years', label: 'Year(s)' },
  { id: 'forever', label: 'Forever' },
]

// Legacy-conversion prefill suggestions (finalization plan section 6, item
// 8: "Present existing state presets as prefilled value/unit suggestions,
// not as an external legal decision.") -- display-only hints offered when
// an operator starts converting a never-authored legacy row; nothing here
// is written until the operator submits. Mirrors
// civiccast.schedule.retention_terms's LEGACY_SHORT_SUGGESTED_* constants
// and the "permanent -> forever" migration backfill. `default`/`meeting`
// are deliberately absent -- the plan forbids inventing a duration for
// those, so the operator starts from a blank term.
const LEGACY_CONVERSION_SUGGESTIONS: Partial<
  Record<RetentionPolicy, { unit: RetentionTermUnit; value: number | null }>
> = {
  permanent: { unit: 'forever', value: null },
  short: { unit: 'days', value: 30 },
}

function fmtSize(bytes: number | null): string {
  if (bytes == null) return '—'
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 ** 2) return `${(bytes / 1024).toFixed(1)} KB`
  if (bytes < 1024 ** 3) return `${(bytes / 1024 / 1024).toFixed(1)} MB`
  return `${(bytes / 1024 / 1024 / 1024).toFixed(2)} GB`
}

function fmtDuration(seconds: number | null): string {
  if (seconds == null) return '—'
  const h = Math.floor(seconds / 3600)
  const m = Math.floor((seconds % 3600) / 60)
  const s = Math.floor(seconds % 60)
  if (h > 0)
    return `${h}:${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`
  return `${m}:${String(s).padStart(2, '0')}`
}

function fmtDate(iso: string | null): string {
  if (iso == null) return '—'
  return new Date(iso).toLocaleString(undefined, {
    month: 'short',
    day: 'numeric',
    year: 'numeric',
    hour: 'numeric',
    minute: '2-digit',
  })
}

function isoToLocalInput(iso: string | null): string {
  if (!iso) return ''
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return ''
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`
}

function localInputToIso(local: string): string | null {
  if (!local) return null
  const d = new Date(local)
  if (Number.isNaN(d.getTime())) return null
  return d.toISOString()
}

function LoadingState() {
  return (
    <div className="px-6 py-10 text-sm" style={{ color: 'var(--cc-ink-2)' }}>
      Loading asset…
    </div>
  )
}

function ErrorState({
  error,
  onClose,
}: {
  error: Error
  onClose: () => void
}) {
  const isApi = error instanceof ApiError
  return (
    <div className="flex flex-col items-start gap-3 px-6 py-6">
      <div
        role="alert"
        className="max-w-lg rounded-md p-4 text-sm"
        style={{ background: 'var(--cc-err-soft)', color: 'var(--cc-err)' }}
      >
        <div className="font-semibold">Could not load asset.</div>
        <div className="mt-1 text-xs" style={{ color: 'var(--cc-ink-2)' }}>
          {isApi && error.detail ? error.detail : error.message}
        </div>
      </div>
      <button
        type="button"
        onClick={onClose}
        className="rounded-md px-3 py-1.5 text-xs font-medium"
        style={{
          border: '1px solid var(--cc-line)',
          color: 'var(--cc-ink-2)',
        }}
      >
        Back to assets
      </button>
    </div>
  )
}

interface FormState {
  title: string
  description: string
  meeting_body: string
  retention_policy: RetentionPolicy
  retention_until_local: string
  // WP-08: 'legacy' edits the pre-existing retention_policy/retention_until
  // pair above; 'term' authors the new value/unit/forever contract. A row
  // that has already been converted (asset.retention_term_unit is set) is
  // always in 'term' mode -- there is no "convert back" operation.
  retentionMode: 'legacy' | 'term'
  retentionTermUnit: RetentionTermUnit
  // Kept as a string so the number input can hold '' while the operator is
  // mid-edit; validated/parsed at submit time.
  retentionTermValueText: string
}

function initialFormState(asset: AssetRow): FormState {
  const alreadyAuthored = asset.retention_term_unit != null
  return {
    title: asset.title,
    description: asset.description ?? '',
    meeting_body: asset.meeting_body ?? '',
    retention_policy: asset.retention_policy,
    retention_until_local: isoToLocalInput(asset.retention_until),
    retentionMode: alreadyAuthored ? 'term' : 'legacy',
    retentionTermUnit: asset.retention_term_unit ?? 'days',
    retentionTermValueText:
      asset.retention_term_value != null ? String(asset.retention_term_value) : '',
  }
}

function diffPatch(
  asset: AssetRow,
  form: FormState,
): AssetMetadataUpdate | null {
  // QA-008 (audit-team v0.3.0): expected_version is always echoed back
  // from the asset's current version. Set it eagerly so the patch object
  // is well-formed even when no other fields changed (the changed flag
  // gates the actual return).
  const patch: AssetMetadataUpdate = { expected_version: asset.version }
  let changed = false
  if (form.title.trim() !== asset.title) {
    patch.title = form.title.trim()
    changed = true
  }
  const desc = form.description.trim()
  if ((desc || null) !== (asset.description ?? null)) {
    patch.description = desc || null
    changed = true
  }
  const body = form.meeting_body.trim()
  if ((body || null) !== (asset.meeting_body ?? null)) {
    patch.meeting_body = body || null
    changed = true
  }
  if (form.retentionMode === 'term') {
    // WP-08: value/unit/forever authoring. Mutually exclusive with
    // retention_policy/retention_until server-side (AssetMetadataUpdate
    // rejects mixing them in one PATCH), so this branch never sets those.
    const value =
      form.retentionTermUnit === 'forever'
        ? null
        : form.retentionTermValueText.trim() === ''
          ? null
          : Number(form.retentionTermValueText)
    const unitChanged = form.retentionTermUnit !== (asset.retention_term_unit ?? null)
    const valueChanged = value !== (asset.retention_term_value ?? null)
    if (unitChanged || valueChanged) {
      patch.retention_term_unit = form.retentionTermUnit
      if (form.retentionTermUnit !== 'forever') {
        patch.retention_term_value = value ?? undefined
      }
      changed = true
    }
  } else {
    if (form.retention_policy !== asset.retention_policy) {
      patch.retention_policy = form.retention_policy
      changed = true
    }
    const newUntil = localInputToIso(form.retention_until_local)
    const cur = asset.retention_until
    // Compare as ms-truncated strings — both browsers and JSON serializers
    // normalize to ms precision, so a same-instant value with different
    // textual form (Z vs +00:00) is still equal.
    const sameInstant =
      (newUntil == null && cur == null) ||
      (newUntil != null &&
        cur != null &&
        new Date(newUntil).getTime() === new Date(cur).getTime())
    if (!sameInstant) {
      patch.retention_until = newUntil
      changed = true
    }
  }
  return changed ? patch : null
}

function validate(form: FormState): string | null {
  const title = form.title.trim()
  if (title.length < 1) return 'Title cannot be empty.'
  if (title.length > 200) return 'Title must be 200 characters or fewer.'
  if (form.description.length > 2000)
    return 'Description must be 2000 characters or fewer.'
  if (form.meeting_body.trim().length > 120)
    return 'Meeting body must be 120 characters or fewer.'
  if (form.retentionMode === 'term' && form.retentionTermUnit !== 'forever') {
    const trimmed = form.retentionTermValueText.trim()
    if (trimmed === '') return 'Enter how many days/weeks/months/years to retain this recording.'
    const n = Number(trimmed)
    if (!Number.isInteger(n) || n <= 0)
      return 'Retention length must be a whole number greater than zero.'
  }
  return null
}

interface DetailEditorProps {
  asset: AssetRow
  onClose: () => void
  onEditTrim: (assetId: string) => void
}

function DetailEditor({ asset, onClose, onEditTrim }: DetailEditorProps) {
  const [form, setForm] = useState<FormState>(() => initialFormState(asset))
  const [submitError, setSubmitError] = useState<string | null>(null)
  const queryClient = useQueryClient()

  // Custom-field value writes are role-gated (spec §4); read-only operators see
  // the inputs disabled, not hidden. A failed identity probe never blanks the
  // editor — it just leaves the custom-field inputs read-only.
  const identityQuery = useQuery<StaffIdentityResponse>({
    queryKey: ['staff-identity'],
    queryFn: getStaffIdentity,
    retry: false,
  })
  const canWriteCustomFields = hasRole(identityQuery.data, CUSTOM_FIELD_WRITE_ROLES)

  // Audit UX-002: suggest the meeting bodies already in use so operator
  // typos don't fork the resident facet ("City Council" vs "city council").
  const assetList = useQuery({ queryKey: ['staff-assets'], queryFn: listStaffAssets })
  const knownBodies = useMemo(() => {
    const seen = new Set<string>()
    for (const row of assetList.data ?? []) {
      if (row.meeting_body) seen.add(row.meeting_body)
    }
    return [...seen].sort((a, b) => a.localeCompare(b))
  }, [assetList.data])

  // Form state self-converges with the asset: after a successful PATCH the
  // server's response invalidates the query, the asset prop re-renders with
  // the new values, and diffPatch returns null — so dirty flips to false
  // automatically. Resetting the form in an effect would be redundant and
  // would also clobber unrelated user edits made while a save is in flight.
  const patch = useMemo(() => diffPatch(asset, form), [asset, form])
  const dirty = patch !== null
  const validationError = validate(form)

  const mutation = useMutation<AssetRow, Error, AssetMetadataUpdate>({
    mutationFn: (p) => updateStaffAsset(asset.asset_id, p),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['staff-assets'] })
      void queryClient.invalidateQueries({
        queryKey: ['staff-asset', asset.asset_id],
      })
    },
    onError: (err) => {
      setSubmitError(
        err instanceof ApiError && err.detail ? err.detail : err.message,
      )
    },
  })

  const submitDisabled = !dirty || !!validationError || mutation.isPending

  const handleSave = () => {
    setSubmitError(null)
    if (!patch) return
    if (validationError) {
      setSubmitError(validationError)
      return
    }
    mutation.mutate(patch)
  }

  // "Delete it like any other asset" -- the A-1 first-run seeded sample's
  // own description promises this, but no removal or unpublish path
  // existed anywhere in the console before this (Codex review, PR #419).
  // Scoped to Portal visibility only: clears published_at so the asset
  // stops appearing on the public portal. It does not delete the asset
  // row, its media, or attempt to reverse IA/YouTube/ActivityPub delivery
  // -- those are independent peer surfaces under the three-tier publish
  // model (spec Sec 2.6), out of scope here.
  const [unpublishError, setUnpublishError] = useState<string | null>(null)
  const unpublishMutation = useMutation<AssetRow, Error, void>({
    mutationFn: () => unpublishStaffAsset(asset.asset_id),
    onSuccess: () => {
      setUnpublishError(null)
      void queryClient.invalidateQueries({ queryKey: ['staff-assets'] })
      void queryClient.invalidateQueries({ queryKey: ['publish-assets'] })
      void queryClient.invalidateQueries({
        queryKey: ['staff-asset', asset.asset_id],
      })
    },
    onError: (err) => {
      setUnpublishError(
        err instanceof ApiError && err.detail ? err.detail : err.message,
      )
    },
  })

  // Unpublishing removes a public-record recording from residents' view --
  // the shared ConfirmDialog (WCAG-compliant focus trap, Escape-to-cancel,
  // consistent copy voice) replaces the native window.confirm() this used to
  // use, matching every other destructive action in the console.
  const [confirmingUnpublish, setConfirmingUnpublish] = useState(false)

  const handleUnpublish = () => {
    setConfirmingUnpublish(true)
  }

  return (
    <div className="flex flex-col">
      <header className="px-4 pb-4 pt-6 sm:px-6">
        <button
          type="button"
          onClick={onClose}
          className="mb-3 rounded-md px-2 py-1 text-xs"
          style={{
            border: '1px solid var(--cc-line)',
            color: 'var(--cc-ink-2)',
          }}
          aria-label="Back to assets"
        >
          ← Back
        </button>
        <div
          className="text-[10px] font-semibold uppercase tracking-wider"
          style={{ color: 'var(--cc-ink-3)' }}
        >
          Library · asset detail
        </div>
        <h1 className="m-0 text-2xl font-semibold tracking-tight">
          {asset.title}
        </h1>
        <div className="cc-mono mt-1 text-xs" style={{ color: 'var(--cc-ink-3)' }}>
          {asset.asset_id}
        </div>
      </header>

      <div className="grid gap-6 px-4 pb-6 sm:px-6 lg:grid-cols-[2fr_1fr]">
        <section
          aria-label="Metadata"
          className="flex flex-col gap-4 rounded-md p-4"
          style={{
            background: 'var(--cc-surface)',
            border: '1px solid var(--cc-line)',
          }}
        >
          <h2
            className="m-0 text-sm font-semibold"
            style={{ color: 'var(--cc-ink)' }}
          >
            Public metadata
          </h2>

          <label className="block">
            <span
              className="mb-1 block text-[11px] font-semibold uppercase tracking-wider"
              style={{ color: 'var(--cc-ink-3)' }}
            >
              Title
            </span>
            <input
              type="text"
              value={form.title}
              maxLength={200}
              onChange={(e) =>
                setForm((f) => ({ ...f, title: e.target.value }))
              }
              className="w-full rounded-md px-3 py-2 text-sm"
              style={{
                background: 'var(--cc-surface)',
                border: '1px solid var(--cc-line)',
                color: 'var(--cc-ink)',
              }}
            />
            <div
              className="cc-mono mt-1 text-[10px]"
              style={{ color: 'var(--cc-ink-3)' }}
            >
              {form.title.length}/200 · shown to residents on the portal
            </div>
          </label>

          <label className="block">
            <span
              className="mb-1 block text-[11px] font-semibold uppercase tracking-wider"
              style={{ color: 'var(--cc-ink-3)' }}
            >
              Description (optional)
            </span>
            <textarea
              value={form.description}
              maxLength={2000}
              rows={4}
              onChange={(e) =>
                setForm((f) => ({ ...f, description: e.target.value }))
              }
              className="w-full rounded-md px-3 py-2 text-sm"
              style={{
                background: 'var(--cc-surface)',
                border: '1px solid var(--cc-line)',
                color: 'var(--cc-ink)',
              }}
            />
            <div
              className="cc-mono mt-1 text-[10px]"
              style={{ color: 'var(--cc-ink-3)' }}
            >
              {form.description.length}/2000
            </div>
          </label>

          <label className="block">
            <span
              className="mb-1 block text-[11px] font-semibold uppercase tracking-wider"
              style={{ color: 'var(--cc-ink-3)' }}
            >
              Meeting body (optional)
            </span>
            <input
              type="text"
              value={form.meeting_body}
              maxLength={120}
              list="meeting-body-options"
              placeholder="e.g. City Council"
              onChange={(e) =>
                setForm((f) => ({ ...f, meeting_body: e.target.value }))
              }
              className="w-full rounded-md px-3 py-2 text-sm"
              style={{
                background: 'var(--cc-surface)',
                border: '1px solid var(--cc-line)',
                color: 'var(--cc-ink)',
              }}
            />
            <datalist id="meeting-body-options">
              {knownBodies.map((value) => (
                <option key={value} value={value} />
              ))}
            </datalist>
            <div
              className="cc-mono mt-1 text-[10px]"
              style={{ color: 'var(--cc-ink-3)' }}
            >
              {form.meeting_body.length}/120 · blank = untagged. Residents
              browse by this on the portal — pick a suggestion to reuse the
              exact spelling of a body already in use.
            </div>
          </label>

          {form.retentionMode === 'legacy' ? (
            <>
              <fieldset className="m-0 border-0 p-0">
                <legend
                  className="mb-2 block text-[10px] font-semibold uppercase tracking-wider"
                  style={{ color: 'var(--cc-ink-3)' }}
                >
                  Retention policy
                </legend>
                <RadioCardGroup
                  label="Retention policy"
                  options={RETENTION_OPTIONS.map((opt) => ({
                    id: opt.id,
                    label: opt.label,
                    description: opt.description,
                  }))}
                  value={form.retention_policy}
                  onChange={(retentionPolicy) =>
                    setForm((f) => ({ ...f, retention_policy: retentionPolicy }))
                  }
                  className="grid gap-2 sm:grid-cols-2"
                />
                <div
                  className="mt-2 rounded-md p-2 text-[11px]"
                  style={{ background: 'var(--cc-surface-2)', color: 'var(--cc-ink-2)' }}
                >
                  <strong>Records officer review required.</strong> State presets
                  provide a starting point, but local schedules, litigation holds,
                  and official-minutes rules can require longer retention.
                </div>
              </fieldset>

              <label className="block">
                <span
                  className="mb-1 block text-[11px] font-semibold uppercase tracking-wider"
                  style={{ color: 'var(--cc-ink-3)' }}
                >
                  Retention deadline (optional)
                </span>
                <input
                  type="datetime-local"
                  value={form.retention_until_local}
                  onChange={(e) =>
                    setForm((f) => ({
                      ...f,
                      retention_until_local: e.target.value,
                    }))
                  }
                  className="cc-mono w-full rounded-md px-3 py-2 text-sm"
                  style={{
                    background: 'var(--cc-surface)',
                    border: '1px solid var(--cc-line)',
                    color: 'var(--cc-ink)',
                  }}
                />
                <div
                  className="cc-mono mt-1 text-[10px]"
                  style={{ color: 'var(--cc-ink-3)' }}
                >
                  {form.retention_until_local
                    ? `Set: ${form.retention_until_local}`
                    : 'Leave blank to defer to the policy default.'}
                </div>
              </label>

              <button
                type="button"
                onClick={() => {
                  const suggestion = LEGACY_CONVERSION_SUGGESTIONS[asset.retention_policy]
                  setForm((f) => ({
                    ...f,
                    retentionMode: 'term',
                    retentionTermUnit: suggestion?.unit ?? 'days',
                    retentionTermValueText:
                      suggestion?.value != null ? String(suggestion.value) : '',
                  }))
                }}
                className="self-start rounded-md px-3 py-1.5 text-xs font-medium"
                style={{ border: '1px solid var(--cc-line)', color: 'var(--cc-ink-2)' }}
              >
                Convert to a length + unit or forever term...
              </button>
            </>
          ) : (
            <fieldset className="m-0 flex flex-col gap-2 border-0 p-0">
              <legend
                className="mb-1 block text-[10px] font-semibold uppercase tracking-wider"
                style={{ color: 'var(--cc-ink-3)' }}
              >
                Retention term
              </legend>
              <div className="flex flex-wrap items-center gap-2">
                {form.retentionTermUnit !== 'forever' && (
                  <input
                    type="number"
                    min={1}
                    step={1}
                    inputMode="numeric"
                    aria-label="Retention length"
                    value={form.retentionTermValueText}
                    onChange={(e) =>
                      setForm((f) => ({ ...f, retentionTermValueText: e.target.value }))
                    }
                    className="cc-mono w-24 rounded-md px-3 py-2 text-sm"
                    style={{
                      background: 'var(--cc-surface)',
                      border: '1px solid var(--cc-line)',
                      color: 'var(--cc-ink)',
                    }}
                  />
                )}
                <select
                  aria-label="Retention unit"
                  value={form.retentionTermUnit}
                  onChange={(e) =>
                    setForm((f) => ({
                      ...f,
                      retentionTermUnit: e.target.value as RetentionTermUnit,
                    }))
                  }
                  className="rounded-md px-3 py-2 text-sm"
                  style={{
                    background: 'var(--cc-surface)',
                    border: '1px solid var(--cc-line)',
                    color: 'var(--cc-ink)',
                  }}
                >
                  {RETENTION_TERM_UNIT_OPTIONS.map((opt) => (
                    <option key={opt.id} value={opt.id}>
                      {opt.label}
                    </option>
                  ))}
                </select>
              </div>
              <div
                className="rounded-md p-2 text-[11px]"
                style={{ background: 'var(--cc-surface-2)', color: 'var(--cc-ink-2)' }}
              >
                {form.retentionTermUnit === 'forever' ? (
                  <>Held indefinitely; never flagged for disposition review.</>
                ) : (
                  <>
                    Deletion is never automatic. When this term's deadline passes, the
                    asset is flagged into the records-clerk disposition review queue --
                    it stays in place until a clerk acts.
                  </>
                )}
                {asset.retention_anchor_at ? (
                  <>
                    {' '}
                    Counted from this recording's first publication:{' '}
                    {fmtDate(asset.retention_anchor_at)}.
                  </>
                ) : (
                  <>
                    {' '}
                    This recording has never been published -- saving will anchor the
                    term to the moment you save, not to a future publish date.
                  </>
                )}
              </div>
              {asset.retention_until && asset.retention_term_unit && (
                <div className="cc-mono text-[10px]" style={{ color: 'var(--cc-ink-3)' }}>
                  Current computed deadline: {fmtDate(asset.retention_until)}
                </div>
              )}
              {!(asset.retention_term_unit != null) && (
                <button
                  type="button"
                  onClick={() => setForm((f) => ({ ...f, retentionMode: 'legacy' }))}
                  className="self-start rounded-md px-3 py-1.5 text-xs font-medium"
                  style={{ border: '1px solid var(--cc-line)', color: 'var(--cc-ink-2)' }}
                >
                  Cancel conversion
                </button>
              )}
            </fieldset>
          )}

          {submitError && (
            <div
              role="alert"
              className="rounded-md p-3 text-xs"
              style={{
                background: 'var(--cc-err-soft)',
                color: 'var(--cc-err)',
              }}
            >
              <strong>Save failed.</strong>{' '}
              <span style={{ color: 'var(--cc-ink-2)' }}>{submitError}</span>
            </div>
          )}

          {validationError && !submitError && (
            <div
              role="alert"
              className="rounded-md p-3 text-xs"
              style={{
                background: 'var(--cc-warn-soft)',
                color: 'var(--cc-ink)',
                border: '1px solid var(--cc-line)',
              }}
            >
              <strong>Fix before saving.</strong>{' '}
              <span style={{ color: 'var(--cc-ink-2)' }}>
                {validationError}
              </span>
            </div>
          )}

          <div className="flex flex-wrap items-center gap-2 pt-2">
            <button
              type="button"
              onClick={() => setForm(initialFormState(asset))}
              disabled={!dirty || mutation.isPending}
              className="rounded-md px-3 py-1.5 text-xs font-medium"
              style={{
                border: '1px solid var(--cc-line)',
                color: dirty ? 'var(--cc-ink-2)' : 'var(--cc-ink-3)',
                cursor: dirty ? 'pointer' : 'not-allowed',
                background: 'var(--cc-surface)',
              }}
            >
              Reset
            </button>
            <button
              type="button"
              onClick={handleSave}
              disabled={submitDisabled}
              className="rounded-md px-3 py-1.5 text-xs font-semibold"
              style={{
                background: submitDisabled
                  ? 'var(--cc-surface-3)'
                  : 'var(--cc-brand)',
                color: submitDisabled
                  ? 'var(--cc-ink-3)'
                  : 'var(--cc-brand-ink)',
                cursor: submitDisabled ? 'not-allowed' : 'pointer',
              }}
            >
              {mutation.isPending
                ? 'Saving…'
                : dirty
                  ? 'Save metadata'
                  : 'Saved'}
            </button>
            {mutation.isSuccess && !dirty && (
              <span
                className="text-[11px]"
                style={{ color: 'var(--cc-ok)' }}
              >
                ✓ Saved.
              </span>
            )}
          </div>

          {/* S22: operator-defined custom fields. A separate full-replace
              round-trip (PUT /custom-fields) from the core metadata PATCH above,
              so the OCC/diff flow stays untouched and absence is always valid. */}
          <AssetCustomFieldsEditor
            assetId={asset.asset_id}
            canWrite={canWriteCustomFields}
          />
        </section>

        <aside
          aria-label="Technical metadata"
          className="flex flex-col gap-3 rounded-md p-4"
          style={{
            background: 'var(--cc-surface)',
            border: '1px solid var(--cc-line)',
          }}
        >
          <h2
            className="m-0 text-sm font-semibold"
            style={{ color: 'var(--cc-ink)' }}
          >
            Technical
          </h2>
          <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-2 text-xs">
            <dt style={{ color: 'var(--cc-ink-3)' }}>State</dt>
            <dd className="m-0 font-medium">{asset.state}</dd>
            <dt style={{ color: 'var(--cc-ink-3)' }}>Duration</dt>
            <dd className="m-0 cc-mono cc-tabular">{fmtDuration(asset.duration_seconds)}</dd>
            <dt style={{ color: 'var(--cc-ink-3)' }}>Size</dt>
            <dd className="m-0 cc-mono cc-tabular">{fmtSize(asset.file_size_bytes)}</dd>
            <dt style={{ color: 'var(--cc-ink-3)' }}>Codec</dt>
            <dd className="m-0">
              {asset.codec_video ?? '—'}
              {asset.codec_audio ? (
                <>
                  {' · '}
                  <span style={{ color: 'var(--cc-ink-3)' }}>{asset.codec_audio}</span>
                </>
              ) : null}
            </dd>
            <dt style={{ color: 'var(--cc-ink-3)' }}>Resolution</dt>
            <dd className="m-0 cc-mono cc-tabular">
              {asset.width_px && asset.height_px
                ? `${asset.width_px}×${asset.height_px}`
                : '—'}
            </dd>
            <dt style={{ color: 'var(--cc-ink-3)' }}>Format</dt>
            <dd className="m-0 cc-mono">{asset.format_name ?? '—'}</dd>
            <dt style={{ color: 'var(--cc-ink-3)' }}>Published</dt>
            <dd className="m-0">
              {fmtDate(asset.published_at)}
              {asset.published_at && (
                <>
                  {' · '}
                  <button
                    type="button"
                    onClick={handleUnpublish}
                    disabled={unpublishMutation.isPending}
                    className="text-[11px] font-medium underline"
                    style={{
                      color: unpublishMutation.isPending ? 'var(--cc-ink-3)' : 'var(--cc-err)',
                      cursor: unpublishMutation.isPending ? 'not-allowed' : 'pointer',
                    }}
                  >
                    {unpublishMutation.isPending ? 'Removing…' : 'Remove from portal'}
                  </button>
                </>
              )}
            </dd>
            {unpublishError && (
              <>
                <dt className="sr-only">Remove from portal error</dt>
                <dd className="m-0 text-[11px]" role="alert" style={{ color: 'var(--cc-err)' }}>
                  Could not remove from the portal: {unpublishError}
                </dd>
              </>
            )}
            <dt style={{ color: 'var(--cc-ink-3)' }}>Manifest</dt>
            <dd className="m-0">
              {asset.manifest_url ? (
                <span className="cc-mono cc-truncate text-[11px]" style={{ color: 'var(--cc-ok)' }}>
                  {asset.manifest_url}
                </span>
              ) : (
                <span style={{ color: 'var(--cc-ink-3)' }}>
                  {asset.state === 'recorded'
                    ? 'Not yet servable. Set a public manifest URL in Setup to publish live recordings.'
                    : 'Not packaged yet.'}
                </span>
              )}
            </dd>
            <dt style={{ color: 'var(--cc-ink-3)' }}>Trim window</dt>
            <dd className="m-0 cc-mono cc-tabular">
              {asset.trim_in_seconds != null && asset.trim_out_seconds != null
                ? `${fmtDuration(asset.trim_in_seconds)} – ${fmtDuration(asset.trim_out_seconds)}`
                : 'Full duration'}
            </dd>
            <dt style={{ color: 'var(--cc-ink-3)' }}>Chapters</dt>
            <dd className="m-0">{asset.chapters.length}</dd>
          </dl>

          <div className="mt-2 flex flex-col gap-2">
            {(() => {
              // Beta B3: live recordings are trimmable — the finalization
              // worker re-renders the published package when trim changes.
              const trimmable =
                asset.state === 'validated' || asset.state === 'recorded'
              return (
                <button
                  type="button"
                  onClick={() => onEditTrim(asset.asset_id)}
                  disabled={!trimmable}
                  className="rounded-md px-3 py-2 text-xs font-medium"
                  style={{
                    background: trimmable ? 'var(--cc-brand)' : 'var(--cc-surface-3)',
                    color: trimmable ? 'var(--cc-brand-ink)' : 'var(--cc-ink-3)',
                    cursor: trimmable ? 'pointer' : 'not-allowed',
                  }}
                >
                  Edit trim &amp; chapters
                </button>
              )
            })()}
            <span
              className="text-[10px]"
              style={{ color: 'var(--cc-ink-3)' }}
            >
              {asset.state === 'validated'
                ? 'Trim and chapters apply at packaging time.'
                : asset.state === 'recorded'
                  ? 'Saving a trim re-renders the published recording automatically.'
                  : 'Trim editor opens once ingest is validated.'}
            </span>
          </div>

          <MediaLifecyclePanel assetId={asset.asset_id} />
        </aside>
      </div>

      <div className="grid gap-4 px-4 pb-6 sm:px-6">
        {asset.published_at && <EmbedCopyPanel key={asset.asset_id} kind="asset" identity={asset.asset_id} />}
        <OfflineCaptionJobsPanel assetId={asset.asset_id} />
        <GenerateSummaryPanel assetId={asset.asset_id} />
        <AudioDescriptionPanel assetId={asset.asset_id} />
      </div>

      {confirmingUnpublish && (
        <ConfirmDialog
          title={`Remove "${asset.title}" from the public portal?`}
          body="Residents will no longer be able to view or find it there. This only affects portal visibility -- the asset row, its media, and the Internet Archive / syndication copies are untouched."
          confirmLabel="Remove from portal"
          tone="danger"
          busy={unpublishMutation.isPending}
          onConfirm={() => {
            setConfirmingUnpublish(false)
            unpublishMutation.mutate()
          }}
          onCancel={() => setConfirmingUnpublish(false)}
        />
      )}
    </div>
  )
}

export function AssetDetailScreen({ assetId, onClose, onEditTrim }: Props) {
  const query = useQuery<AssetRow, Error>({
    queryKey: ['staff-asset', assetId],
    queryFn: () => getStaffAsset(assetId),
    retry: false,
  })

  if (query.isLoading) return <LoadingState />
  if (query.isError) return <ErrorState error={query.error} onClose={onClose} />
  if (!query.data) return <ErrorState error={new Error('No data')} onClose={onClose} />
  // Keying on asset_id resets the editor's local form state when the user
  // navigates from one asset detail to another. After a save → refetch the
  // same asset_id stays mounted and the form self-converges via diffPatch.
  return (
    <DetailEditor
      key={query.data.asset_id}
      asset={query.data}
      onClose={onClose}
      onEditTrim={onEditTrim}
    />
  )
}
