import { fmtRangeLabel } from './schedule-format'

import { useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  ApiError,
  cancelSchedule,
  commitToAir,
  getStaffIdentity,
  getStationTimeZone,
  listSchedule,
  prepareCommit,
} from '../api/client'
import { hasOperatorRole } from '../auth/roles'
import { ConfirmDialog, type PendingConfirm } from '../components/ConfirmDialog'
import { ScheduleDrawer } from '../components/schedule/ScheduleDrawer'
import { useToast } from '../components/toast-context'
import {
  MODE_META,
  SCHEDULE_STATE_META,
  type ScheduleItem,
  type ScheduleMode,
  type ScheduleState,
} from '../types/schedule'
import type { CommitToAirPlan } from '../types/api.generated'
import { DryRunReview } from './CommitToAirPanel'
import { groupByDay } from './scheduleGrouping'
import {
  dateFromStationDateKey,
  effectiveStationTimeZone,
  formatStationInstant,
  stationDateKey,
  stationDateParts,
} from '../lib/station-time'

function apiMessage(error: unknown, fallback: string): string {
  if (error instanceof ApiError) return error.detail ?? fallback
  if (error instanceof Error) return error.message
  return fallback
}

type ViewMode = 'week' | 'list'

// Default visible hours for the week grid. Audit-team v0.3.0 surfaced
// (QA-003 / ENG-006 / UX-002) that hard-coded 6 AM–10 PM was hiding
// real-world early-morning + overnight broadcast events off-grid. The
// grid now auto-expands to cover every event in the visible week and
// only falls back to this default range when the week is empty.
const DEFAULT_HOURS_START = 6
const DEFAULT_HOURS_END = 22
const ROW_PX = 56

function startOfWeek(d: Date, timeZone: string): Date {
  const out = dateFromStationDateKey(stationDateKey(d, timeZone))
  // The Date stores a station calendar date at UTC midnight. UTC accessors
  // keep the week grid independent of the operator's browser timezone.
  const dow = out.getUTCDay() === 0 ? 7 : out.getUTCDay()
  out.setUTCDate(out.getUTCDate() - (dow - 1))
  return out
}

function addDays(d: Date, n: number): Date {
  const out = new Date(d)
  out.setUTCDate(out.getUTCDate() + n)
  return out
}

function fmtHourLabel(h: number): string {
  if (h === 0) return '12 am'
  if (h === 12) return '12 pm'
  return h < 12 ? `${h} am` : `${h - 12} pm`
}

function sameDay(a: Date, b: Date): boolean {
  return (
    a.getUTCFullYear() === b.getUTCFullYear() &&
    a.getUTCMonth() === b.getUTCMonth() &&
    a.getUTCDate() === b.getUTCDate()
  )
}

interface EventLayout {
  item: ScheduleItem
  dayIdx: number
  topPx: number
  heightPx: number
}

function computeVisibleHours(
  items: ScheduleItem[],
  weekStart: Date,
  timeZone: string,
): { start: number; end: number; list: number[] } {
  // QA-003 / ENG-006 (audit-team v0.3.0): the week grid auto-extends
  // to span every event in the visible week so early-morning and
  // overnight broadcasts cannot render off-grid. Falls back to the
  // default business-hours range when the week is empty.
  let minHour = DEFAULT_HOURS_START
  let maxHour = DEFAULT_HOURS_END
  const weekEnd = new Date(weekStart)
  weekEnd.setUTCDate(weekEnd.getUTCDate() + 7)
  for (const item of items) {
    const start = new Date(item.scheduled_at)
    const itemDay = dateFromStationDateKey(stationDateKey(start, timeZone))
    if (itemDay < weekStart || itemDay >= weekEnd) continue
    const parts = stationDateParts(start, timeZone)
    if (!parts) continue
    const startHour = parts.hour + parts.minute / 60
    const durSec =
      item.mode === 'embargo' || !item.duration_seconds
        ? 30 * 60
        : item.duration_seconds
    const end = new Date(start.getTime() + durSec * 1000)
    const endDay = dateFromStationDateKey(stationDateKey(end, timeZone))
    const endParts = stationDateParts(end, timeZone)
    const endHour = endParts && sameDay(endDay, itemDay)
      ? endParts.hour + endParts.minute / 60
      : 24
    if (startHour < minHour) minHour = Math.max(0, Math.floor(startHour))
    if (endHour > maxHour) maxHour = Math.min(24, Math.ceil(endHour))
  }
  // Clamp + ensure non-empty range.
  minHour = Math.max(0, Math.min(minHour, 23))
  maxHour = Math.max(minHour + 1, Math.min(maxHour, 24))
  const list: number[] = []
  for (let h = minHour; h <= maxHour; h++) list.push(h)
  return { start: minHour, end: maxHour, list }
}

