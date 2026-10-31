import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, waitFor, within } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'

vi.mock('../api/client', () => ({
  ApiError: class ApiError extends Error {
    status: number
    detail?: string
    constructor(message: string, status = 0, detail?: string) {
      super(message)
      this.status = status
      this.detail = detail
    }
  },
  getStaffAsset: vi.fn(),
  getStaffIdentity: vi.fn(),
  listStaffAssets: vi.fn(),
  unpublishStaffAsset: vi.fn(),
  updateStaffAsset: vi.fn(),
}))

// The detail editor also mounts MediaLifecyclePanel, OfflineCaptionJobsPanel
// and GenerateSummaryPanel, each with their own independent queries -- stub
// them out so this test stays focused on the unpublish confirm flow this
// finding is about.
vi.mock('./MediaLifecyclePanel', () => ({ MediaLifecyclePanel: () => null }))
vi.mock('./OfflineCaptionJobsPanel', () => ({ OfflineCaptionJobsPanel: () => null }))
vi.mock('./GenerateSummaryPanel', () => ({ GenerateSummaryPanel: () => null }))
vi.mock('./AudioDescriptionPanel', () => ({ AudioDescriptionPanel: () => null }))
vi.mock('./AssetCustomFieldsEditor', () => ({ AssetCustomFieldsEditor: () => null }))

import {
  getStaffAsset,
  getStaffIdentity,
  listStaffAssets,
  unpublishStaffAsset,
  updateStaffAsset,
} from '../api/client'
import type { AssetRow } from '../types/asset'
import type { StaffIdentityResponse } from '../types/api.generated'
import { AssetDetailScreen } from './AssetDetailScreen'

afterEach(cleanup)

function assetFixture(overrides: Partial<AssetRow> = {}): AssetRow {
  return {
    asset_id: 'asset-1',
    title: 'City Council — Aug 30',
    description: null,
    meeting_body: 'City Council',
    state: 'validated',
    manifest_url: null,
    published_at: '2026-08-30T18:00:00Z',
    file_path: '/media/asset-1.mp4',
    file_size_bytes: 12_000_000,
    duration_seconds: 3600,
    codec_video: 'h264',
    codec_audio: 'aac',
    width_px: 1920,
    height_px: 1080,
    bitrate_bps: 4_000_000,
    format_name: 'mp4',
    trim_in_seconds: null,
    trim_out_seconds: null,
    chapters: [],
    retention_policy: 'default',
    retention_until: null,
    retention_term_unit: null,
    retention_term_value: null,
    retention_anchor_at: null,
    version: 1,
    source_live_session_id: null,
    ...overrides,
  }
}

function identity(roles: string[]): StaffIdentityResponse {
  return { operator_id: 'dana', operator_display_name: 'Dana', roles } as StaffIdentityResponse
}

function renderScreen() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const screen = render(
    <QueryClientProvider client={client}>
      <AssetDetailScreen assetId="asset-1" onClose={() => {}} onEditTrim={() => {}} />
    </QueryClientProvider>,
  )
  return { ...screen, client }
}

beforeEach(() => {
  vi.clearAllMocks()
  vi.mocked(getStaffIdentity).mockResolvedValue(identity(['setup_admin']))
  vi.mocked(listStaffAssets).mockResolvedValue([])
})

describe('AssetDetailScreen unpublish uses the shared ConfirmDialog, not window.confirm', () => {
  it('does not unpublish on the first click, and does on confirm', async () => {
    vi.mocked(getStaffAsset).mockResolvedValue(assetFixture())
    vi.mocked(unpublishStaffAsset).mockResolvedValue(assetFixture({ published_at: null }))
    const confirmSpy = vi.spyOn(window, 'confirm')

    const { findByRole, queryByRole, client } = renderScreen()
    client.setQueryDefaults(['publish-assets'], { staleTime: Infinity })
    client.setQueryData(['publish-assets'], { assets: [{ asset_id: 'asset-1', canonical_public: true }] })

    fireEvent.click(await findByRole('button', { name: 'Remove from portal' }))

    // window.confirm must never be invoked -- the shared, accessible
    // ConfirmDialog replaces it entirely.
    expect(confirmSpy).not.toHaveBeenCalled()
    const dialog = await findByRole('alertdialog')
    expect(dialog.textContent).toContain('Remove "City Council — Aug 30" from the public portal?')
    expect(unpublishStaffAsset).not.toHaveBeenCalled()

    fireEvent.click(within(dialog).getByRole('button', { name: 'Remove from portal' }))
    await waitFor(() => expect(unpublishStaffAsset).toHaveBeenCalledWith('asset-1'))
    await waitFor(() => expect(queryByRole('alertdialog')).toBeNull())
    await waitFor(() => expect(client.getQueryState(['publish-assets'])?.isInvalidated).toBe(true))
  })

  it('unpublishes nothing when the operator cancels', async () => {
    vi.mocked(getStaffAsset).mockResolvedValue(assetFixture())
    const { findByRole, queryByRole } = renderScreen()

    fireEvent.click(await findByRole('button', { name: 'Remove from portal' }))
    await findByRole('alertdialog')
    fireEvent.click(await findByRole('button', { name: 'Cancel' }))

    expect(queryByRole('alertdialog')).toBeNull()
    expect(unpublishStaffAsset).not.toHaveBeenCalled()
  })
})

