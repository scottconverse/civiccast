import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'

afterEach(cleanup)

import type { CgFeedSource, CgZoneConfig, ResolvedBoard } from '../types/api.generated'
import type { AssetRow } from '../types/asset'
import { BoardAudioForm, BoardCreateForm, BoardPreviewPanel, FeedForm, ZoneForm } from './CgBoardDesignerScreen'

const FEED: CgFeedSource = {
  feed_source_id: 'feed_rss',
  channel_id: 'public',
  kind: 'rss',
  label: 'City news',
  source_url: 'https://x.gov/news.rss',
  trust_tier: 'operator_curated',
  refresh_seconds: 900,
  enabled: true,
  created_by: 'op',
  created_at: '2026-01-01T00:00:00Z',
}

// --- isolated form / panel component tests ---

describe('BoardCreateForm', () => {
  it('submits the chosen template id', () => {
    const onSubmit = vi.fn()
    const { getByText } = render(<BoardCreateForm submitting={false} onSubmit={onSubmit} />)
    fireEvent.click(getByText('Create board'))
    expect(onSubmit).toHaveBeenCalledWith({ template_id: 'standard-community-board' })
  })
})

describe('BoardAudioForm', () => {
  it('selects an audio asset and sends the chosen level', () => {
    const onSubmit = vi.fn()
    const assets = [
      { asset_id: 'music-1', title: 'Station music', file_path: 'music.wav', codec_audio: 'pcm_s16le', duration_seconds: 180 },
      { asset_id: 'silent-1', title: 'Silent video', file_path: 'video.mp4', codec_audio: null },
      { asset_id: 'unmeasured-1', title: 'Unmeasured track', file_path: 'track.wav', codec_audio: 'pcm_s16le' },
    ] as AssetRow[]
    const { getByLabelText, getByText, queryByText } = render(
      <BoardAudioForm initial={null} assets={assets} submitting={false} onSubmit={onSubmit} />,
    )
    expect(queryByText('Silent video')).toBeNull()
    expect(queryByText('Unmeasured track')).toBeNull()
    fireEvent.change(getByLabelText('Board audio track'), { target: { value: 'music-1' } })
    fireEvent.change(getByLabelText('Board audio level'), { target: { value: '-9' } })
    fireEvent.click(getByText('Set board audio'))
    expect(onSubmit).toHaveBeenCalledWith({ asset_ref: 'music-1', gain_db: -9 })
  })
})

