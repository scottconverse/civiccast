import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render } from '@testing-library/react'

import type { ChannelLogEntry, CommitToAirPlan, CommitToAirReport } from '../types/api.generated'
import { CommitReportRow, DryRunReview } from './CommitToAirPanel'
import {
  canApprove,
  committedOccurrenceIds,
  occurrenceBadge,
  reportStatusLabel,
} from './commit-format'

// vitest config has no global afterEach — unmount each render so body-scoped
// queries don't see elements left behind by the previous test.
afterEach(cleanup)

function entry(overrides: Partial<ChannelLogEntry> = {}): ChannelLogEntry {
  return {
    occurrence_id: 'occ-1',
    slot_id: 'slot-1',
    channel_id: 'public',
    asset_id: 'city-council',
    title_override: 'City Council',
    occurrence_start: '2026-06-20T18:00:00Z',
    duration_seconds: 1800,
    schedule_item_id: '550e8400-e29b-41d4-a716-446655440000',
    status: 'scheduled',
    detail: '',
    ...overrides,
  }
}

function plan(overrides: Partial<CommitToAirPlan> = {}): CommitToAirPlan {
  return {
    plan_id: 'ctap_x',
    channel_id: 'public',
    occurrence_id: 'occ-1',
    schedule_item_id: '550e8400-e29b-41d4-a716-446655440000',
    asset_id: 'city-council',
    title: 'City Council',
    scheduled_at: '2026-06-20T18:00:00Z',
    duration_seconds: 1800,
    dry_run_passed: true,
    conflicts_detected: [],
    missing_media_detail: null,
    gaps_detected: [],
    created_at: '2026-06-15T12:00:00Z',
    operator_id: 'dana',
    ...overrides,
  }
}

function report(overrides: Partial<CommitToAirReport> = {}): CommitToAirReport {
  return {
    report_id: 'ctar_1',
    channel_id: 'public',
    occurrence_id: 'occ-1',
    schedule_item_id: '550e8400-e29b-41d4-a716-446655440000',
    asset_id: 'city-council',
    title: 'City Council',
    scheduled_at: '2026-06-20T18:00:00Z',
    duration_seconds: 1800,
    approved_by_operator_id: 'dana',
    approved_at: '2026-06-15T12:00:00Z',
    conflicts_found: 0,
    gaps_found: 0,
    dispatch_status: 'queued',
    created_at: '2026-06-15T12:00:00Z',
    updated_at: '2026-06-15T12:00:00Z',
    ...overrides,
  }
}

describe('occurrenceBadge', () => {
  it('marks an occurrence with a live commit as Committed', () => {
    const badge = occurrenceBadge(entry(), new Set(['occ-1']))
    expect(badge.label).toBe('Committed')
    expect(badge.tone).toBe('ok')
  })

  it('marks an un-materialized occurrence as not ready', () => {
    expect(occurrenceBadge(entry({ schedule_item_id: null }), new Set()).label).toBe('Not ready to air')
  })

  it('surfaces a skipped occurrence reason', () => {
    expect(occurrenceBadge(entry({ status: 'skipped_conflict' }), new Set()).label).toBe('Skipped (conflict)')
  })

  it('marks a fresh scheduled occurrence as ready to review', () => {
    expect(occurrenceBadge(entry(), new Set()).label).toBe('Ready to review')
  })

  it('marks a manually-scheduled item as ready to review, not a skip (F-RC4-2)', () => {
    const badge = occurrenceBadge(entry({ status: 'manual' }), new Set())
    expect(badge.label).toBe('Ready to review')
    expect(badge.tone).toBe('info')
  })
})

describe('reportStatusLabel', () => {
  it('maps dispatch statuses to plain English', () => {
    expect(reportStatusLabel('queued')).toBe('Queued to air')
    expect(reportStatusLabel('acknowledged')).toBe('On air (confirmed)')
    expect(reportStatusLabel('error')).toBe("Couldn't reach the engine")
    expect(reportStatusLabel('cancelled')).toBe('Rolled back')
  })
})

describe('committedOccurrenceIds', () => {
  it('counts pending/queued/acknowledged as on air, not error/cancelled', () => {
    const ids = committedOccurrenceIds([
      report({ occurrence_id: 'a', dispatch_status: 'queued' }),
      report({ occurrence_id: 'b', dispatch_status: 'acknowledged' }),
      report({ occurrence_id: 'c', dispatch_status: 'cancelled' }),
      report({ occurrence_id: 'd', dispatch_status: 'error' }),
    ])
    expect(ids.has('a')).toBe(true)
    expect(ids.has('b')).toBe(true)
    expect(ids.has('c')).toBe(false)
    expect(ids.has('d')).toBe(false)
  })
})

