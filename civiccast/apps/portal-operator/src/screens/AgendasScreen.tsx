import { AgendaMinutesPanel } from './AgendaMinutesPanel'
import { AgendaSourceSetup } from './AgendaSourceSetup'
import { agendaSettingsKey, getAgendaSourceSettings } from '../api/agenda-settings'
// SPDX-License-Identifier: Apache-2.0
// Copyright (c) The CivicCast Authors
//
// S25 Operator console: Meeting agenda editor (slice 4).
//
// One screen with three stacked sections:
//
//   A. Agenda picker / metadata — dropdown of all agendas + a create form;
//      the selected agenda card shows status badge, source doc link, the
//      publish/unpublish button (publish refuses on zero items per DC-1 with a
//      surfaced 422), and a two-step delete confirm with the cascade warning.
//
//   B. Items use HH:MM:SS on the explicitly labeled playback/source clock.
//      Active capture jobs expose a measured-media mark action. Their durable
//      association follows the content-derived final asset; published source
//      markers are mapped through the immutable package range.
//
//   C. Bulk actions — "Sync from chapters" + a plain-text agenda import. The
//      PDF / DOCX path returns 415 from the server; we surface that as a
//      clear "plain text only today; PDF is a follow-up" banner.
//
// Role gate: records_clerk OR meeting_operator (matches the backend _AUTHOR
// set in civiccast/agenda/router.py).

import { useEffect, useId, useMemo, useRef, useState, type CSSProperties, type ReactNode } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { AuthRequiredState } from '../components/AuthRequiredState'

import {
  AGENDA_EXTERNAL_SOURCES,
  ApiError,
  createAgendaItem,
  createMeetingAgenda,
  deleteAgendaItem,
  deleteMeetingAgenda,
  getJsPortalPosture,
  getStaffIdentity,
  importAgendaFromDoc,
  importExternalAgenda,
  startAgendaImportPreview,
  getAgendaImportPreview,
  resumeAgendaImportPreview,
  cancelAgendaImportPreview,
  confirmAgendaImportPreview,
  type AgendaImportPreview,
  staffApiBlobRequest,
  listAgendaItems,
  listAgendaMeetings,
  markAgendaItemNow,
  type AgendaMeetingOption,
  listExternalAgendaMeetings,
  listMeetingAgendas,
  patchAgendaItem,
  patchMeetingAgenda,
  syncAgendaFromChapters,
  type AgendaExternalSource,
} from '../api/client'
import type {
  AgendaImportExternalRequest,
  AgendaItem,
  AgendaItemInput,
  AgendaItemUpdate,
  ExternalMeetingSummary,
  MeetingAgenda,
  MeetingAgendaInput,
  StaffIdentityResponse,
} from '../types/api.generated'
import { formatTimecode, parseAgendaTime } from './agendas-format'
import { hasRole } from './contribution-format'
import { EmptyState } from '../components/EmptyState'

// Spec §4 / router _AUTHOR — records_clerk or meeting_operator may CRUD,
// publish, sync, and import.
const AUTHOR_ROLES = ['records_clerk', 'meeting_operator']
const DEFAULT_STATION_ID = 'civiccast-station'

// civiccast/agenda_import/ (Agenda Bridge) vendor labels + js_portal vendor
// hints — human copy for the External import section below.
const EXTERNAL_SOURCE_LABELS: Record<AgendaExternalSource, string> = {
  legistar: 'Legistar',
  primegov: 'PrimeGov',
  civicclerk: 'CivicClerk',
  js_portal: 'JS-rendered portal (CivicPlus, Granicus, other)',
  townlight: 'Townlight shared importer',
}

const JS_PORTAL_VENDOR_HINTS = [
  { value: 'generic', label: 'Generic / unknown' },
  { value: 'civicplus', label: 'CivicPlus (AgendaCenter)' },
  { value: 'granicus', label: 'Granicus' },
  { value: 'legistar_js', label: 'Legistar (JS-rendered public page)' },
  { value: 'primegov_js', label: 'PrimeGov (JS-rendered public page)' },
]

/**
 * Build the public-portal "watch this meeting" URL for a given asset id, so
 * the UX-1 timecode tip can link the operator straight to the embedded
 * player. Returns null when the public portal base URL is not configured
 * (in which case the tip renders as prose without a hyperlink). The base
 * URL comes from VITE_PUBLIC_PORTAL_BASE_URL (e.g. "https://watch.example.gov"
 * or "/portal"); the trailing #/watch/{id} matches the portal-public router.
 */
function publicWatchUrlFor(assetId: string | null | undefined): string | null {
  if (!assetId) return null
  const base = (import.meta.env.VITE_PUBLIC_PORTAL_BASE_URL ?? '').trim()
  const trimmed = base.replace(/\/+$/, '')
  return `${trimmed}/#/watch/${encodeURIComponent(assetId)}`
}

type Tone = 'neutral' | 'warn' | 'info' | 'ok'

const TONE_COLORS: Record<Tone, { bg: string; bd: string }> = {
  neutral: { bg: 'var(--cc-surface-2)', bd: 'var(--cc-line)' },
  warn: { bg: 'var(--cc-warn-soft)', bd: 'var(--cc-warn)' },
  info: { bg: 'var(--cc-info-soft)', bd: 'var(--cc-info)' },
  ok: { bg: 'var(--cc-ok-soft)', bd: 'var(--cc-ok)' },
}

const INPUT_STYLE: CSSProperties = {
  background: 'var(--cc-surface)',
  border: '1px solid var(--cc-line)',
  color: 'var(--cc-ink)',
}

function apiMessage(err: unknown, fallback: string): string {
  if (err instanceof ApiError) return err.detail || err.message || fallback
  if (err instanceof Error) return err.message || fallback
  return fallback
}

function Banner({ tone, children }: { tone: Tone; children: ReactNode }) {
  const c = TONE_COLORS[tone]
  // Match UnderwritingScreen's convention: warn = role="alert" (assertive),
  // everything else = role="status" (polite).
  const role = tone === 'warn' ? 'alert' : 'status'
  return (
    <div
      role={role}
      className="rounded-md p-3 text-sm"
      style={{ background: c.bg, border: `1px solid ${c.bd}` }}
    >
      {children}
    </div>
  )
}

function StatusBadge({ status }: { status: 'draft' | 'published' }) {
  const ok = status === 'published'
  const c = ok ? TONE_COLORS.ok : TONE_COLORS.neutral
  return (
    <span
      aria-label={`Agenda status: ${status}`}
      className="cc-mono rounded-full px-2 py-0.5 text-xs"
      style={{ background: c.bg, border: `1px solid ${c.bd}` }}
    >
      {status}
    </span>
  )
}

// --- Create-agenda form -----------------------------------------------------

interface CreateAgendaFormState {
  agenda_id: string
  meeting_asset_id: string
  source_doc_url: string
}

const EMPTY_CREATE_FORM: CreateAgendaFormState = {
  agenda_id: '',
  meeting_asset_id: '',
  source_doc_url: '',
}

function CreateAgendaForm({
  form,
  onChange,
  onSubmit,
  pending,
  meetings,
}: {
  meetings: AgendaMeetingOption[]
  form: CreateAgendaFormState
  onChange: (next: CreateAgendaFormState) => void
  onSubmit: () => void
  pending: boolean
}) {
  const idMa = useId()
  const idUrl = useId()
  const canSubmit =
    form.agenda_id.trim().length > 0 && form.meeting_asset_id.trim().length > 0 && !pending
  return (
    <section
      aria-label="Create a new meeting agenda"
      className="space-y-3 rounded-md p-4 text-sm"
      style={{ background: 'var(--cc-surface)', border: '1px solid var(--cc-line)' }}
    >
      <h2 className="text-sm font-semibold">Create new agenda</h2>
      <label htmlFor={idMa} className="grid gap-1 text-xs">
        <span>Meeting or recording</span>
        <select id={idMa} aria-label="Meeting or recording" value={form.meeting_asset_id}
          onChange={(event) => onChange({ ...form, meeting_asset_id: event.target.value, agenda_id: `agenda-${crypto.randomUUID()}` })}
          className="rounded-md px-2 py-1.5" style={INPUT_STYLE}>
          <option value="">Choose a meeting or recording</option>
          {meetings.map((meeting) => <option key={meeting.meeting_asset_id} value={meeting.meeting_asset_id}>{meeting.title} ({meeting.state})</option>)}
        </select>
      </label>
      <label htmlFor={idUrl} className="grid gap-1 text-xs">
        <span style={{ color: 'var(--cc-ink-3)' }}>Source doc URL (optional)</span>
        <input
          id={idUrl}
          aria-label="Source doc URL"
          type="url"
          pattern="https?://.*"
          value={form.source_doc_url}
          placeholder="https://example.gov/agendas/council-2026-01.pdf"
          onChange={(e) => onChange({ ...form, source_doc_url: e.target.value })}
          className="rounded-md px-2 py-1.5"
          style={INPUT_STYLE}
        />
        <span style={{ color: 'var(--cc-ink-3)' }}>
          Link to the published PDF/HTML agenda the resident can read alongside the video.
        </span>
      </label>
      <div className="flex flex-wrap items-center gap-2 pt-1">
        <button
          type="button"
          disabled={!canSubmit}
          onClick={onSubmit}
          className="rounded-md px-3 py-1.5 font-semibold disabled:opacity-50"
          style={{ background: 'var(--cc-brand)', color: 'var(--cc-brand-ink)' }}
        >
          Create agenda
        </button>
      </div>
    </section>
  )
}

