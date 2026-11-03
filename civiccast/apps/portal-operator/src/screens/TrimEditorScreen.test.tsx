import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'

import type { AssetRow } from '../types/asset'
import { ToastContext } from '../components/toast-context'

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
  getStaffAssetPlaybackGrant: vi.fn(),
  updateStaffAsset: vi.fn(),
}))

vi.mock('hls.js', () => ({
  default: class HlsStub {
    static isSupported() {
      return false
    }
  },
}))

import { getStaffAsset, getStaffAssetPlaybackGrant } from '../api/client'
import { TrimEditorScreen } from './TrimEditorScreen'

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
})

function asset(): AssetRow {
  return {
    asset_id: 'recording-1',
    title: 'Council meeting',
    description: null,
    meeting_body: null,
    state: 'recorded',
    manifest_url: null,
    published_at: null,
    file_path: '/recordings/recording-1.ts',
    file_size_bytes: 45_000_000,
    duration_seconds: 20,
    codec_video: 'h264',
    codec_audio: 'aac',
    width_px: 1280,
    height_px: 720,
    bitrate_bps: null,
    format_name: 'mpegts',
    trim_in_seconds: 5,
    trim_out_seconds: 10,
    chapters: [],
    retention_policy: 'default',
    retention_until: null,
    retention_term_unit: null,
    retention_term_value: null,
    retention_anchor_at: null,
    version: 1,
    source_live_session_id: null,
  }
}

function renderEditor() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={queryClient}>
      <ToastContext.Provider value={{ push: vi.fn() }}>
        <TrimEditorScreen assetId="recording-1" onClose={vi.fn()} />
      </ToastContext.Provider>
    </QueryClientProvider>,
  )
}

beforeEach(() => {
  vi.clearAllMocks()
  vi.mocked(getStaffAsset).mockResolvedValue(asset())
  vi.mocked(getStaffAssetPlaybackGrant).mockResolvedValue({
    url: '/media/staff-source/recording-1/source',
    packaged_url: '/media/staff-source/recording-1/hls/playlist.m3u8',
    playback_kind: 'hls',
    source_content_type: 'video/mp2t',
    source_offset_seconds: 5,
    expires_at: '2026-10-12T12:15:00Z',
  })
  vi.spyOn(HTMLMediaElement.prototype, 'canPlayType').mockReturnValue('probably')
  vi.spyOn(HTMLMediaElement.prototype, 'load').mockImplementation(() => {})
})