function layoutEvents(
  items: ScheduleItem[],
  weekStart: Date,
  hoursStart: number,
  timeZone: string,
): EventLayout[] {
  return items.flatMap((item) => {
    const start = new Date(item.scheduled_at)
    const itemDay = dateFromStationDateKey(stationDateKey(start, timeZone))
    const parts = stationDateParts(start, timeZone)
    if (!parts || Number.isNaN(itemDay.getTime())) return []
    const dayIdx = Math.floor(
      (itemDay.getTime() - weekStart.getTime()) / (24 * 3600 * 1000),
    )
    if (dayIdx < 0 || dayIdx > 6) return []
    const hour = parts.hour + parts.minute / 60
    const top = (hour - hoursStart) * ROW_PX
    const durSec =
      item.mode === 'embargo' || !item.duration_seconds
        ? 30 * 60 // embargo renders as a 30-min marker
        : item.duration_seconds
    const height = Math.max(28, (durSec / 3600) * ROW_PX)
    return [{ item, dayIdx, topPx: top, heightPx: height }]
  })
}

function ModeChip({ mode }: { mode: ScheduleMode }) {
  const meta = MODE_META[mode]
  const palette: Record<ScheduleMode, { bg: string; fg: string }> = {
    premiere: { bg: 'var(--cc-brand-soft)', fg: 'var(--cc-brand-2)' },
    embargo: { bg: 'var(--cc-warn-soft)', fg: 'var(--cc-warn)' },
  }
  const tone = palette[mode]
  return (
    <span
      className="inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wider"
      style={{ background: tone.bg, color: tone.fg }}
    >
      {meta.label}
    </span>
  )
}

function StateChip({ state }: { state: ScheduleState }) {
  // Defensive fallback: if the backend ever ships a state value the
  // frontend hasn't been updated to recognize, render the raw string with
  // neutral styling rather than crash the screen. The contract test
  // tests/test_fe_be_state_contract.py prevents the drift from landing,
  // but the fallback is the second line of defense.
  const meta = SCHEDULE_STATE_META[state] ?? {
    label: String(state),
    tone: 'neutral' as const,
  }
  const palette: Record<
    'ok' | 'warn' | 'err' | 'info' | 'neutral',
    { bg: string; fg: string }
  > = {
    ok: { bg: 'var(--cc-ok-soft)', fg: 'var(--cc-ok)' },
    warn: { bg: 'var(--cc-warn-soft)', fg: 'var(--cc-warn)' },
    err: { bg: 'var(--cc-err-soft)', fg: 'var(--cc-err)' },
    info: { bg: 'var(--cc-info-soft)', fg: 'var(--cc-info)' },
    neutral: { bg: 'var(--cc-surface-3)', fg: 'var(--cc-ink-2)' },
  }
  const tone = palette[meta.tone]
  return (
    <span
      className="inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[10px] font-medium"
      style={{ background: tone.bg, color: tone.fg }}
    >
      {meta.label}
    </span>
  )
}

// Commit-to-Air only applies to premiere items (embargo entries release
// through a separate single-moment mechanism and are never committed — see
// civiccast/schedule/commit_service.py's "no air duration" rejection). Only
// a premiere item's `scheduled` vs `published` state maps to "not yet
// visible" vs "visible to residents"; embargo's `scheduled` state means
// something else entirely, so this stays scoped to premiere.
//
// Field evidence (2026-08): "Schedule premiere" succeeded (item state
// `scheduled`) but the item never appeared on the portal's Coming Up widget
// — a separate playout commit was required and the console had no button
// for it. This note tells the operator the truth before they tell a
// resident to tune in.
function VisibilityNote({ item }: { item: ScheduleItem }) {
  if (item.mode !== 'premiere') return null
  if (item.state === 'scheduled') {
    return (
      <span className="text-[11px]" style={{ color: 'var(--cc-warn, var(--cc-ink-2))' }}>
        Not yet visible to residents
      </span>
    )
  }
  if (item.state === 'published') {
    return (
      <span className="text-[11px]" style={{ color: 'var(--cc-ok, var(--cc-ink-2))' }}>
        Visible to residents
      </span>
    )
  }
  return null
}

