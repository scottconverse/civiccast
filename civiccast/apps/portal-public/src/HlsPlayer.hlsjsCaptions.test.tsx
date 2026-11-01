// SPDX-License-Identifier: Apache-2.0
// Copyright (c) The CivicCast Authors
//
// Regression coverage for the hls.js/MediaSource caption-selection race
// root-caused on the webkit CI lane (a11y.spec.ts's "caption controls..."
// test): hls.js's SubtitleTrackController polls the video's native
// <track> elements to keep hls.subtitleTrack in sync with the browser's
// own native captions UI. On WebKit (no TextTrackList#onchange) that poll
// can sample before the native <track> DOM element for the
// just-auto-selected default subtitle exists -- creating it requires an
// async fetch of the subtitle sub-playlist -- and, seeing nothing marked
// "showing" yet, force-resets hls.subtitleTrack to -1
// (video-dev/hls.js#1948, #4345). HlsPlayer.tsx now tracks what its own UI
// asked for (desiredSubtitleTrackRef) independently of hls.js's internal
// trackId and reasserts it whenever a SUBTITLE_TRACK_SWITCH disagrees, so
// the fake hls.js below simulates exactly that spurious internal
// correction and asserts the component recovers.
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { HlsPlayer, type HlsPlayerHandle } from './HlsPlayer'
import { EmbedPlayer } from './EmbedPlayer'

const Events = {
  MEDIA_ATTACHED: 'hlsMediaAttached',
  MANIFEST_PARSED: 'hlsManifestParsed',
  SUBTITLE_TRACKS_UPDATED: 'hlsSubtitleTracksUpdated',
  SUBTITLE_TRACK_SWITCH: 'hlsSubtitleTrackSwitch',
  ERROR: 'hlsError',
} as const

const ErrorTypes = {
  NETWORK_ERROR: 'networkError',
  OTHER_ERROR: 'otherError',
} as const

interface FakeSubtitleTrack {
  name: string
  lang: string
  default: boolean
}

/**
 * Minimal fake standing in for hls.js's public surface, with two seams a
 * real hls.js instance does not expose: `emitSubtitleTracksUpdated()` lets
 * a test drive the exact "tracks known, but hls.subtitleTrack has not been
 * assigned yet" instant a real SUBTITLE_TRACKS_UPDATED listener observes,
 * and `simulateSpuriousPollReset()` drives hls.js's own internal
 * native-<track>-polling correction directly (bypassing the public
 * `subtitleTrack` setter HlsPlayer itself calls) so the test can assert
 * HlsPlayer fights back against it exactly like the real bug required.
 */
class FakeHls {
  readonly config: Record<string, unknown>

  constructor(config: Record<string, unknown> = {}) {
    this.config = config
  }

  static isSupported() {
    return true
  }
  static Events = Events
  static ErrorTypes = ErrorTypes

  private listeners = new Map<string, Array<(event: string, data: unknown) => void>>()
  private _subtitleTracks: FakeSubtitleTrack[] = []
  private _subtitleTrack = -1
  playingDate: Date | null = null
  subtitleDisplay = true
  media: HTMLMediaElement | undefined

  on(event: string, cb: (event: string, data: unknown) => void) {
    const list = this.listeners.get(event) ?? []
    list.push(cb)
    this.listeners.set(event, list)
  }

  off(event: string, cb: (event: string, data: unknown) => void) {
    this.listeners.set(event, (this.listeners.get(event) ?? []).filter((listener) => listener !== cb))
  }

  trigger(event: string, data: unknown) {
    this.emit(event, data)
  }

  private emit(event: string, data: unknown) {
    for (const cb of this.listeners.get(event) ?? []) cb(event, data)
  }

  attachMedia(media: HTMLMediaElement) {
    this.media = media
    this.emit(Events.MEDIA_ATTACHED, {})
  }
  loadSource() {
    this.emit(Events.MANIFEST_PARSED, {})
  }
  destroy() {}