describe('TrimEditorScreen source preview', () => {
  it('plays packaged HLS and maps its rebased clock to the selected source range', async () => {
    const { findByLabelText } = renderEditor()
    const video = (await findByLabelText('Packaged preview for Council meeting')) as HTMLVideoElement
    await waitFor(() =>
      expect(new URL(video.src).pathname).toBe('/media/staff-source/recording-1/hls/playlist.m3u8'),
    )
    await waitFor(() => expect(getStaffAssetPlaybackGrant).toHaveBeenCalledWith('recording-1'))

    Object.defineProperties(video, {
      currentTime: { configurable: true, writable: true, value: 0 },
      duration: { configurable: true, value: 5 },
      readyState: { configurable: true, value: 1 },
    })
    fireEvent.loadedMetadata(video)
    expect(video.currentTime).toBe(0)

    const pause = vi.fn()
    Object.defineProperties(video, {
      currentTime: { configurable: true, writable: true, value: 5.25 },
      paused: { configurable: true, value: false },
      pause: { configurable: true, value: pause },
    })
    fireEvent.timeUpdate(video)

    expect(pause).toHaveBeenCalledOnce()
    expect(video.currentTime).toBe(5)
  })

  it('does not fall back to a raw source when no completed HLS package exists', async () => {
    vi.mocked(getStaffAssetPlaybackGrant).mockResolvedValue({
      url: '/media/staff-source/recording-1/source',
      packaged_url: null,
      playback_kind: 'source',
      source_content_type: 'video/mp4',
      source_offset_seconds: 0,
      expires_at: '2026-10-12T12:15:00Z',
    })
    const { findByText, queryByLabelText } = renderEditor()

    expect(
      await findByText(/needs an HLS package before its trim can be previewed/i),
    ).toBeTruthy()
    expect(queryByLabelText('Packaged preview for Council meeting')).toBeNull()
  })

  it('shows a friendly retry state when source access is unavailable', async () => {
    vi.mocked(getStaffAssetPlaybackGrant).mockRejectedValue(new Error('forbidden'))
    const { findByRole, findByText } = renderEditor()
    expect(await findByText('Preview is unavailable')).toBeTruthy()
    expect(await findByRole('button', { name: 'Retry preview' })).toBeTruthy()
    expect(await findByText(/source preview needs an active staff account/i)).toBeTruthy()
  })

  it('reattaches playback after a media error and same-URL grant refresh', async () => {
    const { findByRole, findByLabelText } = renderEditor()
    const failedVideo = (await findByLabelText(
      'Packaged preview for Council meeting',
    )) as HTMLVideoElement
    await waitFor(() =>
      expect(new URL(failedVideo.src).pathname).toBe(
        '/media/staff-source/recording-1/hls/playlist.m3u8',
      ),
    )

    fireEvent.error(failedVideo)
    fireEvent.click(await findByRole('button', { name: 'Retry preview' }))

    const retriedVideo = (await findByLabelText(
      'Packaged preview for Council meeting',
    )) as HTMLVideoElement
    expect(retriedVideo).not.toBe(failedVideo)
    await waitFor(() =>
      expect(new URL(retriedVideo.src).pathname).toBe(
        '/media/staff-source/recording-1/hls/playlist.m3u8',
      ),
    )
    expect(getStaffAssetPlaybackGrant).toHaveBeenCalledTimes(2)
  })
})

describe('TrimEditorScreen Space key handling', () => {
  it('does not intercept Space on the focused video in paused or playing state', async () => {
    const { findByLabelText } = renderEditor()
    const video = (await findByLabelText(
      'Packaged preview for Council meeting',
    )) as HTMLVideoElement
    const play = vi.fn().mockResolvedValue(undefined)
    const pause = vi.fn()
    Object.defineProperties(video, {
      currentTime: { configurable: true, writable: true, value: 0 },
      duration: { configurable: true, value: 5 },
      readyState: { configurable: true, value: 1 },
      paused: { configurable: true, value: true },
      play: { configurable: true, value: play },
      pause: { configurable: true, value: pause },
    })

    expect(fireEvent.keyDown(video, { key: ' ' })).toBe(true)
    expect(play).not.toHaveBeenCalled()
    expect(pause).not.toHaveBeenCalled()

    Object.defineProperty(video, 'paused', {
      configurable: true,
      value: false,
    })
    expect(fireEvent.keyDown(video, { key: ' ' })).toBe(true)
    expect(play).not.toHaveBeenCalled()
    expect(pause).not.toHaveBeenCalled()
  })

  it('still toggles playback with Space from a non-input button, once per press', async () => {
    const { findByLabelText, findByRole } = renderEditor()
    const video = (await findByLabelText(
      'Packaged preview for Council meeting',
    )) as HTMLVideoElement
    const play = vi.fn().mockResolvedValue(undefined)
    const pause = vi.fn()
    Object.defineProperties(video, {
      currentTime: { configurable: true, writable: true, value: 0 },
      duration: { configurable: true, value: 5 },
      readyState: { configurable: true, value: 1 },
      paused: { configurable: true, value: true },
      play: { configurable: true, value: play },
      pause: { configurable: true, value: pause },
    })
    const button = await findByRole('button', { name: 'Close trim editor' })
    button.focus()

    expect(fireEvent.keyDown(button, { key: ' ' })).toBe(false)
    expect(play).toHaveBeenCalledOnce()
    expect(pause).not.toHaveBeenCalled()

    Object.defineProperty(video, 'paused', {
      configurable: true,
      value: false,
    })
    expect(fireEvent.keyDown(button, { key: ' ' })).toBe(false)
    expect(pause).toHaveBeenCalledOnce()
    expect(play).toHaveBeenCalledOnce()
  })
})