function ErrorState({ error, onRetry }: { error: Error; onRetry: () => void }) {
  const isApiError = error instanceof ApiError
  const is503 = isApiError && error.status === 503
  return (
    <div
      role="alert"
      className="mx-6 my-6 flex flex-col gap-2 rounded-md p-4"
      style={{ background: 'var(--cc-err-soft)', color: 'var(--cc-err)' }}
    >
      <div className="text-sm font-semibold">
        {is503 ? 'Durable storage is not ready.' : 'Could not load schedule.'}
      </div>
      <div className="text-xs" style={{ color: 'var(--cc-ink-2)' }}>
        {isApiError && error.detail ? error.detail : error.message}
      </div>
      <div className="text-xs" style={{ color: 'var(--cc-ink-2)' }}>
        <strong>Next step.</strong>{' '}
        {is503
          ? 'Open Setup, prepare durable storage, then return here.'
          : 'Try again, or check the server logs for more detail.'}
      </div>
      <div>
        {is503 && (
          <a
            href="#/setup"
            className="mr-2 inline-flex rounded-md px-3 py-1.5 text-xs font-medium"
            style={{ background: 'var(--cc-brand)', color: 'var(--cc-brand-ink)' }}
          >
            Go to Setup
          </a>
        )}
        <button
          type="button"
          onClick={onRetry}
          className="rounded-md px-3 py-1.5 text-xs font-medium"
          style={{ background: 'var(--cc-surface)', border: '1px solid var(--cc-line)' }}
        >
          Retry
        </button>
      </div>
    </div>
  )
}

function EmptyState({ onNew }: { onNew: () => void }) {
  return (
    <div
      className="mx-6 my-10 flex flex-col items-center gap-3 rounded-md p-10 text-center"
      style={{ background: 'var(--cc-surface-2)', border: '1px dashed var(--cc-line-strong)' }}
    >
      <div className="text-sm font-semibold">Nothing scheduled this week.</div>
      <div className="max-w-md text-xs" style={{ color: 'var(--cc-ink-3)' }}>
        Schedule a recording with Premiere, then publish it to residents separately.
        Embargo release is unavailable in this build. Conflicts are checked when you submit.
      </div>
      <button
        type="button"
        onClick={onNew}
        className="rounded-md px-4 py-2 text-xs font-semibold"
        style={{ background: 'var(--cc-brand)', color: 'var(--cc-brand-ink)' }}
      >
        New scheduled item
      </button>
    </div>
  )
}

function LoadingState() {
  return (
    <div className="mx-6 my-6 flex flex-col gap-2">
      {[0, 1, 2].map((i) => (
        <div
          key={i}
          className="h-16 w-full animate-pulse rounded-md"
          style={{ background: 'var(--cc-surface-2)' }}
        />
      ))}
    </div>
  )
}

interface WeekGridProps {
  weekStart: Date
  items: ScheduleItem[]
  timeZone: string
  onItemSelect: (item: ScheduleItem) => void
}