  get subtitleTracks() {
    return this._subtitleTracks
  }
  get subtitleTrack() {
    return this._subtitleTrack
  }
  set subtitleTrack(id: number) {
    this._subtitleTrack = id
    this.emit(Events.SUBTITLE_TRACK_SWITCH, { id })
  }

  /** Simulates hls.js announcing tracks before it has assigned a default. */
  seedTracks(tracks: FakeSubtitleTrack[]) {
    this._subtitleTracks = tracks
    this.emit(Events.SUBTITLE_TRACKS_UPDATED, {})
  }

  /**
   * Simulates the actual upstream bug: hls.js's own native-<track>-polling
   * reconciliation deciding (wrongly, mid-load) that nothing is showing
   * and forcing its internal trackId back to -1, WITHOUT that request
   * coming from HlsPlayer's own `subtitleTrack =` setter.
   */
  simulateSpuriousPollReset() {
    this._subtitleTrack = -1
    this.emit(Events.SUBTITLE_TRACK_SWITCH, { id: -1 })
  }
}

let lastInstance: FakeHls | undefined

vi.mock('hls.js', () => ({
  default: class extends FakeHls {
    constructor(config: Record<string, unknown>) {
      super(config)
      // Test double needs to hand its own instance back to the test so it
      // can drive seedTracks()/simulateSpuriousPollReset() on the exact
      // instance HlsPlayer constructed.
      // eslint-disable-next-line @typescript-eslint/no-this-alias
      lastInstance = this
    }
  },
}))

afterEach(() => {
  vi.restoreAllMocks()
  lastInstance = undefined
})