describe('ZoneForm', () => {
  it('builds a manual zone payload by default', () => {
    const onSubmit = vi.fn()
    const { getByText } = render(<ZoneForm feeds={[]} submitting={false} onSubmit={onSubmit} />)
    fireEvent.click(getByText('Add zone'))
    expect(onSubmit).toHaveBeenCalledTimes(1)
    const payload = onSubmit.mock.calls[0][0]
    expect(payload.content_source).toBe('manual')
    expect(payload.zone_kind).toBe('ticker')
    expect(payload.feed_source_id).toBeNull()
  })

  it('blocks a feed zone with no feed and shows the rule', () => {
    const onSubmit = vi.fn()
    const { getByText, getByLabelText, queryByText } = render(
      <ZoneForm feeds={[FEED]} submitting={false} onSubmit={onSubmit} />,
    )
    fireEvent.change(getByLabelText('Content source'), { target: { value: 'feed_adapter' } })
    expect(getByText(/must name a feed/)).toBeTruthy()
    expect((getByText('Add zone') as HTMLButtonElement).disabled).toBe(true)
    // Selecting a feed clears the block.
    fireEvent.change(getByLabelText('Feed source'), { target: { value: 'feed_rss' } })
    expect(queryByText(/must name a feed/)).toBeNull()
    fireEvent.click(getByText('Add zone'))
    expect(onSubmit.mock.calls[0][0].feed_source_id).toBe('feed_rss')
  })

  // Issue #348: a board image zone can show the channel's uploadable station logo.
  it('lets an image zone choose the station logo and sends the reference', () => {
    const onSubmit = vi.fn()
    const { getByLabelText, getByText, queryByText } = render(
      <ZoneForm feeds={[]} submitting={false} onSubmit={onSubmit} />,
    )
    fireEvent.change(getByLabelText('Content source'), { target: { value: 'image' } })
    fireEvent.change(getByLabelText('Zone image'), { target: { value: 'station-logo' } })
    // No logo uploaded on this channel: say so, but still allow saving the zone.
    expect(getByText(/No station logo has been uploaded yet/)).toBeTruthy()
    fireEvent.click(getByText('Add zone'))
    expect(onSubmit.mock.calls[0][0].image_asset_ref).toBe('station-logo')
    expect(queryByText(/No station logo has been uploaded yet/)).toBeTruthy()
  })

  it('drops the note once the channel has a logo, and clears the reference for other sources', () => {
    const onSubmit = vi.fn()
    const { getByLabelText, getByText, queryByText } = render(
      <ZoneForm feeds={[]} stationLogoPresent submitting={false} onSubmit={onSubmit} />,
    )
    fireEvent.change(getByLabelText('Content source'), { target: { value: 'image' } })
    fireEvent.change(getByLabelText('Zone image'), { target: { value: 'station-logo' } })
    expect(queryByText(/No station logo has been uploaded yet/)).toBeNull()
    fireEvent.change(getByLabelText('Content source'), { target: { value: 'manual' } })
    fireEvent.click(getByText('Add zone'))
    expect(onSubmit.mock.calls[0][0].image_asset_ref).toBeNull()
  })

  it('includes parsed allowed_tags in the payload', () => {
    const onSubmit = vi.fn()
    const { getByText, getByLabelText } = render(<ZoneForm feeds={[]} submitting={false} onSubmit={onSubmit} />)
    fireEvent.change(getByLabelText('Allowed tags'), { target: { value: 'events, alerts, events' } })
    fireEvent.click(getByText('Add zone'))
    expect(onSubmit.mock.calls[0][0].allowed_tags).toEqual(['events', 'alerts'])
  })

  it('edit mode prefills and uses a Save-changes label', () => {
    const zone: CgZoneConfig = {
      zone_id: 'z1',
      board_id: 'b1',
      region: 'lower',
      zone_kind: 'ticker',
      content_source: 'manual',
      approval_required: false,
      manual_text: 'Welcome',
      created_at: '2026-01-01T00:00:00Z',
    }
    const { getByText, getByDisplayValue } = render(
      <ZoneForm feeds={[]} submitting={false} onSubmit={vi.fn()} initial={zone} />,
    )
    expect(getByDisplayValue('Welcome')).toBeTruthy()
    expect(getByText('Save changes')).toBeTruthy()
  })

  it('keeps audio out of visual zones and directs operators to board audio control', () => {
    const { getByLabelText } = render(
      <ZoneForm feeds={[]} submitting={false} onSubmit={vi.fn()} />,
    )
    const zoneKind = getByLabelText('Zone kind') as HTMLSelectElement
    const audio = zoneKind.querySelector('option[value="audio"]') as HTMLOptionElement
    expect(audio.disabled).toBe(true)
    expect(audio.textContent).toMatch(/configured below the Board card/i)
    expect((getByLabelText('Content source') as HTMLSelectElement).querySelector('option[value="live_video"]')).toBeTruthy()
  })

  it('requires a configured source for a live-video zone', () => {
    const onSubmit = vi.fn()
    const { getByLabelText, getByText } = render(
      <ZoneForm
        feeds={[]}
        liveSources={[{ live_source_id: 'camera-1', channel_id: 'public', name: 'Chamber camera', source_type: 'srt' }]}
        submitting={false}
        onSubmit={onSubmit}
      />,
    )
    fireEvent.change(getByLabelText('Content source'), { target: { value: 'live_video' } })
    expect((getByText('Add zone') as HTMLButtonElement).disabled).toBe(true)
    fireEvent.change(getByLabelText('Live video source'), { target: { value: 'camera-1' } })
    fireEvent.click(getByText('Add zone'))
    expect(onSubmit.mock.calls[0][0].live_source_id).toBe('camera-1')
  })
})