function WeekGrid({ weekStart, items, timeZone, onItemSelect }: WeekGridProps) {
  // QA-003 / ENG-006 (audit-team v0.3.0): hour range is computed from
  // the visible week's actual events so an early-morning rebroadcast or
  // overnight emergency-meeting recording cannot render off-grid.
  const hours = useMemo(
    () => computeVisibleHours(items, weekStart, timeZone),
    [items, weekStart, timeZone],
  )
  const layouts = useMemo(
    () => layoutEvents(items, weekStart, hours.start, timeZone),
    [items, weekStart, hours.start, timeZone],
  )
  const todayIdx = useMemo(() => {
    const today = dateFromStationDateKey(stationDateKey(new Date(), timeZone))
    for (let i = 0; i < 7; i++) {
      if (sameDay(today, addDays(weekStart, i))) return i
    }
    return -1
  }, [weekStart, timeZone])

  // The hour-rail and the body share the same row count; height is
  // (hours.list.length - 1) * ROW_PX because hour labels mark
  // boundaries (N labels = N-1 row bands). Guard against a
  // pathological 0-height when the range computation yields a single
  // hour (it shouldn't, but the clamp in computeVisibleHours ensures).
  const bandCount = Math.max(1, hours.list.length - 1)
  const totalHeight = bandCount * ROW_PX

  return (
    <div
      className="mx-6 my-4 overflow-x-auto rounded-md"
      style={{ background: 'var(--cc-surface)', border: '1px solid var(--cc-line)' }}
    >
      {(hours.start !== DEFAULT_HOURS_START || hours.end !== DEFAULT_HOURS_END) && (
        <div
          className="px-3 py-2 text-[11px]"
          style={{
            background: 'var(--cc-surface-2)',
            color: 'var(--cc-ink-2)',
            borderBottom: '1px solid var(--cc-line)',
          }}
          aria-live="polite"
        >
          Showing {fmtHourLabel(hours.start)}–{fmtHourLabel(hours.end)} (auto-extended to cover events outside business hours).
        </div>
      )}
      <div
        className="grid"
        style={{
          gridTemplateColumns: '60px repeat(7, minmax(120px, 1fr))',
          minWidth: 720,
        }}
      >
        <div
          className="cc-mono px-2 py-2 text-[10px] uppercase tracking-wider"
          style={{ color: 'var(--cc-ink-3)', borderBottom: '1px solid var(--cc-line)' }}
        >
          Station
        </div>
        {Array.from({ length: 7 }, (_, i) => {
          const d = addDays(weekStart, i)
          const isToday = i === todayIdx
          return (
            <div
              key={i}
              className="px-3 py-2 text-xs"
              style={{
                borderBottom: '1px solid var(--cc-line)',
                background: isToday ? 'var(--cc-brand-soft)' : undefined,
                color: isToday ? 'var(--cc-brand-2)' : 'var(--cc-ink-2)',
                fontWeight: isToday ? 600 : 500,
              }}
            >
              <span>{d.toLocaleDateString(undefined, { timeZone: 'UTC', weekday: 'short' })}</span>{' '}
              <span className="cc-mono cc-tabular text-[11px]" style={{ color: 'var(--cc-ink-3)' }}>
                {d.getUTCMonth() + 1}/{String(d.getUTCDate()).padStart(2, '0')}
              </span>
            </div>
          )
        })}

        <div
          className="relative"
          style={{ gridColumn: '1 / 2', height: totalHeight }}
        >
          {hours.list.map((h, i) => (
            <div
              key={h}
              className="cc-mono absolute right-2 text-[10px]"
              style={{
                top: i * ROW_PX,
                color: 'var(--cc-ink-3)',
                transform: 'translateY(-6px)',
              }}
            >
              {fmtHourLabel(h)}
            </div>
          ))}
        </div>

        <div
          className="relative"
          style={{
            gridColumn: '2 / 9',
            height: totalHeight,
            background: 'var(--cc-surface-2)',
            overflow: 'hidden',
          }}
        >
          {hours.list.slice(0, -1).map((h, i) => (
            <div
              key={h}
              className="absolute left-0 right-0"
              style={{
                top: i * ROW_PX,
                height: ROW_PX,
                borderTop: '1px solid var(--cc-line)',
              }}
            />
          ))}
          <div
            className="absolute inset-0 grid"
            style={{ gridTemplateColumns: 'repeat(7, 1fr)' }}
          >
            {Array.from({ length: 7 }, (_, i) => (
              <div
                key={i}
                style={{
                  borderRight: i < 6 ? '1px solid var(--cc-line)' : undefined,
                  background: i === todayIdx ? 'var(--cc-brand-soft)' : undefined,
                }}
              />
            ))}
          </div>
          {layouts.map(({ item, dayIdx, topPx, heightPx }) => {
            const palette: Record<ScheduleMode, { bg: string; fg: string; border: string }> = {
              premiere: {
                bg: 'var(--cc-brand-soft)',
                fg: 'var(--cc-brand-2)',
                border: 'var(--cc-brand)',
              },
              embargo: {
                bg: 'var(--cc-warn-soft)',
                fg: 'var(--cc-warn)',
                border: 'var(--cc-warn)',
              },
            }
            const tone = palette[item.mode]
            const dim = item.state === 'cancelled'
            // UX-003: render the human title as the primary string;
            // asset_id is demoted to a tiny meta line for engineer-
            // debug recognizability.
            const display = item.asset_title?.trim() || item.asset_id
            const ariaLabel = `${MODE_META[item.mode].label} · ${display} · ${fmtRangeLabel(item, timeZone)} on ${item.channel_id}`
            return (
              <button
                type="button"
                key={item.id}
                onClick={() => onItemSelect(item)}
                aria-label={ariaLabel}
                className="absolute overflow-hidden rounded-md p-2 text-left text-[11px]"
                style={{
                  top: topPx,
                  height: heightPx,
                  left: `calc(${dayIdx} * (100% / 7) + 4px)`,
                  width: `calc(100% / 7 - 8px)`,
                  background: tone.bg,
                  color: tone.fg,
                  borderLeft: `3px solid ${tone.border}`,
                  opacity: dim ? 0.55 : 1,
                  textDecoration: dim ? 'line-through' : undefined,
                  cursor: 'pointer',
                }}
                title={ariaLabel}
              >
                <div className="flex items-center justify-between gap-1">
                  <span className="text-[9px] font-semibold uppercase tracking-wider">
                    {MODE_META[item.mode].label}
                  </span>
                  <span className="cc-mono text-[9px]" style={{ color: 'var(--cc-ink-3)' }}>
                    {item.channel_id}
                  </span>
                </div>
                <div className="cc-truncate mt-0.5 font-medium" style={{ color: 'var(--cc-ink)' }}>
                  {display}
                </div>
                <div
                  className="cc-mono cc-truncate text-[9px]"
                  style={{ color: 'var(--cc-ink-3)' }}
                >
                  {item.asset_id}
                </div>
                <div className="cc-mono cc-tabular mt-0.5 text-[10px]">
                  {fmtRangeLabel(item, timeZone)}
                </div>
              </button>
            )
          })}
        </div>
      </div>
    </div>
  )
}