describe('WP-08 retention term authoring', () => {
  it('shows the legacy policy/deadline editor for a never-converted asset', async () => {
    vi.mocked(getStaffAsset).mockResolvedValue(
      assetFixture({ retention_policy: 'permanent', retention_term_unit: null }),
    )
    const { findByText, queryByLabelText } = renderScreen()

    await findByText('Retention policy')
    expect(queryByLabelText('Retention length')).toBeNull()
  })

  it('converting a legacy row prefills the permanent -> forever suggestion', async () => {
    vi.mocked(getStaffAsset).mockResolvedValue(
      assetFixture({ retention_policy: 'permanent', retention_term_unit: null }),
    )
    const { findByRole, queryByLabelText, findByText } = renderScreen()

    fireEvent.click(await findByRole('button', { name: /Convert to a length/ }))

    await findByText('Retention term')
    // forever needs no numeric value -- the input disappears entirely.
    expect(queryByLabelText('Retention length')).toBeNull()
    const unit = (await findByRole('combobox', { name: 'Retention unit' })) as HTMLSelectElement
    expect(unit.value).toBe('forever')
  })

  it('converting a legacy short row prefills 30 days', async () => {
    vi.mocked(getStaffAsset).mockResolvedValue(
      assetFixture({ retention_policy: 'short', retention_term_unit: null }),
    )
    const { findByRole } = renderScreen()

    fireEvent.click(await findByRole('button', { name: /Convert to a length/ }))

    const value = (await findByRole('spinbutton', {
      name: 'Retention length',
    })) as HTMLInputElement
    const unit = (await findByRole('combobox', { name: 'Retention unit' })) as HTMLSelectElement
    expect(value.value).toBe('30')
    expect(unit.value).toBe('days')
  })

  it('an already-authored term asset shows the term editor directly, with no convert button', async () => {
    vi.mocked(getStaffAsset).mockResolvedValue(
      assetFixture({
        retention_policy: 'default',
        retention_term_unit: 'months',
        retention_term_value: 6,
        retention_anchor_at: '2026-01-01T00:00:00Z',
      }),
    )
    const { findByText, queryByRole, findByRole } = renderScreen()

    await findByText('Retention term')
    expect(queryByRole('button', { name: /Convert to a length/ })).toBeNull()
    expect(queryByRole('button', { name: 'Cancel conversion' })).toBeNull()
    const unit = (await findByRole('combobox', { name: 'Retention unit' })) as HTMLSelectElement
    expect(unit.value).toBe('months')
  })

  it('submits a finite term as a value/unit patch, never mixed with legacy fields', async () => {
    const asset = assetFixture({ retention_policy: 'short', retention_term_unit: null })
    vi.mocked(getStaffAsset).mockResolvedValue(asset)
    vi.mocked(updateStaffAsset).mockResolvedValue({
      ...asset,
      retention_term_unit: 'days',
      retention_term_value: 45,
      version: 2,
    })

    const { findByRole } = renderScreen()
    fireEvent.click(await findByRole('button', { name: /Convert to a length/ }))

    const value = (await findByRole('spinbutton', {
      name: 'Retention length',
    })) as HTMLInputElement
    fireEvent.change(value, { target: { value: '45' } })
    fireEvent.click(await findByRole('button', { name: 'Save metadata' }))

    await waitFor(() => expect(updateStaffAsset).toHaveBeenCalled())
    const [, patch] = vi.mocked(updateStaffAsset).mock.calls[0]!
    expect(patch.retention_term_unit).toBe('days')
    expect(patch.retention_term_value).toBe(45)
    expect(patch.retention_policy).toBeUndefined()
    expect(patch.retention_until).toBeUndefined()
  })

  it('disables save (with an actionable message) while the term length is empty', async () => {
    vi.mocked(getStaffAsset).mockResolvedValue(
      assetFixture({ retention_policy: 'default', retention_term_unit: null }),
    )
    const { findByRole, findByText } = renderScreen()
    fireEvent.click(await findByRole('button', { name: /Convert to a length/ }))

    await findByText(/Enter how many days\/weeks\/months\/years/)
    const saveButton = (await findByRole('button', {
      name: 'Save metadata',
    })) as HTMLButtonElement
    expect(saveButton.disabled).toBe(true)
    fireEvent.click(saveButton)
    expect(updateStaffAsset).not.toHaveBeenCalled()
  })

  it('forever conversion is immediately saveable with no length required', async () => {
    const asset = assetFixture({ retention_policy: 'permanent', retention_term_unit: null })
    vi.mocked(getStaffAsset).mockResolvedValue(asset)
    vi.mocked(updateStaffAsset).mockResolvedValue({
      ...asset,
      retention_term_unit: 'forever',
      retention_term_value: null,
      version: 2,
    })

    const { findByRole } = renderScreen()
    fireEvent.click(await findByRole('button', { name: /Convert to a length/ }))
    const saveButton = (await findByRole('button', {
      name: 'Save metadata',
    })) as HTMLButtonElement
    expect(saveButton.disabled).toBe(false)

    fireEvent.click(saveButton)
    await waitFor(() => expect(updateStaffAsset).toHaveBeenCalled())
    const [, patch] = vi.mocked(updateStaffAsset).mock.calls[0]!
    expect(patch.retention_term_unit).toBe('forever')
    expect(patch.retention_term_value).toBeUndefined()
  })
})