describe('HlsPlayer hls.js captions', () => {
  it('enables recovery outside the served live window without shrinking the normal buffer', async () => {
    render(<HlsPlayer manifestUrl="/media/live/public/playlist.m3u8" />)
    await waitFor(() => expect(lastInstance).toBeDefined())
    expect(lastInstance?.config).toMatchObject({
      liveSyncDurationCount: 3,
      liveMaxLatencyDurationCount: 6,
    })
  })

  it('prerolls a tiny startup gap before forwarding its seek to the native decoder', async () => {
    const seek = vi.spyOn(HTMLMediaElement.prototype, 'currentTime', 'set')
    render(<HlsPlayer manifestUrl="/short-vod/playlist.m3u8" />)
    await waitFor(() => expect(lastInstance).toBeDefined())
    const video = (lastInstance as FakeHls).media!
    Object.defineProperty(video, 'buffered', {
      configurable: true, value: { length: 1, start: () => 0.021333, end: () => 2 },
    })
    Object.defineProperty(video, 'readyState', { configurable: true, value: 1 })
    video.currentTime = 0.1
    video.currentTime = 0.1
    expect(video.currentTime).toBe(0)
    expect(seek).not.toHaveBeenCalled()
    Object.defineProperty(video, 'readyState', { configurable: true, value: 2 })
    fireEvent.loadedData(video)
    expect(video.currentTime).toBe(0.1)
    expect(seek).toHaveBeenCalledOnce()
    expect(Object.hasOwn(video, 'currentTime')).toBe(false)
  })

  it('keeps already playable startup data ready when a tiny gap seek was deferred', async () => {
    const nativeTime = Object.getOwnPropertyDescriptor(HTMLMediaElement.prototype, 'currentTime')!
    let readyState = 1
    const seek = vi.spyOn(HTMLMediaElement.prototype, 'currentTime', 'set').mockImplementation(function (this: HTMLMediaElement, target) {
      nativeTime.set!.call(this, target)
      // A native seek can discard the ready state before queued playing events.
      readyState = 1
    })
    render(<HlsPlayer manifestUrl="/short-vod/playlist.m3u8" />)
    await waitFor(() => expect(lastInstance).toBeDefined())
    const video = (lastInstance as FakeHls).media!
    Object.defineProperty(video, 'buffered', {
      configurable: true, value: { length: 1, start: () => 0.021333, end: () => 2 },
    })
    Object.defineProperty(video, 'readyState', { configurable: true, get: () => readyState })
    video.currentTime = 0.1
    expect(seek).not.toHaveBeenCalled()
    readyState = 4
    fireEvent.loadedData(video)
    expect(video.readyState).toBe(4)
    expect(seek).not.toHaveBeenCalled()
    expect(video.currentTime).toBe(0)
    expect(Object.hasOwn(video, 'currentTime')).toBe(false)
  })

  it.each([
    { start: 0.2, target: 0.3 },
    { start: 0.021333, target: 0.5 },
    { start: 0, target: 0.1 },
  ])('forwards seeks outside the tiny startup gap ($start -> $target)', async ({ start, target }) => {
    render(<HlsPlayer manifestUrl="/vod/playlist.m3u8" />)
    await waitFor(() => expect(lastInstance).toBeDefined())
    const video = (lastInstance as FakeHls).media!
    Object.defineProperty(video, 'buffered', {
      configurable: true, value: { length: 1, start: () => start, end: () => 2 },
    })
    Object.defineProperty(video, 'readyState', { configurable: true, value: 1 })
    video.currentTime = target
    expect(video.currentTime).toBe(target)
    expect(Object.hasOwn(video, 'currentTime')).toBe(false)
  })

  it('lets a deliberate seek supersede a pending startup jump', async () => {
    render(<HlsPlayer manifestUrl="/vod/playlist.m3u8" />)
    await waitFor(() => expect(lastInstance).toBeDefined())
    const video = (lastInstance as FakeHls).media!
    Object.defineProperty(video, 'buffered', {
      configurable: true, value: { length: 1, start: () => 0.021333, end: () => 2 },
    })
    Object.defineProperty(video, 'readyState', { configurable: true, value: 1 })
    video.currentTime = 0.1
    video.currentTime = 0.5
    Object.defineProperty(video, 'readyState', { configurable: true, value: 2 })
    fireEvent.loadedData(video)
    expect(video.currentTime).toBe(0.5)
  })

  it.each(['handle', 'startSeconds'])('preserves an explicit tiny seek through %s', async (path) => {
    const handleRef = { current: null as HlsPlayerHandle | null }
    const { rerender } = render(<HlsPlayer manifestUrl="/vod/playlist.m3u8" handleRef={handleRef} />)
    await waitFor(() => expect(lastInstance).toBeDefined())
    const video = (lastInstance as FakeHls).media!
    Object.defineProperty(video, 'buffered', {
      configurable: true, value: { length: 1, start: () => 0.021333, end: () => 2 },
    })
    Object.defineProperty(video, 'readyState', { configurable: true, value: 1 })
    Object.defineProperty(video, 'paused', { configurable: true, value: false })
    video.currentTime = 0.1
    expect(video.currentTime).toBe(0)
    if (path === 'handle') handleRef.current!.seekTo(0.1)
    else rerender(<HlsPlayer manifestUrl="/vod/playlist.m3u8" handleRef={handleRef} startSeconds={0.1} />)
    expect(video.currentTime).toBe(0.1)
    expect(Object.hasOwn(video, 'currentTime')).toBe(false)
  })

  it('waits for a paused chapter seek to finish before resuming playback', async () => {
    const handleRef = { current: null as HlsPlayerHandle | null }
    const nativeTime = Object.getOwnPropertyDescriptor(HTMLMediaElement.prototype, 'currentTime')!
    let seeking = false
    vi.spyOn(HTMLMediaElement.prototype, 'currentTime', 'set').mockImplementation(function (this: HTMLMediaElement, target) {
      nativeTime.set!.call(this, target)
      seeking = true
    })
    render(<HlsPlayer manifestUrl="/vod/playlist.m3u8" handleRef={handleRef} />)
    await waitFor(() => expect(lastInstance).toBeDefined())
    const video = (lastInstance as FakeHls).media!
    const play = vi.fn().mockResolvedValue(undefined)
    Object.defineProperty(video, 'play', { configurable: true, value: play })
    Object.defineProperty(video, 'seeking', { configurable: true, get: () => seeking })
    Object.defineProperty(video, 'readyState', { configurable: true, value: 3 })
    handleRef.current!.seekTo(0.5)
    expect(video.currentTime).toBe(0.5)
    expect(play).not.toHaveBeenCalled()
    seeking = false
    fireEvent.seeked(video)
    expect(play).toHaveBeenCalledOnce()
    fireEvent.seeked(video)
    expect(play).toHaveBeenCalledOnce()
  })

  it.each(['new seek', 'source change', 'fatal error', 'unmount'])('cancels a pending chapter resume on %s', async (ending) => {
    const handleRef = { current: null as HlsPlayerHandle | null }
    const nativeTime = Object.getOwnPropertyDescriptor(HTMLMediaElement.prototype, 'currentTime')!
    let seeking = false
    vi.spyOn(HTMLMediaElement.prototype, 'currentTime', 'set').mockImplementation(function (this: HTMLMediaElement, target) {
      nativeTime.set!.call(this, target)
      seeking = true
    })
    const { rerender, unmount } = render(<HlsPlayer manifestUrl="/vod/playlist.m3u8" handleRef={handleRef} />)
    await waitFor(() => expect(lastInstance).toBeDefined())
    const hls = lastInstance as FakeHls
    const video = hls.media!
    const play = vi.fn().mockResolvedValue(undefined)
    Object.defineProperty(video, 'play', { configurable: true, value: play })
    Object.defineProperty(video, 'seeking', { configurable: true, get: () => seeking })
    Object.defineProperty(video, 'readyState', { configurable: true, value: 3 })
    handleRef.current!.seekTo(0.5)
    if (ending === 'new seek') handleRef.current!.seekTo(0.75)
    else if (ending === 'source change') rerender(<HlsPlayer manifestUrl="/other/playlist.m3u8" handleRef={handleRef} />)
    else if (ending === 'fatal error') hls.trigger(Events.ERROR, { fatal: true, type: ErrorTypes.NETWORK_ERROR })
    else unmount()
    seeking = false
    fireEvent.seeked(video)
    expect(play).toHaveBeenCalledTimes(ending === 'new seek' ? 1 : 0)
    if (ending === 'new seek') expect(video.currentTime).toBe(0.75)
  })

  it.each([false, true])('preserves immediate chapter behavior when paused=%s and no native seek is pending', async (paused) => {
    const handleRef = { current: null as HlsPlayerHandle | null }
    render(<HlsPlayer manifestUrl="/vod/playlist.m3u8" handleRef={handleRef} />)
    await waitFor(() => expect(lastInstance).toBeDefined())
    const video = (lastInstance as FakeHls).media!
    const play = vi.fn().mockResolvedValue(undefined)
    Object.defineProperty(video, 'play', { configurable: true, value: play })
    Object.defineProperty(video, 'paused', { configurable: true, value: paused })
    Object.defineProperty(video, 'readyState', { configurable: true, value: 3 })
    handleRef.current!.seekTo(0)
    expect(video.currentTime).toBe(0)
    expect(play).toHaveBeenCalledTimes(paused ? 1 : 0)
  })

  it('preserves an existing nonconfigurable currentTime accessor', async () => {
    const { container } = render(<HlsPlayer manifestUrl="/vod/playlist.m3u8" />)
    const video = container.querySelector('video')!
    const getter = () => 0.25
    Object.defineProperty(video, 'currentTime', { configurable: false, get: getter })
    await waitFor(() => expect(lastInstance).toBeDefined())
    expect(Object.getOwnPropertyDescriptor(video, 'currentTime')?.get).toBe(getter)
  })

  it('leaves requested nonzero starts under native seek control', async () => {
    render(<HlsPlayer manifestUrl="/vod/playlist.m3u8" startSeconds={0.05} />)
    await waitFor(() => expect(lastInstance).toBeDefined())
    const video = (lastInstance as FakeHls).media!
    Object.defineProperty(video, 'readyState', { configurable: true, value: 1 })
    fireEvent.loadedMetadata(video)
    expect(video.currentTime).toBe(0.05)
    expect(Object.hasOwn(video, 'currentTime')).toBe(false)
  })

  it.each(['unmount', 'fatal error'])('restores native seek access on %s', async (ending) => {
    const { unmount } = render(<HlsPlayer manifestUrl="/vod/playlist.m3u8" />)
    await waitFor(() => expect(lastInstance).toBeDefined())
    const hls = lastInstance as FakeHls
    const video = hls.media!
    expect(Object.hasOwn(video, 'currentTime')).toBe(true)
    if (ending === 'unmount') unmount()
    else hls.trigger(Events.ERROR, { fatal: true, type: ErrorTypes.NETWORK_ERROR })
    expect(Object.hasOwn(video, 'currentTime')).toBe(false)
  })

  it('labels buffered embedded playback separately from the newest live-edge title', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(JSON.stringify({
      title: 'Current program', manifest_url: '/live/playlist.m3u8',
      program_timeline: [
        { title: 'Earlier program', started_at: '2026-10-12T18:00:00Z' },
        { title: 'Current program', started_at: '2026-10-12T18:01:00Z' },
      ],
    }), { status: 200, headers: { 'Content-Type': 'application/json' } }))
    render(<EmbedPlayer kind="channel" identity="gov" />)
    await waitFor(() => expect(lastInstance).toBeDefined())
    const hls = lastInstance as FakeHls
    hls.playingDate = new Date('2026-10-12T18:00:40Z')
    const video = screen.getByLabelText('Meeting video player') as HTMLVideoElement
    Object.defineProperty(video, 'readyState', { configurable: true, value: 3 })
    fireEvent.canPlay(video)
    fireEvent.timeUpdate(video)
    await waitFor(() => expect(screen.getByLabelText('Program at playback position').textContent).toBe('Now playing: Earlier program'))
    expect(screen.getByRole('link', { name: 'Open station portal (live edge: Current program)' })).toBeTruthy()
  })
  it('shows the title for the buffered playback time instead of the newest source title', async () => {
    render(
      <HlsPlayer
        manifestUrl="/live/playlist.m3u8"
        programTimeline={[
          { title: 'Earlier program', started_at: '2026-10-12T18:00:00Z' },
          { title: 'Current program', started_at: '2026-10-12T18:01:00Z' },
        ]}
      />,
    )

    await waitFor(() => expect(lastInstance).toBeDefined())
    const hls = lastInstance as FakeHls
    hls.playingDate = new Date('2026-10-12T18:00:40Z')
    const video = screen.getByLabelText('Meeting video player') as HTMLVideoElement
    Object.defineProperty(video, 'readyState', { configurable: true, value: 3 })
    fireEvent.canPlay(video)
    fireEvent.timeUpdate(video)

    await waitFor(() => {
      expect(screen.getByLabelText('Program at playback position').textContent).toBe(
        'Now playing: Earlier program',
      )
    })
  })

  it('resumes playback after the manifest source changes while the viewer was watching', async () => {
    const { rerender } = render(<HlsPlayer manifestUrl="/first/playlist.m3u8" />)
    await waitFor(() => expect(lastInstance).toBeDefined())

    const video = screen.getByLabelText('Meeting video player') as HTMLVideoElement
    const play = vi.fn().mockResolvedValue(undefined)
    Object.defineProperty(video, 'paused', { configurable: true, value: false })
    Object.defineProperty(video, 'ended', { configurable: true, value: false })
    Object.defineProperty(video, 'play', { configurable: true, value: play })

    rerender(<HlsPlayer manifestUrl="/cdn/playlist.m3u8" />)
    await waitFor(() => expect(lastInstance).toBeDefined())
    video.dispatchEvent(new Event('canplay'))

    await waitFor(() => expect(play).toHaveBeenCalledOnce())
  })

  it('derives the default selection from the manifest, not from a stale hls.subtitleTrack read', async () => {
    render(<HlsPlayer manifestUrl="/hlsjs-captioned/playlist.m3u8" />)

    await waitFor(() => expect(lastInstance).toBeDefined())
    const hls = lastInstance as FakeHls

    // Real hls.js fires SUBTITLE_TRACKS_UPDATED before it assigns
    // hls.subtitleTrack for the manifest's DEFAULT=YES track (see
    // subtitle-track-controller.ts's switchLevel: the trigger() call
    // precedes the setSubtitleTrack() call). hls.subtitleTrack is still -1
    // at the exact instant this fires.
    hls.seedTracks([
      { name: 'English', lang: 'en', default: true },
      { name: 'Spanish', lang: 'es', default: false },
    ])

    await waitFor(() => {
      expect(screen.getByRole('button', { name: 'English' }).getAttribute('aria-pressed')).toBe(
        'true',
      )
    })
    expect(screen.getByRole('button', { name: 'Spanish' }).getAttribute('aria-pressed')).toBe(
      'false',
    )
  })

  it('reasserts the desired track when hls.js spuriously reverts it to -1', async () => {
    render(<HlsPlayer manifestUrl="/hlsjs-captioned/playlist.m3u8" />)

    await waitFor(() => expect(lastInstance).toBeDefined())
    const hls = lastInstance as FakeHls

    hls.seedTracks([
      { name: 'English', lang: 'en', default: true },
      { name: 'Spanish', lang: 'es', default: false },
    ])
    await waitFor(() => {
      expect(screen.getByRole('button', { name: 'English' }).getAttribute('aria-pressed')).toBe(
        'true',
      )
    })

    // The native-<track>-polling reconciliation misfires (the bug this
    // test exists for) and force-resets hls.js's internal selection.
    // HlsPlayer's SUBTITLE_TRACK_SWITCH handler reasserts synchronously
    // (same call stack, no re-render/tick needed), so by the time this
    // call returns hls.js's internal state is already corrected back --
    // that immediacy is itself part of what the fix buys over the old
    // Playwright-level toPass retry.
    hls.simulateSpuriousPollReset()
    expect(hls.subtitleTrack).toBe(0)

    // ...and the button UI (driven by React state) durably reflects it too.
    await waitFor(() => {
      expect(screen.getByRole('button', { name: 'English' }).getAttribute('aria-pressed')).toBe(
        'true',
      )
    })
    expect(hls.subtitleTrack).toBe(0)
  })

  it('an explicit "Off" click is not overwritten by a later manifest-default reseed', async () => {
    render(<HlsPlayer manifestUrl="/hlsjs-captioned/playlist.m3u8" />)

    await waitFor(() => expect(lastInstance).toBeDefined())
    const hls = lastInstance as FakeHls

    hls.seedTracks([
      { name: 'English', lang: 'en', default: true },
      { name: 'Spanish', lang: 'es', default: false },
    ])
    await waitFor(() => {
      expect(screen.getByRole('button', { name: 'English' }).getAttribute('aria-pressed')).toBe(
        'true',
      )
    })

    fireEvent.click(screen.getByRole('button', { name: 'Off' }))
    expect(screen.getByRole('button', { name: 'Off' }).getAttribute('aria-pressed')).toBe('true')

    // A second SUBTITLE_TRACKS_UPDATED (e.g. a mid-stream level switch)
    // must not silently re-select the manifest default over the viewer's
    // explicit choice -- this is exactly why "not yet decided" and
    // "explicitly off" are different states internally.
    hls.seedTracks([
      { name: 'English', lang: 'en', default: true },
      { name: 'Spanish', lang: 'es', default: false },
    ])

    expect(screen.getByRole('button', { name: 'Off' }).getAttribute('aria-pressed')).toBe('true')
    expect(screen.getByRole('button', { name: 'English' }).getAttribute('aria-pressed')).toBe(
      'false',
    )
  })
})