// --- Selected-agenda card ---------------------------------------------------

function SelectedAgendaCard({
  agenda,
  meetingTitle,
  itemCount,
  onPublish,
  onUnpublish,
  onArmDelete,
  onConfirmDelete,
  confirming,
  publishing,
  deleting,
  publishError,
  deleteError,
}: {
  agenda: MeetingAgenda
  meetingTitle?: string
  itemCount: number
  onPublish: () => void
  onUnpublish: () => void
  onArmDelete: () => void
  onConfirmDelete: () => void
  confirming: boolean
  publishing: boolean
  deleting: boolean
  publishError: string | null
  deleteError: string | null
}) {
  const status = agenda.status ?? 'draft'
  const canPublish = status === 'draft' && itemCount > 0
  return (
    <section
      aria-label="Selected agenda"
      className="space-y-2 rounded-md p-4 text-sm"
      style={{ background: 'var(--cc-surface)', border: '1px solid var(--cc-line)' }}
    >
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <div className="flex items-center gap-2">
            <strong className="text-sm">{meetingTitle ?? 'Meeting agenda'}</strong>
            <StatusBadge status={status} />
          </div>
          <div className="text-xs" style={{ color: 'var(--cc-ink-3)' }}>
            Linked recording: {meetingTitle ?? 'unavailable; refresh the recording list'}
          </div>
          {agenda.source_doc_url ? (
            <div className="text-xs">
              <a
                href={agenda.source_doc_url}
                target="_blank"
                rel="noreferrer noopener"
                aria-label="Open source agenda document"
                style={{ color: 'var(--cc-brand-2)' }}
              >
                Source doc ↗
              </a>
            </div>
          ) : (
            <div className="text-xs" style={{ color: 'var(--cc-ink-3)' }}>
              No source doc URL.
            </div>
          )}
        </div>
        <div className="flex flex-wrap items-center gap-2">
          {status === 'draft' ? (
            <button
              type="button"
              aria-label="Publish this agenda"
              disabled={!canPublish || publishing}
              onClick={onPublish}
              title={
                itemCount === 0
                  ? 'Publish needs at least one item — add one below or sync from chapters.'
                  : undefined
              }
              className="rounded-md px-3 py-1.5 text-xs font-semibold disabled:opacity-50"
              style={{ background: 'var(--cc-brand)', color: 'var(--cc-brand-ink)' }}
            >
              {publishing ? 'Publishing…' : 'Publish'}
            </button>
          ) : (
            <button
              type="button"
              aria-label="Unpublish this agenda"
              disabled={publishing}
              onClick={onUnpublish}
              className="rounded-md px-3 py-1.5 text-xs font-medium disabled:opacity-50"
              style={{ background: 'var(--cc-surface)', border: '1px solid var(--cc-line)' }}
            >
              {publishing ? 'Saving…' : 'Unpublish'}
            </button>
          )}
          {confirming ? (
            <button
              type="button"
              aria-label="Confirm delete agenda"
              disabled={deleting}
              onClick={onConfirmDelete}
              className="rounded-md px-3 py-1.5 text-xs font-semibold disabled:opacity-50"
              style={{ background: 'var(--cc-err-soft)', border: '1px solid var(--cc-err)' }}
            >
              {deleting ? 'Deleting…' : 'Confirm delete'}
            </button>
          ) : (
            <button
              type="button"
              aria-label="Delete this agenda"
              onClick={onArmDelete}
              className="rounded-md px-3 py-1.5 text-xs font-medium"
              style={{ background: 'var(--cc-surface)', border: '1px solid var(--cc-line)' }}
            >
              Delete
            </button>
          )}
        </div>
      </div>
      {status === 'draft' && itemCount === 0 && (
        <p className="text-xs" style={{ color: 'var(--cc-ink-3)' }}>
          Publish needs at least one item. Add one below or sync from the meeting asset&apos;s
          chapters.
        </p>
      )}
      {publishError && <Banner tone="warn">{publishError}</Banner>}
      {confirming && (
        <p className="text-xs" style={{ color: 'var(--cc-warn)' }}>
          Confirming will also delete every item under this agenda.
        </p>
      )}
      {deleteError && <Banner tone="warn">{deleteError}</Banner>}
    </section>
  )
}

// --- Item form --------------------------------------------------------------

interface ItemFormState {
  /** null when adding; item_id otherwise. */
  editingItemId: string | null
  item_id: string
  order: string
  number: string
  title: string
  video_timecode_s: string
  doc_anchor: string
  notes: string
}

const EMPTY_ITEM_FORM: ItemFormState = {
  editingItemId: null,
  item_id: '',
  order: '0',
  number: '',
  title: '',
  video_timecode_s: '',
  doc_anchor: '',
  notes: '',
}

function formFromItem(item: AgendaItem): ItemFormState {
  return {
    editingItemId: item.item_id,
    item_id: item.item_id,
    order: String(item.order),
    number: item.number ?? '',
    title: item.title,
    video_timecode_s: item.video_timecode_s == null ? '' : formatTimecode(item.video_timecode_s),
    doc_anchor: item.doc_anchor ?? '',
    notes: item.notes ?? '',
  }
}

function parseOptionalInt(text: string): { ok: boolean; value: number | null } {
  const trimmed = text.trim()
  if (trimmed.length === 0) return { ok: true, value: null }
  if (!/^\d+$/.test(trimmed)) return { ok: false, value: null }
  const n = Number.parseInt(trimmed, 10)
  if (!Number.isFinite(n) || n < 0) return { ok: false, value: null }
  return { ok: true, value: n }
}

function ItemForm({
  form,
  onChange,
  onSubmit,
  onCancelEdit,
  pending,
  publicPortalUrl,
  sourceTime,
}: {
  form: ItemFormState
  onChange: (next: ItemFormState) => void
  onSubmit: () => void
  onCancelEdit: () => void
  pending: boolean
  /**
   * Honest UX-1 fix: link the operator to the public portal page for the
   * selected meeting asset so they can find the timecode in the live player
   * and paste it here. Null when the agenda has no meeting asset bound (the
   * link section then renders nothing). Embedding the player on this screen
   * is a follow-up: HlsPlayer lives in the portal-public package and
   * importing it across apps would require restructuring vite/tsconfig path
   * aliases that are deliberately scoped to ./src per app.
   */
  publicPortalUrl: string | null
  sourceTime?: boolean
}) {
  const idItem = useId()
  const idOrder = useId()
  const idNum = useId()
  const idTitle = useId()
  const idTc = useId()
  const idAnchor = useId()
  const idNotes = useId()
  const editing = form.editingItemId != null
  const orderParsed = parseOptionalInt(form.order)
  const tcParsed = parseAgendaTime(form.video_timecode_s)
  const canSubmit =
    (editing || form.item_id.trim().length > 0) &&
    form.title.trim().length > 0 &&
    orderParsed.ok &&
    orderParsed.value != null &&
    tcParsed.ok &&
    !pending

  return (
    <section
      aria-label={editing ? 'Edit agenda item' : 'Add agenda item'}
      className="space-y-3 rounded-md p-4 text-sm"
      style={{ background: 'var(--cc-surface)', border: '1px solid var(--cc-line)' }}
    >
      <h3 className="text-sm font-semibold">{editing ? 'Edit item' : 'Add item'}</h3>

      {!editing && (
        <label htmlFor={idItem} className="grid gap-1 text-xs">
          <span style={{ color: 'var(--cc-ink-3)' }}>Item ID (slug)</span>
          <input
            id={idItem}
            aria-label="Item ID"
            type="text"
            value={form.item_id}
            placeholder="item-01-call-to-order"
            onChange={(e) => onChange({ ...form, item_id: e.target.value })}
            className="rounded-md px-2 py-1.5"
            style={INPUT_STYLE}
          />
        </label>
      )}

      <div className="grid gap-3 sm:grid-cols-2">
        <label htmlFor={idOrder} className="grid gap-1 text-xs">
          <span style={{ color: 'var(--cc-ink-3)' }}>Order</span>
          <input
            id={idOrder}
            aria-label="Order"
            type="number"
            min={0}
            value={form.order}
            onChange={(e) => onChange({ ...form, order: e.target.value })}
            className="rounded-md px-2 py-1.5"
            style={INPUT_STYLE}
          />
          {!orderParsed.ok && (
            <span className="text-xs" style={{ color: 'var(--cc-warn)' }}>
              Order must be a whole non-negative number.
            </span>
          )}
        </label>
        <label htmlFor={idNum} className="grid gap-1 text-xs">
          <span style={{ color: 'var(--cc-ink-3)' }}>Number (label, optional)</span>
          <input
            id={idNum}
            aria-label="Number"
            type="text"
            value={form.number}
            placeholder="1.a"
            onChange={(e) => onChange({ ...form, number: e.target.value })}
            className="rounded-md px-2 py-1.5"
            style={INPUT_STYLE}
          />
        </label>
      </div>

      <label htmlFor={idTitle} className="grid gap-1 text-xs">
        <span style={{ color: 'var(--cc-ink-3)' }}>Title</span>
        <input
          id={idTitle}
          aria-label="Title"
          type="text"
          value={form.title}
          placeholder="Call to order"
          onChange={(e) => onChange({ ...form, title: e.target.value })}
          className="rounded-md px-2 py-1.5"
          style={INPUT_STYLE}
        />
      </label>

      <div className="grid gap-3 sm:grid-cols-2">
        <label htmlFor={idTc} className="grid gap-1 text-xs">
          <span style={{ color: 'var(--cc-ink-3)' }}>{sourceTime ? 'Source recording time (hh:mm:ss, optional)' : 'Packaged playback time (hh:mm:ss, optional)'}</span>
          <input
            id={idTc}
            aria-label={sourceTime ? 'Source recording time (hh:mm:ss)' : 'Packaged playback time (hh:mm:ss)'}
            aria-describedby={`${idTc}-tip`}
            type="text"
            value={form.video_timecode_s}
            placeholder="00:02:05"
            onChange={(e) => onChange({ ...form, video_timecode_s: e.target.value })}
            className="rounded-md px-2 py-1.5"
            style={INPUT_STYLE}
          />
          <span
            id={`${idTc}-tip`}
            className="text-xs"
            style={{ color: 'var(--cc-ink-3)' }}
          >
            Tip:{' '}
            {publicPortalUrl ? (
              <>
                <a
                  href={publicPortalUrl}
                  target="_blank"
                  rel="noreferrer noopener"
                  aria-label="Open this meeting on the public portal in a new tab to find the timecode"
                  style={{ color: 'var(--cc-brand-2)' }}
                >
                  open the meeting on the public portal ↗
                </a>{' '}
                in another tab, find the moment in the player, then enter the time here. {sourceTime ? 'Use original capture time; published chapters adjust for packaged trims.' : 'Use the packaged public video clock; these times are already playback positions.'}
              </>
            ) : (
              <>
                open the meeting on the public portal in another tab, find the moment in
                the player, then enter the time here. {sourceTime ? 'Use original capture time; published chapters adjust for packaged trims.' : 'Use the packaged public video clock; these times are already playback positions.'}
              </>
            )}
          </span>
          {!tcParsed.ok && (
            <span className="text-xs" style={{ color: 'var(--cc-warn)' }}>
              Enter hh:mm:ss with minutes and seconds from 00 to 59, numeric seconds, or leave blank.
            </span>
          )}
        </label>
        <label htmlFor={idAnchor} className="grid gap-1 text-xs">
          <span style={{ color: 'var(--cc-ink-3)' }}>Doc anchor (optional)</span>
          <input
            id={idAnchor}
            aria-label="Doc anchor"
            type="text"
            value={form.doc_anchor}
            placeholder="#item-1a"
            onChange={(e) => onChange({ ...form, doc_anchor: e.target.value })}
            className="rounded-md px-2 py-1.5"
            style={INPUT_STYLE}
          />
        </label>
      </div>

      <label htmlFor={idNotes} className="grid gap-1 text-xs">
        <span style={{ color: 'var(--cc-ink-3)' }}>Notes (optional)</span>
        <textarea
          id={idNotes}
          aria-label="Notes"
          rows={3}
          value={form.notes}
          placeholder="Operator-private notes; not shown to viewers."
          onChange={(e) => onChange({ ...form, notes: e.target.value })}
          className="rounded-md px-2 py-1.5"
          style={INPUT_STYLE}
        />
      </label>

      <div className="flex flex-wrap items-center gap-2 pt-1">
        <button
          type="button"
          disabled={!canSubmit}
          onClick={onSubmit}
          className="rounded-md px-3 py-1.5 font-semibold disabled:opacity-50"
          style={{ background: 'var(--cc-brand)', color: 'var(--cc-brand-ink)' }}
        >
          {editing ? 'Save item' : 'Add item'}
        </button>
        {editing && (
          <button
            type="button"
            onClick={onCancelEdit}
            className="rounded-md px-3 py-1.5 font-medium"
            style={{ background: 'var(--cc-surface)', border: '1px solid var(--cc-line)' }}
          >
            Cancel
          </button>
        )}
      </div>
    </section>
  )
}

