// SPDX-License-Identifier: Apache-2.0
// Copyright (c) The CivicCast Authors
//
// Field evidence (2026-08, live station, candidate #17, non-technical-
// volunteer walkthrough): "Schedule premiere" succeeded (item state
// `scheduled`) but the item never appeared on the portal's Coming Up widget.
// A separate playout commit-to-air call was required
// (POST /api/staff/playout/prepare-commit -> POST /api/staff/playout/commit)
// and the operator console had no button for it anywhere near where the
// item was scheduled — only the deeper, differently-named "Channel Ops"
// screen's Commit-to-Air panel could reach it.
//
// This test pins the fix: a "Publish to residents" action lives right on
// the Schedule screen's list row, runs the existing prepare-commit dry-run
// then the existing commit endpoint, and the row's own copy never lets an
// operator believe residents can see an item that isn't committed yet.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'

vi.mock('../api/client', () => ({
  ApiError: class ApiError extends Error {
    status: number
    detail?: string
    constructor(message: string, status = 0, detail?: string) {
      super(message)
      this.name = 'ApiError'
      this.status = status
      this.detail = detail
    }
  },
  cancelSchedule: vi.fn(),
  listSchedule: vi.fn(),
  getStaffIdentity: vi.fn(),
  prepareCommit: vi.fn(),
  commitToAir: vi.fn(),
}))

import { ApiError, commitToAir, getStaffIdentity, prepareCommit } from '../api/client'
import { ToastContext } from '../components/toast-context'
import type { CommitToAirPlan, CommitToAirReport, StaffIdentityResponse } from '../types/api.generated'
import type { ScheduleItem } from '../types/schedule'
import { ScheduleList } from './ScheduleScreen'

afterEach(cleanup)
// Every test sets its own mockResolvedValue/mockRejectedValue on the shared
// api/client mocks; reset call history + implementations between tests so
// an earlier test's calls never leak into a later "not called" assertion.
beforeEach(() => {
  vi.resetAllMocks()
})

const ITEM_ID = '550e8400-e29b-41d4-a716-446655440000'

function scheduleItem(overrides: Partial<ScheduleItem> = {}): ScheduleItem {
  return {
    id: ITEM_ID,
    asset_id: 'city-council-2026-08-20',
    asset_title: 'City Council — Aug 20',
    channel_id: 'government',
    mode: 'premiere',
    state: 'scheduled',
    scheduled_at: '2026-08-20T18:00:00Z',
    duration_seconds: 1800,
    notes: null,
    created_at: '2026-08-01T00:00:00Z',
    ...overrides,
  } as ScheduleItem
}

function identity(
  roles: NonNullable<StaffIdentityResponse['roles']> = ['publish_operator'],
): StaffIdentityResponse {
  return { operator_id: 'op-1', operator_display_name: 'Dana', roles }
}

function plan(overrides: Partial<CommitToAirPlan> = {}): CommitToAirPlan {
  return {
    plan_id: 'ctap_x',
    channel_id: 'government',
    occurrence_id: `manual:${ITEM_ID}`,
    schedule_item_id: ITEM_ID,
    asset_id: 'city-council-2026-08-20',
    title: 'City Council — Aug 20',
    scheduled_at: '2026-08-20T18:00:00Z',
    duration_seconds: 1800,
    dry_run_passed: true,
    conflicts_detected: [],
    missing_media_detail: null,
    gaps_detected: [],
    created_at: '2026-08-19T12:00:00Z',
    ...overrides,
  }
}

function report(overrides: Partial<CommitToAirReport> = {}): CommitToAirReport {
  return {
    report_id: 'ctar_1',
    channel_id: 'government',
    occurrence_id: `manual:${ITEM_ID}`,
    schedule_item_id: ITEM_ID,
    asset_id: 'city-council-2026-08-20',
    title: 'City Council — Aug 20',
    scheduled_at: '2026-08-20T18:00:00Z',
    duration_seconds: 1800,
    approved_by_operator_id: 'op-1',
    dispatch_status: 'queued',
    approved_at: '2026-08-19T12:05:00Z',
    ...overrides,
  } as CommitToAirReport
}