describe('canApprove', () => {
  it('requires both a passing dry-run and the manage-air role', () => {
    expect(canApprove(plan({ dry_run_passed: true }), true)).toBe(true)
    expect(canApprove(plan({ dry_run_passed: true }), false)).toBe(false)
    expect(canApprove(plan({ dry_run_passed: false }), true)).toBe(false)
    expect(canApprove(undefined, true)).toBe(false)
  })
})

describe('DryRunReview', () => {
  it('shows the exact start, end and duration before approving a 38-second slot', () => {
    const start = new Date('2026-11-01T17:30:07Z')
    const end = new Date(start.getTime() + 38_000)
    const options: Intl.DateTimeFormatOptions = {
      weekday: 'short', hour: 'numeric', minute: '2-digit', second: '2-digit',
    }
    const { getByText } = render(
      <DryRunReview
        plan={plan({ scheduled_at: start.toISOString(), duration_seconds: 38 })}
        canManage committing={false} onApprove={vi.fn()} onCancel={vi.fn()}
      />,
    )
    expect(getByText(`${start.toLocaleString(undefined, options)} – ${end.toLocaleString(undefined, options)} · 00:00:38`)).toBeTruthy()
  })

  it('disables Approve until the dry-run passes', () => {
    const { getByText } = render(
      <DryRunReview
        plan={plan({ dry_run_passed: false, missing_media_detail: 'Asset has no media file on disk yet.' })}
        canManage
        committing={false}
        onApprove={vi.fn()}
        onCancel={vi.fn()}
      />,
    )
    expect((getByText('Approve & put on air') as HTMLButtonElement).disabled).toBe(true)
    expect(getByText('Asset has no media file on disk yet.')).toBeTruthy()
  })

  it('disables Approve for an operator without the manage-air role', () => {
    const { getByText } = render(
      <DryRunReview plan={plan()} canManage={false} committing={false} onApprove={vi.fn()} onCancel={vi.fn()} />,
    )
    expect((getByText('Approve & put on air') as HTMLButtonElement).disabled).toBe(true)
    expect(getByText(/requires the publish operator or setup admin role/)).toBeTruthy()
  })

  it('approves when the dry-run passed and the operator may manage air', () => {
    const onApprove = vi.fn()
    const { getByText } = render(
      <DryRunReview plan={plan()} canManage committing={false} onApprove={onApprove} onCancel={vi.fn()} />,
    )
    const btn = getByText('Approve & put on air') as HTMLButtonElement
    expect(btn.disabled).toBe(false)
    fireEvent.click(btn)
    expect(onApprove).toHaveBeenCalled()
  })

  it('lists schedule conflicts', () => {
    const { container } = render(
      <DryRunReview
        plan={plan({
          dry_run_passed: false,
          conflicts_detected: [
            {
              existing_schedule_item_id: 's2',
              existing_asset_id: 'other',
              existing_asset_title: 'Other Meeting',
              existing_scheduled_at: '2026-06-20T18:10:00Z',
              existing_duration_seconds: 600,
              proposed_scheduled_at: '2026-06-20T18:00:00Z',
              proposed_duration_seconds: 1800,
              overlap_seconds: 600,
            },
          ],
        })}
        canManage
        committing={false}
        onApprove={vi.fn()}
        onCancel={vi.fn()}
      />,
    )
    expect(container.textContent).toContain('Other Meeting')
    expect(container.textContent).toContain('Clashes with 1 other program')
  })
})

describe('CommitReportRow rollback', () => {
  it('requires two steps and a reason to take a program off air', () => {
    const onRollback = vi.fn()
    const { getByText, getByLabelText } = render(
      <CommitReportRow report={report()} canManage rollingBack={false} onRollback={onRollback} />,
    )
    // First click only arms the confirm UI.
    fireEvent.click(getByText('Take off air'))
    expect(onRollback).not.toHaveBeenCalled()
    const confirm = getByText('Confirm take-off') as HTMLButtonElement
    expect(confirm.disabled).toBe(true) // reason still empty
    fireEvent.change(getByLabelText('Reason for taking off air'), { target: { value: 'wrong meeting' } })
    expect(confirm.disabled).toBe(false)
    fireEvent.click(confirm)
    expect(onRollback).toHaveBeenCalledWith('ctar_1', 'wrong meeting')
  })

  it('hides the take-off control for an operator without the manage-air role', () => {
    const { queryByText } = render(
      <CommitReportRow report={report()} canManage={false} rollingBack={false} onRollback={vi.fn()} />,
    )
    expect(queryByText('Take off air')).toBeNull()
  })

  it('shows the rollback reason and no take-off button once rolled back', () => {
    const { container, queryByText } = render(
      <CommitReportRow
        report={report({ dispatch_status: 'cancelled', rollback_reason: 'aired in error' })}
        canManage
        rollingBack={false}
        onRollback={vi.fn()}
      />,
    )
    expect(queryByText('Take off air')).toBeNull()
    expect(container.textContent).toContain('Rolled back: aired in error')
  })
})