// --- Items table ------------------------------------------------------------

function ItemsTable({
  items,
  onMarkNow,
  marking,
  onEdit,
  onArmDelete,
  onConfirmDelete,
  confirming,
  deleting,
}: {
  items: AgendaItem[]
  onMarkNow?: (itemId: string) => void
  marking?: boolean
  onEdit: (item: AgendaItem) => void
  onArmDelete: (itemId: string) => void
  onConfirmDelete: (itemId: string) => void
  confirming: Record<string, true>
  deleting: string | null
}) {
  if (items.length === 0) {
    return (
      <p className="text-xs" style={{ color: 'var(--cc-ink-3)' }}>
        No items on this agenda yet. Add one with the form above, or run &ldquo;Sync from
        chapters&rdquo; in the Bulk actions section if the meeting asset already has chapter
        markers.
      </p>
    )
  }
  return (
    <div className="overflow-auto">
      <table className="w-full text-sm" aria-label="Agenda items">
        <thead>
          <tr style={{ color: 'var(--cc-ink-3)' }}>
            <th className="px-2 py-1 text-left">Order</th>
            <th className="px-2 py-1 text-left">Number</th>
            <th className="px-2 py-1 text-left">Title</th>
            <th className="px-2 py-1 text-left">Timecode</th>
            <th className="px-2 py-1 text-left">Doc anchor</th>
            <th className="px-2 py-1 text-left">Confidence</th>
            <th className="px-2 py-1 text-right">Actions</th>
          </tr>
        </thead>
        <tbody>
          {items.map((item) => {
            const isConfirming = item.item_id in confirming
            const isDeleting = deleting === item.item_id
            return (
              <tr key={item.item_id} style={{ borderTop: '1px solid var(--cc-line)' }}>
                <td className="cc-mono cc-tabular px-2 py-1 text-xs">{item.order}</td>
                <td className="cc-mono px-2 py-1 text-xs">{item.number ?? '—'}</td>
                <td className="px-2 py-1 text-xs">{item.title}</td>
                <td className="cc-mono cc-tabular px-2 py-1 text-xs">
                  {formatTimecode(item.video_timecode_s ?? null)}
                </td>
                <td className="cc-mono px-2 py-1 text-xs">{item.doc_anchor ?? '—'}</td>
                <td className="px-2 py-1 text-xs">
                  {item.confidence == null ? (
                    '—'
                  ) : (
                    <span
                      className="cc-mono rounded-full px-2 py-0.5 text-[11px] font-semibold"
                      title="Confidence score from the PDF import heuristic — review before publishing if low."
                      style={{
                        background:
                          item.confidence >= 0.9
                            ? 'var(--cc-ok-soft)'
                            : item.confidence >= 0.5
                              ? 'var(--cc-warn-soft)'
                              : 'var(--cc-err-soft)',
                        color: 'var(--cc-ink)',
                      }}
                    >
                      {Math.round(item.confidence * 100)}%
                    </span>
                  )}
                </td>
                <td className="px-2 py-1 text-right">
                  <div className="inline-flex flex-wrap items-center gap-1">
                    {onMarkNow && <button type="button" disabled={marking} aria-label={`Mark ${item.title} now`} onClick={() => onMarkNow(item.item_id)}>Mark current item now</button>}
                    <button
                      type="button"
                      aria-label={`Edit ${item.title}`}
                      onClick={() => onEdit(item)}
                      className="rounded-md px-2 py-1 text-xs font-medium"
                      style={{
                        background: 'var(--cc-surface)',
                        border: '1px solid var(--cc-line)',
                      }}
                    >
                      Edit
                    </button>
                    {isConfirming ? (
                      <button
                        type="button"
                        aria-label={`Confirm delete ${item.title}`}
                        disabled={isDeleting}
                        onClick={() => onConfirmDelete(item.item_id)}
                        className="rounded-md px-2 py-1 text-xs font-semibold disabled:opacity-50"
                        style={{
                          background: 'var(--cc-err-soft)',
                          border: '1px solid var(--cc-err)',
                        }}
                      >
                        {isDeleting ? 'Deleting…' : 'Confirm delete'}
                      </button>
                    ) : (
                      <button
                        type="button"
                        aria-label={`Delete ${item.title}`}
                        onClick={() => onArmDelete(item.item_id)}
                        className="rounded-md px-2 py-1 text-xs font-medium"
                        style={{
                          background: 'var(--cc-surface)',
                          border: '1px solid var(--cc-line)',
                        }}
                      >
                        Delete
                      </button>
                    )}
                  </div>
                </td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}

// --- External agenda import (civiccast/agenda_import/, Agenda Bridge) ------
//
// WP-11 owner-directed card (carried forward from the reconciled long list):
// a disabled "coming in a future release" explainer for the CivicSuite event
// bridge, placed beside the working ExternalImportSection below so the two
// are never conflated. CivicCast already ships a manual/public CivicClerk
// agenda importer today (ExternalImportSection's `civicclerk` source, using
// the tenant's public CivicClerk site — no CivicClerk account or API key
// required) and that stays exactly as-is. The CivicSuite event bridge is a
// separate, not-yet-built integration: an authenticated connection to a
// jurisdiction's CivicSuite account that would receive meeting lifecycle
// events automatically (no operator import click) and send published
// recording links back to CivicClerk. This card exposes no executable
// configuration -- there is nothing to configure yet.
function CivicSuiteBridgeCard() {
  return (
    <div
      className="space-y-1 rounded-md border-t pt-3 text-xs"
      style={{ borderColor: 'var(--cc-line)' }}
      aria-label="CivicSuite event bridge (future release)"
    >
      <div className="font-semibold">CivicSuite event bridge — coming in a future release</div>
      <p className="m-0" style={{ color: 'var(--cc-ink-3)' }}>
        The agenda importer above is separate from this and already works: it pulls a
        meeting&apos;s agenda from a tenant&apos;s public CivicClerk site (or Legistar,
        PrimeGov, or a generic agenda portal) on request, with no CivicClerk account
        required. The CivicSuite event bridge is a different, not-yet-built integration:
        an authenticated connection to a jurisdiction&apos;s CivicSuite account that would
        receive meeting lifecycle events automatically as they happen, and send published
        recording links back to CivicClerk once a meeting airs. There is nothing to
        configure here yet.
      </p>
    </div>
  )
}

// Distinct from the plain-text/PDF "Import from doc" block above: this talks
// to civiccast/agenda_import/router.py's discovery + import-external routes
// (Legistar/PrimeGov/CivicClerk/js_portal adapters), a separate module that
// writes into the same items store. Two-step flow: "Find meetings" lists
// upcoming/recent meetings from the vendor, then the operator picks one and
// imports it. js_portal additionally needs a portal URL + vendor hint (no
// fixed per-vendor host the way the other three have) and may be "not
// installed" on this station (optional crawl4ai/Playwright extra) — the
// posture check runs as soon as js_portal is selected so the operator finds
// out before clicking Import, not after a wasted round trip.

/**
 * The calendar year an ISO date/datetime string falls in, read from its
 * leading `YYYY-` so a timezone offset can never move it across a year
 * boundary. Returns null for an absent/unparseable value.
 */
function isoYear(raw: string | null | undefined): number | null {
  const match = /^(\d{4})-/.exec((raw ?? '').trim())
  return match === null ? null : Number(match[1])
}

function savedAgendaPreview(agendaId: string): string | null {
  try {
    return sessionStorage.getItem(`agenda-import-preview:${agendaId}`)
  } catch {
    return null
  }
}

function AgendaPreviewDocument({ preview }: { preview: AgendaImportPreview }) {
  const [blobUrl, setBlobUrl] = useState<string | null>(null)
  const ownedUrl = useRef<string | null>(null)
  const active = useRef(true)
  useEffect(() => {
    active.current = true
    return () => {
      active.current = false
      if (ownedUrl.current) URL.revokeObjectURL(ownedUrl.current)
    }
  }, [])
  const documentMut = useMutation({
    mutationFn: () => staffApiBlobRequest(preview.source_document_url!),
    onSuccess: (blob) => {
      if (!active.current) return
      const url = URL.createObjectURL(blob)
      ownedUrl.current = url
      setBlobUrl(url)
    },
  })
  const sourceUrl = preview.source_document_url ? blobUrl : preview.source_doc_url
  return <>
    {preview.source_document_url && !blobUrl && <button type="button"
      disabled={documentMut.isPending} onClick={() => documentMut.mutate()}
      className="text-xs underline disabled:opacity-50">
      {documentMut.isPending ? 'Loading retained source document…' : 'Load retained source document'}
    </button>}
    {sourceUrl && <a href={sourceUrl} target="_blank" rel="noreferrer noopener" className="text-xs underline">
      {preview.source_document_url ? 'Open retained source document' : 'Open published agenda'}
    </a>}
    {documentMut.isError && <Banner tone="warn">{apiMessage(documentMut.error, 'Could not load the retained source document.')}</Banner>}
    <ol aria-label="Preview agenda items" className="space-y-2 text-sm">
      {preview.items.map((item) => <li key={item.order}>
        <span>{item.number ? `${item.number.replace(/[.)]$/, '')}. ` : ''}{item.title}</span>
        {(preview.item_pages[String(item.order)] ?? []).map((page) => sourceUrl
          ? <a key={page} className="ml-2 text-xs underline"
            href={`${sourceUrl.replace(/#.*$/, '')}#page=${page}`}
            target="_blank" rel="noreferrer noopener">Page {page}</a>
          : <span key={page} className="ml-2 text-xs">Page {page} (load retained document to view)</span>)}
      </li>)}
    </ol>
  </>
}

function ExternalImportSection({
  agendaId,
  agendaStatus,
  onImported,
}: {
  agendaId: string
  agendaStatus: 'draft' | 'published'
  onImported: () => void
}) {
  const idSource = useId()
  const idClientCode = useId()
  const idPortalUrl = useId()
  const idVendorHint = useId()
  const idSince = useId()
  const idMeeting = useId()

  const stationSettings = useQuery({ queryKey: agendaSettingsKey, queryFn: getAgendaSourceSettings, retry: false })
  const defaults = stationSettings.data
  const [chosenSource, setSource] = useState<AgendaExternalSource | null>(null)
  const source = chosenSource ?? (defaults?.source && defaults.source !== 'off' ? defaults.source : 'legistar')
  const [chosenClientCode, setClientCode] = useState<string | null>(null)
  const clientCode = chosenClientCode ?? defaults?.client_code ?? ''
  const [chosenPortalUrl, setPortalUrl] = useState<string | null>(null)
  const portalUrl = chosenPortalUrl ?? defaults?.portal_url ?? ''
  const [chosenVendorHint, setVendorHint] = useState<string | null>(null)
  const vendorHint = chosenVendorHint ?? defaults?.portal_vendor_hint ?? 'generic'
  const [since, setSince] = useState('')
  const [meetings, setMeetings] = useState<ExternalMeetingSummary[] | null>(null)
  const [selectedEventId, setSelectedEventId] = useState('')
  const [discoveryWarning, setDiscoveryWarning] = useState<string | null>(null)
  const [usePacket, setUsePacket] = useState(false)
  const [previewId, setPreviewId] = useState<string | null>(() => savedAgendaPreview(agendaId))
  const previewClient = useQueryClient()
  const previewKey = ['agenda-import-preview', agendaId, previewId]
  const previewQuery = useQuery({
    queryKey: previewKey,
    queryFn: () => getAgendaImportPreview(agendaId, previewId!),
    enabled: previewId !== null,
    retry: false,
    staleTime: 1000,
    refetchInterval: (query) =>
      query.state.data?.status === 'queued' || query.state.data?.status === 'reading' ? 1000 : false,
  })
  const preview = previewQuery.data
  const readingPreview = preview?.status === 'queued' || preview?.status === 'reading'

  const rememberPreview = (next: AgendaImportPreview) => {
    previewClient.setQueryData(['agenda-import-preview', agendaId, next.preview_id], next)
    setPreviewId(next.preview_id)
    try {
      sessionStorage.setItem(`agenda-import-preview:${agendaId}`, next.preview_id)
    } catch { /* A blocked browser store does not prevent an in-session preview. */ }
  }
  const clearPreview = () => {
    setPreviewId(null)
    try {
      sessionStorage.removeItem(`agenda-import-preview:${agendaId}`)
    } catch { /* The server remains the durable source of preview state. */ }
  }

  const isJsPortal = source === 'js_portal'

  const postureQuery = useQuery({
    queryKey: ['js-portal-posture'],
    queryFn: getJsPortalPosture,
    enabled: isJsPortal,
    staleTime: 60_000,
  })
  const jsPortalBlocked = isJsPortal && postureQuery.data?.installed === false

  const discoverMut = useMutation({
    mutationFn: () =>
      listExternalAgendaMeetings(source, clientCode.trim(), {
        since: since.trim() === '' ? null : since.trim(),
        portalUrl: isJsPortal ? portalUrl.trim() : null,
        portalVendorHint: isJsPortal ? vendorHint : null,
      }),
    onSuccess: (found) => {
      setMeetings(found.meetings)
      setDiscoveryWarning(found.partialWarning)
      setSelectedEventId(found.meetings[0]?.external_id ?? '')
    },
  })

  // The explicit finite year scope sent back on import (PrimeGov only): the
  // year of the meeting the operator selected, else the year of the "on/after"
  // filter used for discovery. PrimeGov's archived lists are per-year, so
  // without it an older archived meeting discovery offered could not be
  // found on import. The server rejects it for the other vendors.
  const selectedMeeting = meetings?.find((m) => m.external_id === selectedEventId)
  const importYearCandidate = isoYear(selectedMeeting?.meeting_datetime) ?? isoYear(since)
  // An UPCOMING meeting discovery offered can be dated in the next calendar year
  // (the always-queried upcoming list is not year-filtered). Such a meeting needs
  // no archive year: the importer reads that upcoming list first. Only a year
  // that is a valid archived-list year (this year or earlier) is sent, so the
  // screen can never ask for a scope the server refuses.
  const importArchiveYear =
    importYearCandidate !== null && importYearCandidate <= new Date().getUTCFullYear()
      ? importYearCandidate
      : null

  const importMut = useMutation({
    mutationFn: () => {
      const payload: AgendaImportExternalRequest = {
        source,
        client_code: clientCode.trim(),
        event_id: selectedEventId,
        portal_url: isJsPortal ? portalUrl.trim() : null,
        portal_vendor_hint: isJsPortal ? vendorHint : null,
        archive_year: source === 'primegov' ? importArchiveYear : null,
      }
      return importExternalAgenda(agendaId, payload)
    },
    onSuccess: () => {
      onImported()
      setMeetings(null)
      setSelectedEventId('')
    },
  })

  const previewMut = useMutation({
    mutationFn: () => startAgendaImportPreview(agendaId, {
      source: 'primegov', client_code: clientCode.trim(), event_id: selectedEventId,
      portal_url: null, portal_vendor_hint: null, archive_year: importArchiveYear,
      document_kind: usePacket ? 'packet' : 'automatic',
    }),
    onSuccess: rememberPreview,
  })
  const resumeMut = useMutation({
    mutationFn: () => resumeAgendaImportPreview(agendaId, previewId!),
    onSuccess: rememberPreview,
  })
  const cancelMut = useMutation({
    mutationFn: () => cancelAgendaImportPreview(agendaId, previewId!),
    onSuccess: rememberPreview,
  })
  const confirmMut = useMutation({
    mutationFn: () => {
      if (!previewId || preview?.status !== 'ready') throw new Error('The preview is not ready to import.')
      return confirmAgendaImportPreview(agendaId, previewId)
    },
    onSuccess: () => {
      onImported()
      clearPreview()
      setMeetings(null)
      setSelectedEventId('')
    },
  })
  const previewBusy = previewMut.isPending || readingPreview || resumeMut.isPending || cancelMut.isPending || confirmMut.isPending

  const importIs503 =
    importMut.isError && importMut.error instanceof ApiError && importMut.error.status === 503
  const discoverIs503 =
    discoverMut.isError && discoverMut.error instanceof ApiError && discoverMut.error.status === 503

  const canDiscover =
    clientCode.trim().length > 0 &&
    (!isJsPortal || (portalUrl.trim().length > 0 && !jsPortalBlocked)) &&
    !discoverMut.isPending

  const importedNeedsReview =
    importMut.isSuccess && (importMut.data ?? []).some((it) => (it.confidence ?? 1) < 0.9)

  return (
    <div className="space-y-2 border-t pt-3" style={{ borderColor: 'var(--cc-line)' }}>
      {stationSettings.isError && <Banner tone="warn">{apiMessage(stationSettings.error, 'Could not load saved agenda settings. Retry after checking the station connection.')}</Banner>}
      {defaults?.source === 'off' && <Banner tone="info">Agenda import is disabled. A setup administrator can enable it in Agenda source setup above.</Banner>}
      <span className="text-xs font-semibold">
        Import from an external agenda system
      </span>
      <div className="grid gap-3 sm:grid-cols-2">
        <label htmlFor={idSource} className="grid gap-1 text-xs">
          <span style={{ color: 'var(--cc-ink-3)' }}>Source</span>
          <select
            id={idSource}
            aria-label="External agenda source"
            value={source}
            onChange={(e) => {
              setSource(e.target.value as AgendaExternalSource)
              setMeetings(null)
              setSelectedEventId('')
              setDiscoveryWarning(null)
            }}
            className="rounded-md px-2 py-1.5"
            style={INPUT_STYLE}
          >
            {AGENDA_EXTERNAL_SOURCES.map((value) => (
              <option key={value} value={value}>
                {EXTERNAL_SOURCE_LABELS[value]}
              </option>
            ))}
          </select>
        </label>
        <label htmlFor={idClientCode} className="grid gap-1 text-xs">
          <span style={{ color: 'var(--cc-ink-3)' }}>
            {isJsPortal ? 'Display label (for your own records)' : 'Tenant / site code'}
          </span>
          <input
            id={idClientCode}
            aria-label={isJsPortal ? 'Display label' : 'Tenant / site code'}
            type="text"
            value={clientCode}
            placeholder={isJsPortal ? 'fairview-agendacenter' : 'longmont'}
            onChange={(e) => setClientCode(e.target.value)}
            className="rounded-md px-2 py-1.5"
            style={INPUT_STYLE}
          />
        </label>
      </div>

      {isJsPortal && (
        <div className="grid gap-3 sm:grid-cols-2">
          <label htmlFor={idPortalUrl} className="grid gap-1 text-xs">
            <span style={{ color: 'var(--cc-ink-3)' }}>Portal URL</span>
            <input
              id={idPortalUrl}
              aria-label="Portal URL"
              type="url"
              pattern="https?://.*"
              value={portalUrl}
              placeholder="https://fairview.example.gov/AgendaCenter"
              onChange={(e) => setPortalUrl(e.target.value)}
              className="rounded-md px-2 py-1.5"
              style={INPUT_STYLE}
            />
          </label>
          <label htmlFor={idVendorHint} className="grid gap-1 text-xs">
            <span style={{ color: 'var(--cc-ink-3)' }}>Vendor hint</span>
            <select
              id={idVendorHint}
              aria-label="Vendor hint"
              value={vendorHint}
              onChange={(e) => setVendorHint(e.target.value)}
              className="rounded-md px-2 py-1.5"
              style={INPUT_STYLE}
            >
              {JS_PORTAL_VENDOR_HINTS.map((opt) => (
                <option key={opt.value} value={opt.value}>
                  {opt.label}
                </option>
              ))}
            </select>
          </label>
        </div>
      )}

      {isJsPortal && (
        <p
          role={postureQuery.data?.installed === false ? 'alert' : 'status'}
          className="text-xs"
          style={{ color: postureQuery.data?.installed === false ? 'var(--cc-warn)' : 'var(--cc-ink-3)' }}
        >
          {postureQuery.isLoading
            ? 'Checking whether the JS-portal runtime is installed on this station…'
            : postureQuery.isError
              ? apiMessage(postureQuery.error, 'Could not check the JS-portal runtime status.')
              : postureQuery.data?.installed
                ? 'JS-portal runtime: installed.'
                : `JS-portal runtime: not installed. ${postureQuery.data?.detail ?? ''}`}
        </p>
      )}

      <label htmlFor={idSince} className="grid gap-1 text-xs sm:max-w-[220px]">
        <span style={{ color: 'var(--cc-ink-3)' }}>Only meetings on/after (optional)</span>
        <input
          id={idSince}
          aria-label="Only meetings on or after this date"
          type="date"
          value={since}
          onChange={(e) => setSince(e.target.value)}
          className="rounded-md px-2 py-1.5"
          style={INPUT_STYLE}
        />
      </label>

      {source === 'primegov' && (
        <><p className="text-xs" style={{ color: 'var(--cc-ink-3)' }}>
          PrimeGov discovery reads the upcoming list plus the archived list for the year of
          that date (the current year if left blank), and the import is scoped to the
          selected meeting&apos;s year.
        </p>
        <label className="flex items-center gap-2 text-xs">
          <input type="checkbox" aria-label="Use full published packet" checked={usePacket}
            disabled={previewBusy || previewId !== null} onChange={(event) => setUsePacket(event.target.checked)} />
          Use full published packet
        </label>
        <p className="text-xs" style={{ color: 'var(--cc-ink-3)' }}>
          The published agenda is selected first by default. Review the preview before confirming an import.
        </p></>
      )}

      <div className="flex flex-wrap items-center gap-2">
        <button
          type="button"
          aria-label="Find meetings from this external agenda source"
          disabled={!canDiscover}
          onClick={() => discoverMut.mutate()}
          className="rounded-md px-3 py-1.5 text-xs font-semibold disabled:opacity-50"
          style={{ background: 'var(--cc-brand)', color: 'var(--cc-brand-ink)' }}
        >
          {discoverMut.isPending ? 'Finding meetings…' : 'Find meetings'}
        </button>
      </div>

      {discoverMut.isError && (
        <Banner tone="warn">
          {discoverIs503
            ? apiMessage(
                discoverMut.error,
                "This source's optional runtime is not installed on this station.",
              )
            : apiMessage(discoverMut.error, 'Could not list meetings from that source.')}
        </Banner>
      )}

      {discoveryWarning && (
        <Banner tone="warn">
          Only partial results were returned. {discoveryWarning}
        </Banner>
      )}

      {meetings != null && meetings.length === 0 && (
        <Banner tone="info">No meetings found for those filters.</Banner>
      )}

      {meetings != null && meetings.length > 0 && (
        <>
          <label htmlFor={idMeeting} className="grid gap-1 text-xs">
            <span style={{ color: 'var(--cc-ink-3)' }}>Meeting</span>
            <select
              id={idMeeting}
              aria-label="Meeting to import"
              value={selectedEventId}
              onChange={(e) => setSelectedEventId(e.target.value)}
              className="rounded-md px-2 py-1.5"
              style={INPUT_STYLE}
            >
              {meetings.map((m) => (
                <option key={m.external_id} value={m.external_id}>
                  {m.title}
                  {m.meeting_datetime ? ` — ${new Date(m.meeting_datetime).toLocaleDateString()}` : ''}
                </option>
              ))}
            </select>
          </label>
          <div className="flex flex-wrap items-center gap-2">
            <button
              type="button"
              aria-label={source === 'primegov' ? "Preview the selected meeting's agenda" : "Import the selected meeting's agenda"}
              disabled={importMut.isPending || (source === 'primegov' && previewBusy) || selectedEventId === ''}
              onClick={() => source === 'primegov' ? previewMut.mutate() : importMut.mutate()}
              className="rounded-md px-3 py-1.5 text-xs font-semibold disabled:opacity-50"
              style={{ background: 'var(--cc-brand)', color: 'var(--cc-brand-ink)' }}
            >
              {source === 'primegov'
                ? previewMut.isPending ? 'Preparing preview…' : 'Preview selected meeting'
                : importMut.isPending ? 'Importing…' : 'Import selected meeting'}
            </button>
            {agendaStatus === 'published' && (
              <span className="text-xs" style={{ color: 'var(--cc-ink-3)' }}>
                This agenda is published. {source === 'primegov' ? 'Confirming the import' : 'Importing'} will move it back to draft until you review
                and republish.
              </span>
            )}
          </div>
        </>
      )}

      {previewId && (
        <section aria-label="PrimeGov agenda import preview" className="space-y-3 rounded-md border p-3">
          <h3 className="text-sm font-semibold">Agenda import preview</h3>
          <p className="text-sm font-semibold">{preview?.title ?? 'Selected meeting'}</p>
          {previewQuery.isLoading && <p role="status">Loading saved preview…</p>}
          {preview && <>
            <p role="status" className="text-xs">
              Preview status: {preview.status}. {preview.pages_total === null
                ? 'Published agenda preview.'
                : `${preview.pages_read} of ${preview.pages_total} document pages read.`}
              {readingPreview && preview.cancel_requested ? ' Cancellation requested; stopping the reader.' : ''}
            </p>
            <p className="text-xs">Review these items and the source document. Confirm import adds them to this agenda.</p>
            {preview.agenda_pages.length > 0 && <p className="text-xs">Agenda pages: {preview.agenda_pages.join(', ')}.</p>}
            {preview.warnings.map((warning, index) => <Banner key={index} tone="warn">{warning}</Banner>)}
            <AgendaPreviewDocument key={`${preview.preview_id}:${preview.source_document_url ?? ''}`} preview={preview} />
            <div className="flex flex-wrap gap-2">
              <button type="button" disabled={preview.status !== 'ready' || previewBusy || previewQuery.isError}
                onClick={() => confirmMut.mutate()} className="rounded-md px-3 py-1.5 text-xs font-semibold disabled:opacity-50"
                style={{ background: 'var(--cc-brand)', color: 'var(--cc-brand-ink)' }}>Confirm import</button>
              {preview.resumable && !readingPreview && <button type="button" disabled={previewBusy}
                onClick={() => resumeMut.mutate()} className="text-xs underline">Resume reading</button>}
              {readingPreview && <button type="button" disabled={cancelMut.isPending || preview.cancel_requested}
                onClick={() => cancelMut.mutate()} className="text-xs underline">Cancel reading</button>}
              {!readingPreview && !previewBusy && <button type="button" onClick={clearPreview}
                className="text-xs underline">Dismiss preview</button>}
            </div>
          </>}
          {previewQuery.isError && <Banner tone="warn">
            {apiMessage(previewQuery.error, 'Could not load this preview.')}
            {!preview && <button type="button" onClick={clearPreview} className="ml-2 underline">Dismiss unavailable preview</button>}
          </Banner>}
        </section>
      )}
      {[previewMut, resumeMut, cancelMut, confirmMut].map((mutation, index) => mutation.isError
        ? <Banner key={index} tone="warn">{apiMessage(mutation.error, 'Could not update the agenda preview.')}</Banner> : null)}
      {confirmMut.isSuccess && <Banner tone="ok">Imported {confirmMut.data.length} item{confirmMut.data.length === 1 ? '' : 's'}.</Banner>}

      {importMut.isSuccess && (
        <Banner tone={importedNeedsReview ? 'warn' : 'ok'}>
          Imported {importMut.data?.length ?? 0} item
          {(importMut.data?.length ?? 0) === 1 ? '' : 's'}.
          {importedNeedsReview
            ? ' Some items carry a lower confidence score (see the Confidence column below) — review them before publishing.'
            : ''}
        </Banner>
      )}
      {importMut.isError && (
        <Banner tone="warn">
          {importIs503
            ? apiMessage(
                importMut.error,
                "This source's optional runtime is not installed on this station.",
              )
            : apiMessage(importMut.error, 'Could not import that meeting.')}
        </Banner>
      )}
    </div>
  )
}

// --- Screen -----------------------------------------------------------------

export function AgendasScreen() {
  const identityQuery = useQuery<StaffIdentityResponse>({
    queryKey: ['staff-identity'],
    queryFn: getStaffIdentity,
    retry: false,
  })

  if (identityQuery.isLoading) {
    return (
      <div className="px-6 py-10 text-sm" style={{ color: 'var(--cc-ink-3)' }}>
        Loading…
      </div>
    )
  }
  if (identityQuery.isError) {
    return (
      <div className="px-6 py-10">
        <AuthRequiredState error={identityQuery.error} />
      </div>
    )
  }
  const canSetup = hasRole(identityQuery.data, ['setup_admin'])
  const canAuthor = hasRole(identityQuery.data, AUTHOR_ROLES)
  if (!canAuthor) {
    return (
      <div className="space-y-4 px-6 py-10">
        {canSetup && <AgendaSourceSetup />}
        <Banner tone="info">
          Agendas require the records clerk or meeting operator role. Ask your station admin
          for access.
        </Banner>
      </div>
    )
  }
  return <><div className="px-6 pt-6">{canSetup && <AgendaSourceSetup />}</div><AgendasBody /></>
}

function AgendasBody() {
  const qc = useQueryClient()

  const meetingsQuery = useQuery({ queryKey: ['agenda-meetings'], queryFn: listAgendaMeetings, refetchInterval: 10000 })
  const meetings = meetingsQuery.data ?? []
  const agendasQuery = useQuery({
    queryKey: ['meeting-agendas'],
    queryFn: () => listMeetingAgendas(),
  })

  const agendas = useMemo(() => {
    const list = agendasQuery.data ?? []
    return [...list].sort((a, b) => {
      const ma = a.meeting_asset_id.localeCompare(b.meeting_asset_id)
      if (ma !== 0) return ma
      return a.agenda_id.localeCompare(b.agenda_id)
    })
  }, [agendasQuery.data])

  // Operator-picked agenda id; `null` means "fall through to the first agenda
  // in the list". We derive the EFFECTIVE selection during render so we never
  // call setState from an effect just to seed a default (react-hooks/set-state-
  // in-effect). A freshly-created agenda gets `setPickedAgendaId(created.id)`
  // from the create-mutation onSuccess so the user is taken straight to it.
  const [pickedAgendaId, setPickedAgendaId] = useState<string | null>(null)

  const selectedAgendaId = useMemo<string | null>(() => {
    if (pickedAgendaId != null && agendas.some((a) => a.agenda_id === pickedAgendaId)) {
      return pickedAgendaId
    }
    return agendas.length > 0 ? agendas[0].agenda_id : null
  }, [agendas, pickedAgendaId])

  const selectedAgenda = useMemo(
    () => agendas.find((a) => a.agenda_id === selectedAgendaId) ?? null,
    [agendas, selectedAgendaId],
  )

  // --- Create agenda (lives on the body so a successful create can clear the
  // form without remounting the picker). ---
  const [createForm, setCreateForm] = useState<CreateAgendaFormState>(EMPTY_CREATE_FORM)
  const invalidateAgendas = () => qc.invalidateQueries({ queryKey: ['meeting-agendas'] })

  const createAgendaMut = useMutation({
    mutationFn: (payload: MeetingAgendaInput) => createMeetingAgenda(payload),
    onSuccess: (created) => {
      invalidateAgendas()
      setCreateForm(EMPTY_CREATE_FORM)
      setPickedAgendaId(created.agenda_id)
    },
  })

  const handleSubmitCreate = () => {
    const payload: MeetingAgendaInput = {
      agenda_id: createForm.agenda_id.trim(),
      station_id: DEFAULT_STATION_ID,
      meeting_asset_id: createForm.meeting_asset_id.trim(),
      source_recording_job_id: meetings.find((meeting) => meeting.meeting_asset_id === createForm.meeting_asset_id)?.source_recording_job_id ?? null,
      source_doc_url:
        createForm.source_doc_url.trim() === '' ? null : createForm.source_doc_url.trim(),
    }
    createAgendaMut.mutate(payload)
  }

  return (
    <div className="space-y-4 px-6 py-6">
      <div>
        <h1 className="text-lg font-semibold">Agendas</h1>
        <p className="text-xs" style={{ color: 'var(--cc-ink-3)' }}>
          Build and publish a meeting agenda the resident sees alongside the recording. Agendas
          stay drafts until published; published agendas appear on the public meeting page.
          Publishing needs at least one item.
        </p>
      </div>

      {/* UX-3 fix: hide the picker entirely on the empty-state path so the
         "No agendas yet" banner below is the only empty-state surface (the
         disabled placeholder option duplicated the banner copy and confused
         screen readers walking the tab order). The picker reappears as soon
         as at least one agenda exists. */}
      {(agendasQuery.isLoading || agendas.length > 0) && (
        <section
          aria-label="Agenda picker"
          className="space-y-3 rounded-md p-4 text-sm"
          style={{ background: 'var(--cc-surface)', border: '1px solid var(--cc-line)' }}
        >
          <AgendaPicker
            agendas={agendas}
            meetings={meetings}
            loading={agendasQuery.isLoading}
            value={selectedAgendaId}
            onChange={setPickedAgendaId}
          />
        </section>
      )}

      <CreateAgendaForm
        form={createForm}
        onChange={setCreateForm}
        onSubmit={handleSubmitCreate}
        pending={createAgendaMut.isPending}
        meetings={meetings}
      />
      {meetingsQuery.isError && <Banner tone="warn">Meeting choices could not be loaded. Refresh after recording or library storage recovers.</Banner>}
      {createAgendaMut.isError && (
        <Banner tone="warn">
          {apiMessage(createAgendaMut.error, 'Could not create the agenda.')}
        </Banner>
      )}

      {selectedAgenda ? (
        // Keyed by agenda_id so all per-agenda transient state (item form,
        // delete-confirm flags, import textarea, mutation results) resets
        // automatically when the operator picks a different agenda. This is
        // the structural alternative to a reset-via-effect (which would
        // trigger react-hooks/set-state-in-effect).
        <SelectedAgendaSection
          key={selectedAgenda.agenda_id}
          agenda={selectedAgenda}
          meeting={meetings.find((meeting) => selectedAgenda.source_recording_job_id ? meeting.source_recording_job_id === selectedAgenda.source_recording_job_id : meeting.meeting_asset_id === selectedAgenda.meeting_asset_id)}
          onAgendaDeleted={() => setPickedAgendaId(null)}
        />
      ) : (
        !agendasQuery.isLoading && (
          <EmptyState
            headline="No agendas yet."
            body="Agendas list what a meeting will cover and appear alongside its recording on the public meeting page. Create one with the form above — it stays a private draft until you publish it."
          />
        )
      )}
    </div>
  )
}

/**
 * Per-agenda card + items + bulk actions. Mounted with `key={agenda_id}` so
 * switching agendas remounts the component and clears the local state (item
 * form, per-row delete confirms, import textarea, mutation results) — the
 * structural alternative to a reset-via-effect that would trip
 * react-hooks/set-state-in-effect.
 */
function SelectedAgendaSection({
  agenda,
  meeting,
  onAgendaDeleted,
}: {
  meeting?: AgendaMeetingOption
  agenda: MeetingAgenda
  onAgendaDeleted: () => void
}) {
  const qc = useQueryClient()
  const agendaId = agenda.agenda_id
  const markNow = useMutation({
    mutationFn: (itemId: string) => markAgendaItemNow(agendaId, itemId),
    onSuccess: () => { void qc.invalidateQueries({ queryKey: ['agenda-items', agendaId] }) },
  })

  const invalidateAgendas = () => qc.invalidateQueries({ queryKey: ['meeting-agendas'] })
  const invalidateItems = () => qc.invalidateQueries({ queryKey: ['agenda-items', agendaId] })

  const itemsQuery = useQuery({
    queryKey: ['agenda-items', agendaId],
    queryFn: () => listAgendaItems(agendaId),
  })
  const items = useMemo(() => itemsQuery.data ?? [], [itemsQuery.data])

  // Agenda-level UI state
  const [confirmDeleteAgenda, setConfirmDeleteAgenda] = useState(false)

  const publishMut = useMutation({
    mutationFn: (statusValue: 'draft' | 'published') =>
      patchMeetingAgenda(agendaId, { status: statusValue }),
    onSuccess: () => invalidateAgendas(),
  })

  const deleteAgendaMut = useMutation({
    mutationFn: () => deleteMeetingAgenda(agendaId),
    onSuccess: () => {
      setConfirmDeleteAgenda(false)
      invalidateAgendas()
      onAgendaDeleted()
    },
  })

  // Item-level UI state
  const [itemForm, setItemForm] = useState<ItemFormState>(EMPTY_ITEM_FORM)
  const [confirmDeleteItem, setConfirmDeleteItem] = useState<Record<string, true>>({})

  const createItemMut = useMutation({
    mutationFn: (payload: AgendaItemInput) => createAgendaItem(agendaId, payload),
    onSuccess: () => {
      invalidateItems()
      setItemForm(EMPTY_ITEM_FORM)
    },
  })

  const patchItemMut = useMutation({
    mutationFn: (v: { itemId: string; patch: AgendaItemUpdate }) =>
      patchAgendaItem(agendaId, v.itemId, v.patch),
    onSuccess: () => {
      invalidateItems()
      setItemForm(EMPTY_ITEM_FORM)
    },
  })

  const deleteItemMut = useMutation({
    mutationFn: (itemId: string) => deleteAgendaItem(agendaId, itemId),
    onSuccess: (_data, itemId) => {
      setConfirmDeleteItem((prev) => {
        const next = { ...prev }
        delete next[itemId]
        return next
      })
      invalidateItems()
    },
  })

  // Bulk-action UI state
  const syncMut = useMutation({
    mutationFn: () => syncAgendaFromChapters(agendaId),
    onSuccess: () => invalidateItems(),
  })

  const [importText, setImportText] = useState('')
  const [importPdfFile, setImportPdfFile] = useState<File | null>(null)
  const importMut = useMutation({
    mutationFn: (doc: { body: string | File; contentType: string }) =>
      importAgendaFromDoc(agendaId, doc.body, doc.contentType),
    onSuccess: () => {
      invalidateItems()
      // A PDF import can silently reopen a published agenda to draft
      // (AI/agenda non-negotiables Spec Sec4.2 -- heuristically-guessed
      // items need operator review before they can be public again), so
      // the agenda-level status badge has to refresh too, not just items.
      invalidateAgendas()
      setImportText('')
      setImportPdfFile(null)
    },
  })
  const importedNeedsReview =
    importMut.isSuccess && (importMut.data ?? []).some((item) => (item.confidence ?? 1) < 0.9)

  const handleSubmitItem = () => {
    const order = parseOptionalInt(itemForm.order)
    if (!order.ok || order.value == null) return
    const tc = parseAgendaTime(itemForm.video_timecode_s)
    if (!tc.ok) return
    if (itemForm.editingItemId != null) {
      const patch: AgendaItemUpdate = {
        order: order.value,
        number: itemForm.number.trim() === '' ? null : itemForm.number.trim(),
        title: itemForm.title.trim(),
        video_timecode_s: tc.value,
        doc_anchor: itemForm.doc_anchor.trim() === '' ? null : itemForm.doc_anchor.trim(),
        notes: itemForm.notes.trim() === '' ? null : itemForm.notes,
      }
      patchItemMut.mutate({ itemId: itemForm.editingItemId, patch })
      return
    }
    const payload: AgendaItemInput = {
      item_id: itemForm.item_id.trim(),
      agenda_id: agendaId,
      order: order.value,
      number: itemForm.number.trim() === '' ? null : itemForm.number.trim(),
      title: itemForm.title.trim(),
      video_timecode_s: tc.value,
      doc_anchor: itemForm.doc_anchor.trim() === '' ? null : itemForm.doc_anchor.trim(),
      notes: itemForm.notes.trim() === '' ? null : itemForm.notes,
    }
    createItemMut.mutate(payload)
  }

  const publishError =
    publishMut.isError && publishMut.variables === 'published'
      ? apiMessage(publishMut.error, 'Could not publish the agenda.')
      : null
  const unpublishError =
    publishMut.isError && publishMut.variables === 'draft'
      ? apiMessage(publishMut.error, 'Could not unpublish the agenda.')
      : null
  const deleteAgendaError = deleteAgendaMut.isError
    ? apiMessage(deleteAgendaMut.error, 'Could not delete the agenda.')
    : null

  const importIs415 =
    importMut.isError && importMut.error instanceof ApiError && importMut.error.status === 415
  const importIs422 =
    importMut.isError && importMut.error instanceof ApiError && importMut.error.status === 422

  return (
    <>
      <SelectedAgendaCard
        agenda={agenda}
        meetingTitle={meeting?.title}
        itemCount={items.length}
        onPublish={() => publishMut.mutate('published')}
        onUnpublish={() => publishMut.mutate('draft')}
        onArmDelete={() => setConfirmDeleteAgenda(true)}
        onConfirmDelete={() => deleteAgendaMut.mutate()}
        confirming={confirmDeleteAgenda}
        publishing={publishMut.isPending}
        deleting={deleteAgendaMut.isPending}
        publishError={publishError ?? unpublishError}
        deleteError={deleteAgendaError}
      />

      <AgendaMinutesPanel key={agendaId} agendaId={agendaId} assetId={meeting?.meeting_asset_id ?? agenda.meeting_asset_id} />
      <p className="text-sm">{meeting?.title ?? 'Recording unavailable'} — {meeting?.state ?? 'awaiting library linkage'}. {agenda.source_recording_job_id ? 'Times refer to the original source recording and adjust for packaged trims.' : 'Times refer to packaged playback and remain unchanged when publishing chapters.'} Publish the recording and agenda when ready; draft markers remain private.</p>
      {markNow.isError && <Banner tone="warn">{apiMessage(markNow.error, 'Recording time is unavailable. Enter a reviewed time instead.')}</Banner>}
      {markNow.isSuccess && <Banner tone="ok">Marked the recorded-media position. Correct the time with Edit if needed.</Banner>}
      <section
        aria-label="Agenda items"
        className="space-y-3 rounded-md p-4 text-sm"
        style={{ background: 'var(--cc-surface)', border: '1px solid var(--cc-line)' }}
      >
        <h2 className="text-sm font-semibold">Items</h2>
        <ItemForm
          form={itemForm}
          onChange={setItemForm}
          onSubmit={handleSubmitItem}
          onCancelEdit={() => setItemForm(EMPTY_ITEM_FORM)}
          pending={createItemMut.isPending || patchItemMut.isPending}
          publicPortalUrl={meeting && !meeting.can_mark_now ? publicWatchUrlFor(meeting.meeting_asset_id) : null}
          sourceTime={Boolean(agenda.source_recording_job_id)}
        />
        {createItemMut.isError && (
          <Banner tone="warn">
            {apiMessage(createItemMut.error, 'Could not create the item.')}
          </Banner>
        )}
        {patchItemMut.isError && (
          <Banner tone="warn">
            {apiMessage(patchItemMut.error, 'Could not save the item.')}
          </Banner>
        )}
        {deleteItemMut.isError && (
          <Banner tone="warn">
            {apiMessage(deleteItemMut.error, 'Could not delete the item.')}
          </Banner>
        )}
        {itemsQuery.isLoading ? (
          <p className="text-xs" style={{ color: 'var(--cc-ink-3)' }}>
            Loading items…
          </p>
        ) : itemsQuery.isError && items.length === 0 ? (
          <Banner tone="warn">{apiMessage(itemsQuery.error, 'Could not load items.')}</Banner>
        ) : (
          <>
            {/* UX-4 fix: when a background refetch fails but a prior cache is
               still on screen, react-query keeps the old rows AND flips
               isError true. Without this pill the operator thinks the table
               is fresh. The Banner above only renders when no cache exists. */}
            {itemsQuery.isError && items.length > 0 && (
              <p
                role="status"
                aria-live="polite"
                className="rounded-md px-2 py-1 text-xs"
                style={{
                  background: 'var(--cc-warn-soft)',
                  border: '1px solid var(--cc-warn)',
                  color: 'var(--cc-ink-2)',
                }}
              >
                Items list may be stale — last refresh failed
                {itemsQuery.error
                  ? ` (${apiMessage(itemsQuery.error, 'reason unknown')}).`
                  : '.'}{' '}
                The rows below are from the previous successful load.
              </p>
            )}
            <ItemsTable
              items={items}
              onMarkNow={meeting?.can_mark_now ? (itemId) => markNow.mutate(itemId) : undefined}
              marking={markNow.isPending}
              onEdit={(item) => setItemForm(formFromItem(item))}
              onArmDelete={(itemId) =>
                setConfirmDeleteItem((prev) => ({ ...prev, [itemId]: true }))
              }
              onConfirmDelete={(itemId) => deleteItemMut.mutate(itemId)}
              confirming={confirmDeleteItem}
              deleting={
                deleteItemMut.isPending && deleteItemMut.variables
                  ? deleteItemMut.variables
                  : null
              }
            />
          </>
        )}
      </section>

      <section
        aria-label="Bulk actions"
        className="space-y-3 rounded-md p-4 text-sm"
        style={{ background: 'var(--cc-surface)', border: '1px solid var(--cc-line)' }}
      >
        <h2 className="text-sm font-semibold">Bulk actions</h2>

        <div className="space-y-2">
          <div className="flex flex-wrap items-center gap-2">
            <button
              type="button"
              aria-label="Sync agenda items from chapter markers"
              disabled={syncMut.isPending}
              onClick={() => syncMut.mutate()}
              className="rounded-md px-3 py-1.5 text-xs font-semibold disabled:opacity-50"
              style={{ background: 'var(--cc-brand)', color: 'var(--cc-brand-ink)' }}
            >
              {syncMut.isPending ? 'Syncing…' : 'Sync from chapters'}
            </button>
            <span className="text-xs" style={{ color: 'var(--cc-ink-3)' }}>
              Seeds draft items from the meeting asset&apos;s chapter markers. Safe to re-run:
              operator edits at the same order are preserved.
            </span>
          </div>
          {syncMut.isSuccess && (
            <Banner tone="ok">
              {syncMut.data?.chapter_count === 0
                ? 'No chapter markers are saved on this meeting asset.'
                : syncMut.data?.added_items.length
                  ? `Added ${syncMut.data.added_items.length} new item${syncMut.data.added_items.length === 1 ? '' : 's'} from ${syncMut.data.chapter_count} chapter marker${syncMut.data.chapter_count === 1 ? '' : 's'}.`
                  : `No new items added from ${syncMut.data?.chapter_count ?? 0} chapter marker${syncMut.data?.chapter_count === 1 ? '' : 's'}.`}
              {syncMut.data?.skipped_existing.length
                ? ` ${syncMut.data.skipped_existing.length} skipped because an agenda item already occupies that position: ${syncMut.data.skipped_existing.map((skip) => `${skip.chapter_title} → ${skip.existing_item_title}`).join('; ')}.`
                : ''}
            </Banner>
          )}
          {syncMut.isError && (
            <Banner tone="warn">
              {apiMessage(syncMut.error, 'Could not sync from chapters.')}
            </Banner>
          )}
        </div>

        <div className="space-y-2">
          <label className="grid gap-1 text-xs">
            <span style={{ color: 'var(--cc-ink-3)' }}>
              Import from doc (paste plain-text agenda, one item per line)
            </span>
            <textarea
              aria-label="Plain-text agenda to import"
              rows={5}
              value={importText}
              placeholder={
                '1. Call to order\n2. Approval of minutes\n3. Public comment\n4. New business'
              }
              onChange={(e) => setImportText(e.target.value)}
              className="rounded-md px-2 py-1.5"
              style={INPUT_STYLE}
            />
          </label>
          <div className="flex flex-wrap items-center gap-2">
            <button
              type="button"
              aria-label="Import agenda items from pasted text"
              disabled={importMut.isPending || importText.trim().length === 0}
              onClick={() =>
                importMut.mutate({ body: importText, contentType: 'text/plain' })
              }
              className="rounded-md px-3 py-1.5 text-xs font-semibold disabled:opacity-50"
              style={{ background: 'var(--cc-brand)', color: 'var(--cc-brand-ink)' }}
            >
              {importMut.isPending && importText.trim().length > 0 ? 'Importing…' : 'Import'}
            </button>
            <span className="text-xs" style={{ color: 'var(--cc-ink-3)' }}>
              Taken literally, one item per line — nothing to review.
            </span>
          </div>

          <label className="grid gap-1 text-xs">
            <span style={{ color: 'var(--cc-ink-3)' }}>
              Or upload a PDF agenda (best-effort: numbered items, ALL-CAPS section headings, and
              call-time markers are recognized; each imported item is scored with a confidence so
              you can spot guesses that need a check)
            </span>
            <input
              type="file"
              accept="application/pdf"
              aria-label="PDF agenda to import"
              onChange={(e) => setImportPdfFile(e.target.files?.[0] ?? null)}
              className="text-xs"
            />
          </label>
          <div className="flex flex-wrap items-center gap-2">
            <button
              type="button"
              aria-label="Import agenda items from uploaded PDF"
              disabled={importMut.isPending || importPdfFile == null}
              onClick={() =>
                importPdfFile &&
                importMut.mutate({ body: importPdfFile, contentType: 'application/pdf' })
              }
              className="rounded-md px-3 py-1.5 text-xs font-semibold disabled:opacity-50"
              style={{ background: 'var(--cc-brand)', color: 'var(--cc-brand-ink)' }}
            >
              {importMut.isPending && importPdfFile != null ? 'Importing…' : 'Import PDF'}
            </button>
            {agenda.status === 'published' && (
              <span className="text-xs" style={{ color: 'var(--cc-ink-3)' }}>
                This agenda is published. A PDF import will move it back to draft until you
                review and republish.
              </span>
            )}
          </div>

          {importMut.isSuccess && (
            <Banner tone={importedNeedsReview ? 'warn' : 'ok'}>
              Imported {importMut.data?.length ?? 0} item
              {(importMut.data?.length ?? 0) === 1 ? '' : 's'}.
              {importedNeedsReview
                ? ' Some items were a best-effort guess (see the Confidence marks below) — review them before publishing.'
                : ''}
            </Banner>
          )}
          {importMut.isError && (
            <Banner tone="warn">
              {importIs415
                ? 'Only plain-text and PDF agendas import here today (DOCX and other formats are a follow-up).'
                : importIs422
                  ? apiMessage(
                      importMut.error,
                      "Couldn't find any recognizable items in that PDF. Try pasting the agenda's text instead.",
                    )
                  : apiMessage(importMut.error, 'Could not import the agenda.')}
            </Banner>
          )}
        </div>

        <ExternalImportSection
          agendaId={agendaId}
          agendaStatus={agenda.status ?? 'draft'}
          onImported={() => {
            invalidateItems()
            // Mirrors the PDF-import path above: an external import can
            // reopen a published agenda to draft (AI/agenda non-negotiables
            // Spec Sec4.2 — civiccast/agenda_import/mapper.py), so the
            // agenda-level status badge has to refresh too, not just items.
            invalidateAgendas()
          }}
        />

        <CivicSuiteBridgeCard />
      </section>
    </>
  )
}

function AgendaPicker({
  agendas,
  meetings,
  loading,
  value,
  onChange,
}: {
  agendas: MeetingAgenda[]
  meetings: AgendaMeetingOption[]
  loading: boolean
  value: string | null
  onChange: (id: string) => void
}) {
  const idSel = useId()
  return (
    <label htmlFor={idSel} className="grid gap-1 text-xs">
      <span style={{ color: 'var(--cc-ink-3)' }}>Pick an agenda</span>
      <select
        id={idSel}
        aria-label="Pick an agenda"
        value={value ?? ''}
        onChange={(e) => onChange(e.target.value)}
        className="rounded-md px-2 py-1.5"
        style={INPUT_STYLE}
      >
        {/* The picker is hidden by the parent when `agendas.length === 0`
           (UX-3), so the only placeholder cases that actually render are
           "loading" and "pick one of N". */}
        <option value="" disabled>
          {loading ? 'Loading agendas…' : 'Pick…'}
        </option>
        {agendas.map((a) => (
          <option key={a.agenda_id} value={a.agenda_id}>
            {meetings.find((meeting) => a.source_recording_job_id ? meeting.source_recording_job_id === a.source_recording_job_id : meeting.meeting_asset_id === a.meeting_asset_id)?.title ?? 'Recording unavailable'} ({a.status ?? 'draft'})
          </option>
        ))}
      </select>
    </label>
  )
}