// The "Publish to residents" button renders on the very first (synchronous)
// paint, before the ['staff-identity'] query resolves — it starts disabled
// and only becomes clickable once the role check settles. Waiting for the
// text alone (findByText) is not enough: it can resolve against that first,
// still-disabled paint, and a click on a disabled button is a silent no-op.
async function clickEnabledPublishButton(getByText: (text: string) => HTMLElement) {
  await waitFor(() => {
    expect((getByText('Publish to residents') as HTMLButtonElement).disabled).toBe(false)
  })
  fireEvent.click(getByText('Publish to residents'))
}

function renderList(items: ScheduleItem[], timeZone?: string) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  const invalidateSpy = vi.spyOn(client, 'invalidateQueries')
  const push = vi.fn()
  const onCancel = vi.fn()
  const utils = render(
    <QueryClientProvider client={client}>
      <ToastContext.Provider value={{ push }}>
        <ScheduleList items={items} timeZone={timeZone} onCancel={onCancel} />
      </ToastContext.Provider>
    </QueryClientProvider>,
  )
  return { ...utils, invalidateSpy, push, onCancel }
}

describe('ScheduleList — Publish to residents', () => {
  it('keeps the exact 38-second approval interval on the station clock rather than the browser clock', async () => {
    vi.mocked(getStaffIdentity).mockResolvedValue(identity())
    const scheduledAt = '2026-11-02T18:02:22Z'
    vi.mocked(prepareCommit).mockResolvedValue(plan({ scheduled_at: scheduledAt, duration_seconds: 38 }))
    const timeZone = Intl.DateTimeFormat().resolvedOptions().timeZone === 'Pacific/Auckland'
      ? 'Asia/Tokyo' : 'Pacific/Auckland'
    const { findByText, getByText } = renderList([
      scheduleItem({ scheduled_at: scheduledAt, duration_seconds: 38 }),
    ], timeZone)

    await clickEnabledPublishButton(getByText)
    const formatter = new Intl.DateTimeFormat(undefined, {
      timeZone, weekday: 'short', hour: 'numeric', minute: '2-digit', second: '2-digit',
    })
    expect(await findByText(
      `${formatter.format(new Date(scheduledAt))} – ${formatter.format(new Date('2026-11-02T18:03:00Z'))} · 00:00:38`,
    )).toBeTruthy()
    expect(commitToAir).not.toHaveBeenCalled()
  })

  it('tells the operator an uncommitted premiere is not yet visible to residents', async () => {
    vi.mocked(getStaffIdentity).mockResolvedValue(identity())
    const { findByText } = renderList([scheduleItem()])
    expect(await findByText('Not yet visible to residents')).toBeTruthy()
  })

  it('shows the action disabled, with the role explained, for an operator without publish rights', async () => {
    vi.mocked(getStaffIdentity).mockResolvedValue(identity(['records_clerk']))
    const { findByText, getByText } = renderList([scheduleItem()])
    await waitFor(() =>
      expect((getByText('Publish to residents') as HTMLButtonElement).disabled).toBe(true),
    )
    expect(
      await findByText(/Publishing a premiere to residents requires the publish operator/),
    ).toBeTruthy()
    expect(prepareCommit).not.toHaveBeenCalled()
  })

  it('runs prepare then commit on approval, using the manual: occurrence-id convention', async () => {
    vi.mocked(getStaffIdentity).mockResolvedValue(identity())
    vi.mocked(prepareCommit).mockResolvedValue(plan())
    vi.mocked(commitToAir).mockResolvedValue(report())
    const { findByText, getByText, invalidateSpy, push } = renderList([scheduleItem()])

    await clickEnabledPublishButton(getByText)
    expect(prepareCommit).toHaveBeenCalledWith({
      channel_id: 'government',
      occurrence_id: `manual:${ITEM_ID}`,
      schedule_item_id: ITEM_ID,
    })

    // The dry-run review appears with its own approve action.
    expect(await findByText('Safe to air')).toBeTruthy()
    fireEvent.click(await findByText('Publish to residents'))

    await waitFor(() =>
      expect(commitToAir).toHaveBeenCalledWith({
        channel_id: 'government',
        occurrence_id: `manual:${ITEM_ID}`,
        schedule_item_id: ITEM_ID,
        plan_id: 'ctap_x',
      }),
    )
    await waitFor(() =>
      expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ['staff-schedule'] }),
    )
    await waitFor(() =>
      expect(push).toHaveBeenCalledWith(
        expect.objectContaining({ tone: 'success', message: 'Visible to residents.' }),
      ),
    )
  })

  it('reflects the published state (visible to residents, no publish action) once the schedule refetch reports it', async () => {
    vi.mocked(getStaffIdentity).mockResolvedValue(identity())
    const { findByText, queryByText, rerender } = renderList([scheduleItem()])
    expect(await findByText('Not yet visible to residents')).toBeTruthy()

    // Simulate the parent's ['staff-schedule'] refetch (triggered by the
    // invalidateQueries call above in production) delivering the
    // now-published row.
    rerender(
      <QueryClientProvider client={new QueryClient()}>
        <ToastContext.Provider value={{ push: vi.fn() }}>
          <ScheduleList items={[scheduleItem({ state: 'published' })]} onCancel={vi.fn()} />
        </ToastContext.Provider>
      </QueryClientProvider>,
    )
    expect(await findByText('Visible to residents')).toBeTruthy()
    expect(queryByText('Not yet visible to residents')).toBeNull()
    expect(queryByText('Publish to residents')).toBeNull()
  })

  it('surfaces a failed dry-run as a readable reason and never offers to commit', async () => {
    vi.mocked(getStaffIdentity).mockResolvedValue(identity())
    vi.mocked(prepareCommit).mockResolvedValue(
      plan({
        dry_run_passed: false,
        missing_media_detail: 'Asset has no media file on disk yet.',
      }),
    )
    const { findByText, getByText } = renderList([scheduleItem()])

    await clickEnabledPublishButton(getByText)
    expect(await findByText('Asset has no media file on disk yet.')).toBeTruthy()
    expect(getByText('Not safe to air yet')).toBeTruthy()
    const approve = getByText('Publish to residents') as HTMLButtonElement
    expect(approve.disabled).toBe(true)
    expect(commitToAir).not.toHaveBeenCalled()
  })

  it('surfaces a failed commit as the readable server message, not a raw status code', async () => {
    vi.mocked(getStaffIdentity).mockResolvedValue(identity())
    vi.mocked(prepareCommit).mockResolvedValue(plan())
    vi.mocked(commitToAir).mockRejectedValue(
      new ApiError(
        'Request failed: 409 Conflict',
        409,
        'A schedule conflict appeared since you reviewed this — reload and try again.',
      ),
    )
    const { findByText, getByText } = renderList([scheduleItem()])

    await clickEnabledPublishButton(getByText)
    expect(await findByText('Safe to air')).toBeTruthy()
    fireEvent.click(await findByText('Publish to residents'))

    expect(
      await findByText('A schedule conflict appeared since you reviewed this — reload and try again.'),
    ).toBeTruthy()
  })

  it('never surfaces the publish action for an embargo item (Commit-to-Air does not apply)', async () => {
    vi.mocked(getStaffIdentity).mockResolvedValue(identity())
    const { queryByText, findByText } = renderList([
      scheduleItem({ mode: 'embargo', duration_seconds: null }),
    ])
    // Let the identity query settle before asserting an absence.
    await findByText('city-council-2026-08-20')
    expect(queryByText('Publish to residents')).toBeNull()
    expect(queryByText('Not yet visible to residents')).toBeNull()
    expect(prepareCommit).not.toHaveBeenCalled()
  })
})