describe('FeedForm', () => {
  it('keeps the feed kind immutable while editing an existing feed', () => {
    const { getByLabelText } = render(
      <FeedForm initial={FEED} submitting={false} onSubmit={vi.fn()} />,
    )
    expect((getByLabelText('Feed kind') as HTMLSelectElement).disabled).toBe(true)
  })

  it('converts refresh minutes to seconds on submit', () => {
    const onSubmit = vi.fn()
    const { getByText, getByLabelText } = render(
      <FeedForm submitting={false} onSubmit={onSubmit} />,
    )
    fireEvent.change(getByLabelText('Feed label'), { target: { value: 'News' } })
    fireEvent.change(getByLabelText('Source URL'), {
      target: { value: 'https://x.gov/n.rss' },
    })
    fireEvent.change(getByLabelText('Refresh (minutes)'), { target: { value: '15' } })
    fireEvent.change(getByLabelText('Feed tags'), { target: { value: 'events, community' } })
    fireEvent.click(getByText('Register feed'))
    const payload = onSubmit.mock.calls[0][0]
    expect(payload.refresh_seconds).toBe(900)
    expect(payload.tags).toEqual(['events', 'community'])
  })

  it('blocks a public-permitted weather feed', () => {
    const { getByText, getByLabelText } = render(
      <FeedForm submitting={false} onSubmit={vi.fn()} />,
    )
    fireEvent.change(getByLabelText('Feed label'), { target: { value: 'WX' } })
    fireEvent.change(getByLabelText('Source URL'), {
      target: { value: 'https://x.gov/wx.json' },
    })
    fireEvent.change(getByLabelText('Feed kind'), { target: { value: 'weather' } })
    fireEvent.change(getByLabelText('Trust tier'), { target: { value: 'public_permitted' } })
    expect(getByText(/must be operator or partner curated/)).toBeTruthy()
    expect((getByText('Register feed') as HTMLButtonElement).disabled).toBe(true)
  })
})

describe('BoardPreviewPanel', () => {
  it('shows zones, the back-filled note, and a degraded zone warning', () => {
    const resolved: ResolvedBoard = {
      board_id: 'b1',
      backfilled_kinds: ['logo'],
      degraded_zone_ids: ['z_feed'],
      snapshot: {
        snapshot_id: 'public-community-board',
        generated_at: '2026-06-01T18:00:00Z',
        channel_id: 'public',
        template: { template_id: 'standard-community-board', label: 'Standard', aspect_ratio: '16:9', regions: [] },
        zones: [
          { zone_id: 'z_feed', kind: 'ticker', title: 'City news', source: 'feed_adapter', content: {}, approved: true },
          { zone_id: 'z_manual', kind: 'primary', title: 'Now showing', source: 'manual', content: {}, approved: true },
        ],
        hls_render_path: '/x.m3u8',
        portal_render_path: '/snap',
        proof_boundary: 'pb',
      },
    }
    const { container, getByText } = render(<BoardPreviewPanel resolved={resolved} />)
    expect(getByText(/Using defaults for unconfigured zones: logo/)).toBeTruthy()
    expect(container.textContent).toContain('City news')
    expect(getByText(/Feed unavailable/)).toBeTruthy()
  })
})

// --- container role gate (mocked client) ---

vi.mock('../api/client', () => ({
  ApiError: class ApiError extends Error {
    status: number
    constructor(message: string, status: number) {
      super(message)
      this.status = status
    }
  },
  getStaffIdentity: vi.fn(),
  getAppPlatformConfig: vi.fn(),
  getCgBoard: vi.fn(),
  getCgBoardAudio: vi.fn(),
  listStaffAssets: vi.fn(),
  listLiveSources: vi.fn(),
  setCgBoardAudio: vi.fn(),
  deleteCgBoardAudio: vi.fn(),
  previewCgBoard: vi.fn(),
  listCgBoardAudit: vi.fn(),
  createCgBoard: vi.fn(),
  updateCgBoard: vi.fn(),
  addCgZone: vi.fn(),
  updateCgZone: vi.fn(),
  deleteCgZone: vi.fn(),
  addCgFeed: vi.fn(),
  updateCgFeed: vi.fn(),
  deleteCgFeed: vi.fn(),
}))

