import { useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import {
  ApiError,
  commitToAir,
  getChannelProgramLog,
  listCommits,
  prepareCommit,
  rollbackCommit,
} from '../api/client'
import type {
  ChannelLogEntry,
  CommitToAirPlan,
  CommitToAirReport,
} from '../types/api.generated'
import {
  canApprove,
  committedOccurrenceIds,
  occurrenceBadge,
  reportStatusLabel,
  reportTone,
} from './commit-format'
import type { CommitDispatchStatus, Tone } from './commit-format'
import { formatDurationHMS } from './recording-format'
import { formatStationInstant } from '../lib/station-time'

const UPCOMING_HOURS = 24
const RECENT_LIMIT = 10

function apiMessage(error: unknown, fallback: string): string {
  if (error instanceof ApiError) return error.detail ?? fallback
  if (error instanceof Error) return error.message
  return fallback
}

function formatWhen(iso: string, timeZone?: string): string {
  const options: Intl.DateTimeFormatOptions = {
    weekday: 'short',
    hour: 'numeric',
    minute: '2-digit',
    second: '2-digit',
  }
  const instant = new Date(iso)
  return timeZone
    ? formatStationInstant(instant, timeZone, options)
    : instant.toLocaleString(undefined, options)
}

const TONE_STYLE: Record<Tone, { bg: string; fg: string }> = {
  ok: { bg: 'var(--cc-ok-soft)', fg: 'var(--cc-ok)' },
  warn: { bg: 'var(--cc-warn-soft)', fg: 'var(--cc-warn)' },
  err: { bg: 'var(--cc-err-soft)', fg: 'var(--cc-err)' },
  info: { bg: 'var(--cc-info-soft)', fg: 'var(--cc-info)' },
  muted: { bg: 'var(--cc-surface-3)', fg: 'var(--cc-ink-3)' },
}

function Badge({ label, tone }: { label: string; tone: Tone }) {
  const style = TONE_STYLE[tone]
  return (
    <span
      className="inline-flex rounded-full px-2 py-0.5 text-[11px] font-semibold"
      style={{ background: style.bg, color: style.fg }}
    >
      {label}
    </span>
  )
}

const PANEL_STYLE = { background: 'var(--cc-surface)', border: '1px solid var(--cc-line)' }
const SUBCARD_STYLE = { background: 'var(--cc-surface-2)', border: '1px solid var(--cc-line)' }

function primaryButtonStyle(disabled: boolean) {
  return {
    background: disabled ? 'var(--cc-surface-3)' : 'var(--cc-brand)',
    color: disabled ? 'var(--cc-ink-3)' : 'var(--cc-brand-ink)',
  }
}

// --- Dry-run review (exported for tests) ---

export function DryRunReview({
  plan,
  timeZone,
  canManage,
  committing,
  onApprove,
  onCancel,
  approveLabel = 'Approve & put on air',
  committingLabel = 'Putting on air…',
  roleGateLabel = 'Airing requires the publish operator or setup admin role.',
}: {
  plan: CommitToAirPlan
  /** Match the calling screen's station clock; omitted callers retain their browser clock. */
  timeZone?: string
  canManage: boolean
  committing: boolean
  onApprove: () => void
  onCancel: () => void
  /** Override the approve button's label — callers outside Channel Ops
   * (e.g. the Schedule screen's "Publish to residents" action) speak to a
   * different audience than the broadcast-engineer "put on air" phrasing. */
  approveLabel?: string
  /** Override the approve button's in-flight label. */
  committingLabel?: string
  /** Override the non-manager explanation shown below the actions. */
  roleGateLabel?: string
}) {
  const approvable = canApprove(plan, canManage)
  const conflicts = plan.conflicts_detected ?? []
  const gaps = plan.gaps_detected ?? []
  const endsAt = new Date(Date.parse(plan.scheduled_at) + plan.duration_seconds * 1000).toISOString()
  return (
    <div className="mt-2 grid gap-3 rounded-md p-3" style={SUBCARD_STYLE}>
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-semibold">{plan.title}</span>
        {plan.dry_run_passed ? (
          <Badge label="Safe to air" tone="ok" />
        ) : (
          <Badge label="Not safe to air yet" tone="err" />
        )}
        <span className="cc-mono text-[11px]" style={{ color: 'var(--cc-ink-3)' }}>
          {formatWhen(plan.scheduled_at, timeZone)} – {formatWhen(endsAt, timeZone)} · {formatDurationHMS(plan.duration_seconds)}
        </span>
      </div>

      {plan.missing_media_detail && (
        <div role="alert" className="text-sm" style={{ color: 'var(--cc-err)' }}>
          {plan.missing_media_detail}
        </div>
      )}

      {conflicts.length > 0 && (
        <div className="grid gap-1 text-sm">
          <div className="font-semibold" style={{ color: 'var(--cc-err)' }}>
            Clashes with {conflicts.length} other program
            {conflicts.length === 1 ? '' : 's'} already scheduled:
          </div>
          {conflicts.map((c) => (
            <div key={c.existing_schedule_item_id} className="text-xs" style={{ color: 'var(--cc-ink-2)' }}>
              {c.existing_asset_title} at {formatWhen(c.existing_scheduled_at, timeZone)} (overlaps {formatDurationHMS(c.overlap_seconds)})
            </div>
          ))}
        </div>
      )}

      {gaps.length > 0 && (
        <div className="grid gap-1 text-xs" style={{ color: 'var(--cc-ink-3)' }}>
          <div className="font-semibold">Dead-air gap before this program (it can still air):</div>
          {gaps.map((g, i) => (
            <div key={`${g.starts_at}-${i}`}>{g.label ?? 'Gap'} — {formatDurationHMS(g.duration_seconds)}</div>
          ))}
        </div>
      )}

      <div className="flex flex-wrap items-center gap-2">
        <button
          type="button"
          disabled={!approvable || committing}
          onClick={onApprove}
          className="rounded-md px-3 py-2 text-sm font-semibold"
          style={primaryButtonStyle(!approvable || committing)}
        >
          {committing ? committingLabel : approveLabel}
        </button>
        <button
          type="button"
          onClick={onCancel}
          className="rounded-md px-3 py-2 text-sm font-medium"
          style={{ border: '1px solid var(--cc-line)', color: 'var(--cc-ink-2)', background: 'var(--cc-surface)' }}
        >
          Cancel
        </button>
        {!canManage && (
          <span className="text-[11px]" style={{ color: 'var(--cc-ink-3)' }}>
            {roleGateLabel}
          </span>
        )}
      </div>
    </div>
  )
}

// --- Recent commit row with two-step rollback (exported for tests) ---

export function CommitReportRow({
  report,
  canManage,
  rollingBack,
  onRollback,
}: {
  report: CommitToAirReport
  canManage: boolean
  rollingBack: boolean
  onRollback: (reportId: string, reason: string) => void
}) {
  const [confirming, setConfirming] = useState(false)
  const [reason, setReason] = useState('')
  const status: CommitDispatchStatus = report.dispatch_status ?? 'pending'
  const rolledBack = status === 'cancelled'
  return (
    <div className="grid gap-2 rounded-md p-3 text-sm" style={SUBCARD_STYLE}>
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-semibold">{report.title}</span>
        <Badge label={reportStatusLabel(status)} tone={reportTone(status)} />
        <span className="cc-mono text-[11px]" style={{ color: 'var(--cc-ink-3)' }}>
          scheduled for {formatWhen(report.scheduled_at)} · approved by {report.approved_by_operator_id}
        </span>
      </div>
      {report.dispatch_error_detail && (
        <div role="alert" className="text-xs" style={{ color: 'var(--cc-err)' }}>
          {report.dispatch_error_detail}
        </div>
      )}
      {rolledBack && report.rollback_reason && (
        <div className="text-xs" style={{ color: 'var(--cc-ink-3)' }}>
          Rolled back: {report.rollback_reason}
        </div>
      )}
      {canManage && !rolledBack && (
        confirming ? (
          <div className="grid gap-2">
            <input
              type="text"
              value={reason}
              onChange={(e) => setReason(e.target.value)}
              placeholder="Why are you taking this off air?"
              aria-label="Reason for taking off air"
              className="rounded-md px-3 py-2 text-sm outline-none"
              style={{ background: 'var(--cc-surface)', border: '1px solid var(--cc-line)', color: 'var(--cc-ink)' }}
            />
            <div className="flex flex-wrap items-center gap-2">
              <button
                type="button"
                disabled={!reason.trim() || rollingBack}
                onClick={() => onRollback(report.report_id, reason.trim())}
                className="rounded-md px-3 py-2 text-sm font-semibold"
                style={{
                  background: !reason.trim() || rollingBack ? 'var(--cc-surface-3)' : 'var(--cc-err)',
                  color: !reason.trim() || rollingBack ? 'var(--cc-ink-3)' : 'white',
                }}
              >
                {rollingBack ? 'Taking off air…' : 'Confirm take-off'}
              </button>
              <button
                type="button"
                onClick={() => setConfirming(false)}
                className="rounded-md px-3 py-2 text-sm font-medium"
                style={{ border: '1px solid var(--cc-line)', color: 'var(--cc-ink-2)', background: 'var(--cc-surface)' }}
              >
                Keep on air
              </button>
            </div>
          </div>
        ) : (
          <div>
            <button
              type="button"
              onClick={() => setConfirming(true)}
              className="rounded-md px-3 py-1.5 text-xs font-medium"
              style={{ border: '1px solid var(--cc-err)', color: 'var(--cc-err)', background: 'var(--cc-surface)' }}
            >
              Take off air
            </button>
          </div>
        )
      )}
    </div>
  )
}

// --- Container ---

export function CommitToAirPanel({
  channelId,
  canManage,
}: {
  channelId: string | undefined
  canManage: boolean
}) {
  const queryClient = useQueryClient()
  const [reviewOccurrenceId, setReviewOccurrenceId] = useState<string | null>(null)

  const occurrencesQuery = useQuery({
    queryKey: ['playout-occurrences', channelId],
    queryFn: () => getChannelProgramLog(channelId ?? '', UPCOMING_HOURS),
    enabled: Boolean(channelId),
  })
  const commitsQuery = useQuery({
    queryKey: ['playout-commits', channelId],
    queryFn: () => listCommits(channelId ?? '', { limit: RECENT_LIMIT }),
    enabled: Boolean(channelId),
  })

  const prepareMutation = useMutation({
    mutationFn: (entry: ChannelLogEntry) =>
      prepareCommit({
        channel_id: entry.channel_id,
        occurrence_id: entry.occurrence_id,
        schedule_item_id: entry.schedule_item_id ?? '',
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
    onSuccess: () => {
      setReviewOccurrenceId(null)
      prepareMutation.reset()
      void queryClient.invalidateQueries({ queryKey: ['playout-occurrences', channelId] })
      void queryClient.invalidateQueries({ queryKey: ['playout-commits', channelId] })
    },
  })
  const rollbackMutation = useMutation({
    mutationFn: ({ reportId, reason }: { reportId: string; reason: string }) =>
      rollbackCommit(reportId, { reason }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['playout-occurrences', channelId] })
      void queryClient.invalidateQueries({ queryKey: ['playout-commits', channelId] })
    },
  })

  const committedIds = useMemo(
    () => committedOccurrenceIds(commitsQuery.data ?? []),
    [commitsQuery.data],
  )
  const upcoming = useMemo(
    () => (occurrencesQuery.data ?? []).filter((e) => e.schedule_item_id),
    [occurrencesQuery.data],
  )
  const activePlan = prepareMutation.data

  function startReview(entry: ChannelLogEntry) {
    setReviewOccurrenceId(entry.occurrence_id)
    prepareMutation.mutate(entry)
  }
  function cancelReview() {
    setReviewOccurrenceId(null)
    prepareMutation.reset()
  }

  return (
    <section className="rounded-md p-4" style={PANEL_STYLE} aria-label="Commit programs to air">
      <h2 className="m-0 text-lg font-semibold">Commit programs to air</h2>
      <div className="text-xs" style={{ color: 'var(--cc-ink-3)' }}>
        {channelId
          ? 'Review the safety check, then approve a program to put it on this channel.'
          : 'Select a channel to review its upcoming programs.'}
      </div>

      {!canManage && (
        <div className="mt-3 text-sm" style={{ color: 'var(--cc-ink-3)' }}>
          You can review the schedule here. Putting a program on air or taking it
          off requires the publish operator or setup admin role.
        </div>
      )}

      {/* Upcoming */}
      <div className="mt-4 grid gap-2">
        <div className="text-[11px] font-semibold uppercase tracking-wider" style={{ color: 'var(--cc-ink-3)' }}>
          Next {UPCOMING_HOURS} hours
        </div>
        {occurrencesQuery.isError && (
          <div role="alert" className="text-sm" style={{ color: 'var(--cc-err)' }}>
            {apiMessage(occurrencesQuery.error, 'Could not load upcoming programs.')}
          </div>
        )}
        {channelId && upcoming.length === 0 && !occurrencesQuery.isError && (
          <div className="text-sm" style={{ color: 'var(--cc-ink-3)' }}>
            No upcoming programs are scheduled for this channel in the next {UPCOMING_HOURS} hours.
          </div>
        )}
        {upcoming.map((entry) => {
          const badge = occurrenceBadge(entry, committedIds)
          const reviewing = reviewOccurrenceId === entry.occurrence_id
          return (
            <div key={entry.occurrence_id} className="grid gap-1 rounded-md p-3" style={SUBCARD_STYLE}>
              <div className="flex flex-wrap items-center justify-between gap-2">
                <div className="min-w-0">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="font-semibold">{entry.title_override ?? entry.asset_id}</span>
                    <Badge label={badge.label} tone={badge.tone} />
                  </div>
                  <div className="cc-mono mt-0.5 text-[11px]" style={{ color: 'var(--cc-ink-3)' }}>
                    {formatWhen(entry.occurrence_start)}
                    {entry.duration_seconds ? ` · ${formatDurationHMS(entry.duration_seconds)}` : ''}
                  </div>
                </div>
                <button
                  type="button"
                  disabled={!canManage || (reviewing && prepareMutation.isPending)}
                  onClick={() => startReview(entry)}
                  className="rounded-md px-3 py-1.5 text-sm font-medium"
                  style={{
                    border: '1px solid var(--cc-line)',
                    color: canManage ? 'var(--cc-ink)' : 'var(--cc-ink-3)',
                    background: 'var(--cc-surface)',
                  }}
                >
                  {reviewing && prepareMutation.isPending ? 'Checking…' : 'Review & prepare'}
                </button>
              </div>
              {reviewing && prepareMutation.isError && (
                <div role="alert" className="text-sm" style={{ color: 'var(--cc-err)' }}>
                  {apiMessage(prepareMutation.error, 'Could not run the safety check.')}
                </div>
              )}
              {reviewing && activePlan && activePlan.occurrence_id === entry.occurrence_id && (
                <>
                  <DryRunReview
                    plan={activePlan}
                    canManage={canManage}
                    committing={commitMutation.isPending}
                    onApprove={() => commitMutation.mutate(activePlan)}
                    onCancel={cancelReview}
                  />
                  {commitMutation.isError && (
                    <div role="alert" className="mt-1 text-sm" style={{ color: 'var(--cc-err)' }}>
                      {apiMessage(commitMutation.error, 'Could not put this program on air.')}
                    </div>
                  )}
                </>
              )}
            </div>
          )
        })}
      </div>

      {/* Recent commits */}
      <div className="mt-5 grid gap-2">
        <div className="text-[11px] font-semibold uppercase tracking-wider" style={{ color: 'var(--cc-ink-3)' }}>
          Recent commits
        </div>
        <div aria-live="polite" className="grid gap-2">
          {channelId && (commitsQuery.data ?? []).length === 0 && (
            <div className="text-sm" style={{ color: 'var(--cc-ink-3)' }}>
              Nothing has been committed to air on this channel yet.
            </div>
          )}
          {(commitsQuery.data ?? []).map((report) => (
            <CommitReportRow
              key={report.report_id}
              report={report}
              canManage={canManage}
              rollingBack={
                rollbackMutation.isPending &&
                rollbackMutation.variables?.reportId === report.report_id
              }
              onRollback={(reportId, reason) => rollbackMutation.mutate({ reportId, reason })}
            />
          ))}
          {rollbackMutation.isError && (
            <div role="alert" className="text-sm" style={{ color: 'var(--cc-err)' }}>
              {apiMessage(rollbackMutation.error, 'Could not take the program off air.')}
            </div>
          )}
        </div>
      </div>
    </section>
  )
}