interface ScheduleListProps {
  items: ScheduleItem[]
  timeZone?: string
  onCancel: (item: ScheduleItem) => void
}

// Exported for ScheduleList.publish.test.tsx (the "Publish to residents"
// container test), mirroring how CommitToAirPanel.tsx exports DryRunReview /
// CommitReportRow for the same reason.
export function ScheduleList({ items, timeZone, onCancel }: ScheduleListProps) {
  const stationTimeZone = timeZone || Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC'
  const { groups, unreadable } = useMemo(() => groupByDay(items, stationTimeZone), [items, stationTimeZone])
  const today = dateFromStationDateKey(stationDateKey(new Date(), stationTimeZone))
  const queryClient = useQueryClient()
  const toast = useToast()

  // Publish to residents = the Commit-to-Air gate (civiccast/schedule/
  // playout_router.py's prepare-commit + commit), surfaced per-item right
  // where the operator scheduled it, instead of requiring a trip to the
  // separate Channel Ops screen the field evidence showed volunteers never
  // find. Role-gated the same way Channel Ops gates it.
  const staffIdentityQuery = useQuery({
    queryKey: ['staff-identity'],
    queryFn: getStaffIdentity,
  })
  const canPublish =
    staffIdentityQuery.isSuccess &&
    (hasOperatorRole(staffIdentityQuery.data, 'publish_operator') ||
      hasOperatorRole(staffIdentityQuery.data, 'setup_admin'))

  const [reviewItemId, setReviewItemId] = useState<string | null>(null)

  const prepareMutation = useMutation<CommitToAirPlan, Error, ScheduleItem>({
    mutationFn: (item) =>
      prepareCommit({
        channel_id: item.channel_id,
        // Manually-scheduled premieres never materialize a SlotOccurrence
        // (that link only ever runs slot -> item), so they have no "real"
        // occurrence id. occurrence_id is opaque provenance the commit path
        // never looks up (playout_router.py / commit_service.py) — this
        // reuses the exact `manual:<schedule_item_id>` convention
        // civiccast/programlog/router.py already established (F-RC4-2) for
        // surfacing these same items to Channel Ops.
        occurrence_id: `manual:${item.id}`,
        schedule_item_id: item.id,
      }),
  })

  const commitMutation = useMutation({
    mutationFn: (plan: CommitToAirPlan) =>
      commitToAir({
        channel_id: plan.channel_id,
        occurrence_id: plan.occurrence_id,
        schedule_item_id: plan.schedule_item_id,
        plan_id: plan.plan_id,
      }),
    onSuccess: (_report, plan) => {
      setReviewItemId(null)
      prepareMutation.reset()
      void queryClient.invalidateQueries({ queryKey: ['staff-schedule'] })
      toast.push({
        tone: 'success',
        message: 'Visible to residents.',
        detail: plan.title,
      })
    },
  })

  function startPublish(item: ScheduleItem) {
    setReviewItemId(item.id)
    prepareMutation.mutate(item)
  }
  function cancelPublishReview() {
    setReviewItemId(null)
    prepareMutation.reset()
  }

  return (
    <div className="px-6 py-4">
      {staffIdentityQuery.isSuccess && !canPublish && (
        <div
          className="mb-4 rounded-md px-3 py-2 text-xs"
          style={{ background: 'var(--cc-surface-2)', color: 'var(--cc-ink-2)' }}
        >
          You can view the schedule here. Publishing a premiere to residents requires the
          publish operator or setup admin role.
        </div>
      )}
      {unreadable.length > 0 && (
        <div
          role="status"
          className="mb-4 rounded-md px-3 py-2 text-xs"
          style={{
            background: 'var(--cc-warn-soft, var(--cc-surface))',
            border: '1px solid var(--cc-warn, var(--cc-line))',
            color: 'var(--cc-ink-2)',
          }}
        >
          {unreadable.length === 1
            ? '1 scheduled item has an air time this station cannot read, so it is not shown below.'
            : `${unreadable.length} scheduled items have air times this station cannot read, so they are not shown below.`}{' '}
          The rest of the schedule is complete and unaffected. Cancel and
          re-schedule them, or send this list to support:{' '}
          <span className="cc-mono">
            {unreadable.map((it) => it.id).join(', ')}
          </span>
        </div>
      )}
      {groups.map((g) => {
        const isToday = sameDay(g.day, today)
        return (
          <div key={g.key} className="mb-6">
            <div
              className="mb-2 flex items-center gap-2 text-xs font-semibold uppercase tracking-wider"
              style={{ color: 'var(--cc-ink-3)' }}
            >
              <span>
                {g.day.toLocaleDateString(undefined, {
                  timeZone: 'UTC',
                  weekday: 'short',
                  month: 'short',
                  day: 'numeric',
                })}
              </span>
              {isToday && (
                <span
                  className="rounded-full px-1.5 py-0.5 text-[9px] uppercase"
                  style={{ background: 'var(--cc-brand-soft)', color: 'var(--cc-brand-2)' }}
                >
                  Today
                </span>
              )}
            </div>
            <div
              className="overflow-hidden rounded-md"
              style={{ background: 'var(--cc-surface)', border: '1px solid var(--cc-line)' }}
            >
              {g.items.map((it, idx) => {
                // UX-003: human title primary, asset_id meta below.
                const display = it.asset_title?.trim() || it.asset_id
                const reviewing = reviewItemId === it.id
                const checking = reviewing && prepareMutation.isPending
                const plan =
                  reviewing && prepareMutation.data?.schedule_item_id === it.id
                    ? prepareMutation.data
                    : null
                return (
                  <div
                    key={it.id}
                    style={{ borderTop: idx > 0 ? '1px solid var(--cc-line)' : undefined }}
                  >
                    <div className="flex flex-wrap items-center gap-3 px-3 py-3 text-xs">
                      <span
                        className="cc-mono cc-tabular w-32 shrink-0"
                        style={{ color: 'var(--cc-ink-2)' }}
                      >
                        {fmtRangeLabel(it, stationTimeZone)}
                      </span>
                      <ModeChip mode={it.mode} />
                      <StateChip state={it.state} />
                      <div className="min-w-0 flex-1">
                        <div className="cc-truncate font-medium">{display}</div>
                        {it.asset_title && (
                          <div
                            className="cc-mono cc-truncate text-[10px]"
                            style={{ color: 'var(--cc-ink-3)' }}
                          >
                            {it.asset_id}
                          </div>
                        )}
                      </div>
                      <VisibilityNote item={it} />
                      <span
                        className="cc-mono text-[11px]"
                        style={{ color: 'var(--cc-ink-3)' }}
                      >
                        {it.channel_id}
                      </span>
                      {it.mode === 'premiere' && it.state === 'scheduled' && !reviewing && (
                        <button
                          type="button"
                          disabled={!canPublish}
                          onClick={() => startPublish(it)}
                          className="rounded-md px-2.5 py-1 text-[11px] font-semibold"
                          style={{
                            background: !canPublish ? 'var(--cc-surface-3)' : 'var(--cc-brand)',
                            color: !canPublish ? 'var(--cc-ink-3)' : 'var(--cc-brand-ink)',
                            cursor: !canPublish ? 'not-allowed' : 'pointer',
                          }}
                        >
                          Publish to residents
                        </button>
                      )}
                      {checking && (
                        <span className="text-[11px]" style={{ color: 'var(--cc-ink-3)' }}>
                          Checking…
                        </span>
                      )}
                      {it.state === 'scheduled' && (
                        <button
                          type="button"
                          onClick={() => onCancel(it)}
                          className="rounded-md px-2.5 py-1 text-[11px] font-medium"
                          style={{
                            border: '1px solid var(--cc-line)',
                            color: 'var(--cc-ink-2)',
                            background: 'var(--cc-surface)',
                          }}
                        >
                          Cancel
                        </button>
                      )}
                    </div>
                    {reviewing && prepareMutation.isError && (
                      <div
                        role="alert"
                        className="mx-3 mb-3 rounded-md p-2 text-xs"
                        style={{ background: 'var(--cc-err-soft)', color: 'var(--cc-err)' }}
                      >
                        {apiMessage(
                          prepareMutation.error,
                          'Could not check whether this is ready to publish.',
                        )}
                      </div>
                    )}
                    {plan && (
                      <div className="px-3 pb-3">
                        <DryRunReview
                          plan={plan}
                          timeZone={stationTimeZone}
                          canManage={canPublish}
                          committing={commitMutation.isPending}
                          onApprove={() => commitMutation.mutate(plan)}
                          onCancel={cancelPublishReview}
                          approveLabel="Publish to residents"
                          committingLabel="Publishing…"
                          roleGateLabel="Publishing to residents requires the publish operator or setup admin role."
                        />
                        {commitMutation.isError && (
                          <div
                            role="alert"
                            className="mt-2 rounded-md p-2 text-xs"
                            style={{ background: 'var(--cc-err-soft)', color: 'var(--cc-err)' }}
                          >
                            {apiMessage(
                              commitMutation.error,
                              'Could not publish this to residents.',
                            )}
                          </div>
                        )}
                      </div>
                    )}
                  </div>
                )
              })}
            </div>
          </div>
        )
      })}
    </div>
  )
}