import type { BoardView, StaffIdentityResponse } from '../types/api.generated'
import {
  ApiError,
  getAppPlatformConfig,
  getCgBoard,
  getCgBoardAudio,
  getStaffIdentity,
  listCgBoardAudit,
  listLiveSources,
  listStaffAssets,
  previewCgBoard,
  updateCgFeed,
} from '../api/client'
import { CgBoardDesignerScreen } from './CgBoardDesignerScreen'

function identity(roles: StaffIdentityResponse['roles']): StaffIdentityResponse {
  return { operator_id: 'op', operator_display_name: 'Op', roles }
}

function renderScreen() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <CgBoardDesignerScreen />
    </QueryClientProvider>,
  )
}

describe('CgBoardDesignerScreen container role gate', () => {
  it('sends only mutable feed fields when editing a feed', async () => {
    vi.clearAllMocks()
    vi.mocked(getStaffIdentity).mockResolvedValue(identity(['publish_operator']))
    vi.mocked(getAppPlatformConfig).mockResolvedValue({ channels: [] } as never)
    vi.mocked(getCgBoard).mockResolvedValue({
      board: {
        board_id: 'board_public',
        channel_id: 'public',
        template_id: 'standard-community-board',
        created_by: 'op',
        created_at: '2026-01-01T00:00:00Z',
        updated_at: '2026-01-01T00:00:00Z',
      },
      zones: [],
      feeds: [FEED],
    } as BoardView)
    vi.mocked(getCgBoardAudio).mockResolvedValue(null)
    vi.mocked(listStaffAssets).mockResolvedValue([])
    vi.mocked(listLiveSources).mockResolvedValue([])
    vi.mocked(previewCgBoard).mockRejectedValue(new ApiError('Not found', 404))
    vi.mocked(listCgBoardAudit).mockResolvedValue([])
    vi.mocked(updateCgFeed).mockResolvedValue(FEED)

    const { findByText, getByLabelText, getByText } = renderScreen()
    await findByText('City news')
    fireEvent.click(getByText('Edit'))
    fireEvent.change(getByLabelText('Feed label'), { target: { value: 'Updated city news' } })
    fireEvent.click(getByText('Save changes'))

    await waitFor(() => expect(updateCgFeed).toHaveBeenCalledTimes(1))
    expect(updateCgFeed).toHaveBeenCalledWith('public', 'feed_rss', {
      label: 'Updated city news',
      source_url: 'https://x.gov/news.rss',
      trust_tier: 'operator_curated',
      refresh_seconds: 900,
      enabled: true,
      tags: [],
    })
  })

  it('shows an access note for an operator without a CG role', async () => {
    vi.mocked(getStaffIdentity).mockResolvedValue(identity(['meeting_operator']))
    vi.mocked(getAppPlatformConfig).mockResolvedValue({ channels: [] } as never)
    const { findByText } = renderScreen()
    expect(await findByText(/requires the publish operator, setup admin, or support admin role/)).toBeTruthy()
  })

  it('offers the create-board form to a publish operator with no board', async () => {
    vi.mocked(getStaffIdentity).mockResolvedValue(identity(['publish_operator']))
    vi.mocked(getAppPlatformConfig).mockResolvedValue({ channels: [] } as never)
    vi.mocked(getCgBoard).mockResolvedValue(null)
    const { findByText } = renderScreen()
    expect(await findByText('Create a CG board for this channel')).toBeTruthy()
  })

  it('is read-only for a support admin (no create affordance)', async () => {
    vi.mocked(getStaffIdentity).mockResolvedValue(identity(['support_admin']))
    vi.mocked(getAppPlatformConfig).mockResolvedValue({ channels: [] } as never)
    vi.mocked(getCgBoard).mockResolvedValue(null)
    vi.mocked(listCgBoardAudit).mockResolvedValue([])
    const { findByText, queryByText } = renderScreen()
    expect(await findByText('No board on this channel yet.')).toBeTruthy()
    expect(queryByText('Create a CG board for this channel')).toBeNull()
  })
})