// UX-002 (audit-team v0.3.0): default Schedule view to List on small
// viewports. The week grid still works on phones (interactive event
// blocks + horizontal scroll) but the list shape is more useful when
// reading on a 375px screen.
function _initialView(): ViewMode {
  if (typeof window === 'undefined') return 'week'
  return window.innerWidth < 768 ? 'list' : 'week'
}

export function ScheduleScreen() {
  const stationProfileQuery = useQuery({
    queryKey: ['station-timezone'],
    queryFn: getStationTimeZone,
    retry: false,
  })
  const stationTimeZone = effectiveStationTimeZone(stationProfileQuery.data?.station_timezone)
  const [view, setView] = useState<ViewMode>(() => _initialView())
  const [weekSelection, setWeekSelection] = useState<{ timeZone: string; start: Date }>(() => ({
    timeZone: 'UTC',
    start: startOfWeek(new Date(), 'UTC'),
  }))
  const weekStart = weekSelection.timeZone === stationTimeZone
    ? weekSelection.start
    : startOfWeek(new Date(), stationTimeZone)
  const setWeekStart = (start: Date) => setWeekSelection({ timeZone: stationTimeZone, start })
  const [drawerOpen, setDrawerOpen] = useState(false)
  const [pendingConfirm, setPendingConfirm] = useState<PendingConfirm | null>(null)
  const queryClient = useQueryClient()
  const toast = useToast()

  const query = useQuery<ScheduleItem[], Error>({
    queryKey: ['staff-schedule'],
    queryFn: () => listSchedule({}),
    retry: false,
  })

  const cancelMutation = useMutation<ScheduleItem, Error, string>({
    mutationFn: (id) => cancelSchedule(id),
    onSuccess: (item) => {
      void queryClient.invalidateQueries({ queryKey: ['staff-schedule'] })
      toast.push({
        tone: 'success',
        message: 'Cancelled.',
        detail: `${MODE_META[item.mode].label} · ${item.asset_title ?? item.asset_id}`,
      })
    },
    onError: (err) => {
      // ENG-007 (audit-team v0.3.0): the cancel mutation previously had
      // no error UI — failures fell on the floor. Surface them through
      // the same portal-toast surface the success path uses.
      const detail = err instanceof ApiError ? err.detail : undefined
      toast.push({
        tone: 'error',
        message: 'Could not cancel scheduled item.',
        detail: detail ?? err.message,
      })
    },
  })

  const items = useMemo(() => query.data ?? [], [query.data])
  const visibleWeekItems = useMemo(() => {
    const end = addDays(weekStart, 7)
    return items.filter((it) => {
      const d = dateFromStationDateKey(stationDateKey(new Date(it.scheduled_at), stationTimeZone))
      return !Number.isNaN(d.getTime()) && d >= weekStart && d < end
    })
  }, [items, stationTimeZone, weekStart])

  const fmtWeekLabel = (start: Date) => {
    const end = addDays(start, 6)
    const sameMonth = start.getUTCMonth() === end.getUTCMonth()
    const startLabel = start.toLocaleDateString(undefined, {
      timeZone: 'UTC',
      month: 'short',
      day: 'numeric',
    })
    const endLabel = end.toLocaleDateString(undefined, {
      timeZone: 'UTC',
      month: sameMonth ? undefined : 'short',
      day: 'numeric',
    })
    return `Week of ${startLabel} – ${endLabel}, ${end.getUTCFullYear()}`
  }

  return (
    <div className="flex flex-col">
      <header className="px-6 pb-4 pt-6">
        <div
          className="mb-1 text-[10px] font-semibold uppercase tracking-wider"
          style={{ color: 'var(--cc-ink-3)' }}
        >
          Workflow
        </div>
        <h1 className="m-0 text-2xl font-semibold tracking-tight">Schedule</h1>
        <p className="mt-1 text-xs" style={{ color: 'var(--cc-ink-2)' }}>
          {fmtWeekLabel(weekStart)} · times shown in station time ({stationTimeZone}).
          Conflicts on the same channel are rejected at the database layer.
        </p>
        {stationProfileQuery.isError && (
          <p role="alert" className="m-0 mt-2 text-xs" style={{ color: 'var(--cc-err)' }}>
            The station timezone could not be loaded; existing items are shown in UTC and new scheduling is disabled.
          </p>
        )}
      </header>

      <div
        className="flex flex-wrap items-center gap-3 px-6 py-3"
        style={{ borderBottom: '1px solid var(--cc-line)' }}
      >
        <div role="tablist" aria-label="Schedule view" className="flex gap-1">
          {(['week', 'list'] as const).map((id) => {
            const active = view === id
            return (
              <button
                key={id}
                role="tab"
                aria-selected={active}
                onClick={() => setView(id)}
                className="rounded-md px-3 py-1.5 text-xs font-medium capitalize"
                style={{
                  background: active ? 'var(--cc-ink)' : 'transparent',
                  color: active ? 'var(--cc-ink-inv)' : 'var(--cc-ink-2)',
                  border: '1px solid transparent',
                }}
              >
                {id}
              </button>
            )
          })}
        </div>

        <div className="flex items-center gap-1">
          <button
            type="button"
            aria-label="Previous week"
            onClick={() => setWeekStart(addDays(weekStart, -7))}
            className="rounded-md px-2 py-1.5 text-xs"
            style={{ border: '1px solid var(--cc-line)', color: 'var(--cc-ink-2)' }}
          >
            ‹
          </button>
          <button
            type="button"
            onClick={() => setWeekStart(startOfWeek(new Date(), stationTimeZone))}
            className="rounded-md px-3 py-1.5 text-xs font-medium"
            style={{ border: '1px solid var(--cc-line)', color: 'var(--cc-ink-2)' }}
          >
            Today
          </button>
          <button
            type="button"
            aria-label="Next week"
            onClick={() => setWeekStart(addDays(weekStart, 7))}
            className="rounded-md px-2 py-1.5 text-xs"
            style={{ border: '1px solid var(--cc-line)', color: 'var(--cc-ink-2)' }}
          >
            ›
          </button>
        </div>

        <div className="ml-auto">
          <button
            type="button"
            onClick={() => setDrawerOpen(true)}
            className="rounded-md px-3 py-1.5 text-xs font-semibold"
            style={{ background: 'var(--cc-brand)', color: 'var(--cc-brand-ink)' }}
          >
            New scheduled item
          </button>
        </div>
      </div>

      {query.isLoading && <LoadingState />}
      {query.isError && (
        <ErrorState error={query.error} onRetry={() => query.refetch()} />
      )}
      {query.isSuccess && items.length === 0 && (
        <EmptyState onNew={() => setDrawerOpen(true)} />
      )}
      {query.isSuccess &&
        items.length > 0 &&
        (view === 'week' ? (
          visibleWeekItems.length > 0 ? (
            <WeekGrid
              weekStart={weekStart}
              items={visibleWeekItems}
              timeZone={stationTimeZone}
              onItemSelect={(it) => {
                // UX-002: clicking an event in the week grid opens the
                // list view scrolled to that day so the operator can
                // act on it (cancel, see details). v0.3.1 ships the
                // simpler "switch to list view" handler; a richer
                // detail drawer is queued for v0.4.
                setView('list')
                // Anchor the week to the item's date so the list view
                // shows the correct day group.
                setWeekStart(startOfWeek(new Date(it.scheduled_at), stationTimeZone))
              }}
            />
          ) : (
            <div
              className="mx-6 my-6 rounded-md p-4 text-center text-xs"
              style={{ background: 'var(--cc-surface-2)', color: 'var(--cc-ink-2)' }}
            >
              Nothing scheduled this week. Use the arrows to browse, or switch to
              List view to see all items.
            </div>
          )
        ) : (
          <ScheduleList
            items={items}
            timeZone={stationTimeZone}
            onCancel={(it) => {
              const display = it.asset_title?.trim() || it.asset_id
              setPendingConfirm({
                title: `Cancel scheduled item for "${display}"?`,
                body: 'The item is removed from the schedule and will not air at its scheduled time. This cannot be undone — you would need to create a new scheduled item to air it again.',
                confirmLabel: 'Cancel scheduled item',
                run: () => cancelMutation.mutate(it.id),
              })
            }}
          />
        ))}

      {drawerOpen && (
        <ScheduleDrawer
          onClose={() => setDrawerOpen(false)}
          onCreated={(item) => {
            setDrawerOpen(false)
            void query.refetch()
            // UX-007 (audit-team v0.3.0): success path was thin compared
            // to the polished 409-conflict path. Acknowledge the create
            // through the portal-toast surface so the operator's
            // confidence on success matches their confidence on failure.
            const localTime = formatStationInstant(new Date(item.scheduled_at), stationTimeZone, {
                weekday: 'short',
                month: 'short',
                day: 'numeric',
                hour: 'numeric',
                minute: '2-digit',
                second: '2-digit',
              })
            toast.push({
              tone: 'success',
              message: 'Scheduled.',
              detail: `${MODE_META[item.mode].label} · ${item.asset_title ?? item.asset_id} · ${localTime}`,
            })
          }}
        />
      )}

      {pendingConfirm && (
        <ConfirmDialog
          title={pendingConfirm.title}
          body={pendingConfirm.body}
          confirmLabel={pendingConfirm.confirmLabel}
          tone={pendingConfirm.tone}
          onConfirm={() => {
            pendingConfirm.run()
            setPendingConfirm(null)
          }}
          onCancel={() => setPendingConfirm(null)}
        />
      )}
    </div>
  )
}
