import { useEffect, useRef, useState, type RefObject } from 'react'
import type Hls from 'hls.js'
import { emitAnalyticsEvent, type AnalyticsContext } from './analytics'
import type { PublicProgramTransition } from './types'

type PlayerState = 'idle' | 'loading' | 'ready' | 'error'

const HEARTBEAT_SECONDS = 60

/**
 * Imperative handle exposed to a parent that needs to drive the player
 * (S25 §6: agenda items seek the video to their timecode). The parent
 * passes in a ref; the player publishes a `seekTo` function on mount and
 * keeps it pointing at the live <video> element across manifest changes.
 */
export interface HlsPlayerHandle {
  seekTo: (seconds: number) => void
}

interface HlsPlayerProps {
  manifestUrl: string
  posterUrl?: string
  /** Privacy-safe playback analytics context (Stage G); omit to disable. */
  analytics?: AnalyticsContext
  /** Optional imperative handle for external seek (S25 agenda integration). */
  handleRef?: RefObject<HlsPlayerHandle | null>
  /** Actual source handoffs, mapped against the HLS playback clock. */
  programTimeline?: PublicProgramTransition[]
  startSeconds?: number
}

interface SubtitleTrackOption {
  id: number
  label: string
  language: string
}

function selectNativeTextTrack(nativeTracks: TextTrackList, trackId: number) {
  for (let index = 0; index < nativeTracks.length; index += 1) {
    nativeTracks[index].mode = index === trackId ? 'showing' : 'disabled'
  }
}

/**
 * HLS.js-backed VOD player.
 *
 * Native HLS (Safari, iOS) bypasses hls.js and uses the <video> element directly.
 * Other browsers dynamically import hls.js on mount for adaptive bitrate
 * switching across whatever ladder the manifest advertises — up to four
 * content renditions plus the slate fallback, and fewer when the source was
 * smaller than the top rungs (the packager never upscales). The player reads
 * the variant list from the manifest and makes no assumption about its
 * length. The dynamic import keeps the initial bundle small — first
 * paint of the portal page does not pay the hls.js download cost.
 */
export function HlsPlayer({
  manifestUrl,
  posterUrl,
  analytics,
  handleRef,
  programTimeline = [],
  startSeconds,
}: HlsPlayerProps) {
  const videoRef = useRef<HTMLVideoElement>(null)
  const hlsRef = useRef<Hls | null>(null)
  const resumeAfterSourceChangeRef = useRef(false)
  const sourceReadyRef = useRef(false)
  const restoreNativeSeekRef = useRef<() => void>(() => {})
  const cancelSeekResumeRef = useRef<() => void>(() => {})
  const programTimelineRef = useRef(programTimeline)
  const syncPlaybackTitleRef = useRef<() => void>(() => {})
  const [state, setState] = useState<PlayerState>('idle')
  useEffect(() => {
    const video = videoRef.current
    if (!video || startSeconds === undefined || !Number.isFinite(startSeconds)) return
    let applied = false
    const seek = () => {
      if (applied || video.readyState < 1) return
      applied = true
      cancelSeekResumeRef.current()
      restoreNativeSeekRef.current()
      video.currentTime = Number.isFinite(video.duration)
        ? Math.min(Math.max(0, startSeconds), video.duration) : Math.max(0, startSeconds)
    }
    video.addEventListener('loadedmetadata', seek)
    seek()
    return () => video.removeEventListener('loadedmetadata', seek)
  }, [manifestUrl, startSeconds])
  const [playbackTitle, setPlaybackTitle] = useState<string | null>(null)

  // Publish/withdraw the imperative seek handle for the agenda sidebar
  // (S25 §6). Clamp to [0, duration] when known, so a stale agenda timecode
  // beyond the video end falls onto the last frame instead of throwing.
  useEffect(() => {
    if (!handleRef) return
    handleRef.current = {
      seekTo: (seconds: number) => {
        const video = videoRef.current
        if (!video) return
        const duration = Number.isFinite(video.duration) ? video.duration : null
        const target = duration !== null ? Math.min(Math.max(0, seconds), duration) : Math.max(0, seconds)
        cancelSeekResumeRef.current()
        const shouldResume = video.paused
        restoreNativeSeekRef.current()
        video.currentTime = target
        if (!shouldResume) return
        const resume = () => {
          void video.play().catch(() => {
            // Autoplay can be blocked; the seek still landed, leave UI as-is.
          })
        }
        if (!video.seeking) {
          resume()
          return
        }
        // Keep paused chapter seeks paused through their native completion.
        // In the failing trace, resuming early reached EOS before DOM seeked.
        const cancelResume = () => {
          video.removeEventListener('seeked', onSeeked)
          if (cancelSeekResumeRef.current === cancelResume) {
            cancelSeekResumeRef.current = () => {}
          }
        }
        const onSeeked = () => {
          if (video.seeking) return
          cancelResume()
          resume()
        }
        cancelSeekResumeRef.current = cancelResume
        video.addEventListener('seeked', onSeeked)
      },
    }
    return () => {
      cancelSeekResumeRef.current()
      handleRef.current = null
    }
  }, [handleRef])
  const [errorMessage, setErrorMessage] = useState<string>('')
  const [subtitleTracks, setSubtitleTracks] = useState<SubtitleTrackOption[]>([])
  const [selectedSubtitleTrack, setSelectedSubtitleTrack] = useState<number>(-1)
  // Source of truth for "which subtitle track does CivicCast's own UI say is
  // active" -- see the long comment at its first use below for why hls.js's
  // own hls.subtitleTrack/SUBTITLE_TRACK_SWITCH cannot be trusted blindly.
  // null = not yet decided (distinct from -1, which is the legitimate,
  // explicit "Off" selection -- collapsing the two would make a real "Off"
  // choice get silently overwritten by the manifest default on the next
  // SUBTITLE_TRACKS_UPDATED, e.g. after a mid-stream level switch).
  const desiredSubtitleTrackRef = useRef<number | null>(null)
  const analyticsRef = useRef<AnalyticsContext | undefined>(analytics)
  useEffect(() => {
    analyticsRef.current = analytics
  }, [analytics])

  useEffect(() => {
    programTimelineRef.current = programTimeline
  }, [programTimeline])

  useEffect(() => {
    const video = videoRef.current
    if (!video) return
    const syncPlaybackTitle = () => {
      if (!sourceReadyRef.current) return
      const hlsDate = hlsRef.current?.playingDate ?? null
      const nativeStartDate = (video as HTMLVideoElement & { getStartDate?: () => Date }).getStartDate?.()
      const playbackDate = hlsDate ?? (
        nativeStartDate
          ? new Date(nativeStartDate.getTime() + video.currentTime * 1000)
          : null
      )
      if (!playbackDate || !Number.isFinite(playbackDate.getTime())) {
        setPlaybackTitle(null)
        return
      }
      const matchingTransition = programTimelineRef.current
        .filter((transition) => Date.parse(transition.started_at) <= playbackDate.getTime())
        .at(-1)
      setPlaybackTitle(matchingTransition?.title ?? null)
    }
    syncPlaybackTitleRef.current = syncPlaybackTitle
    video.addEventListener('timeupdate', syncPlaybackTitle)
    video.addEventListener('seeked', syncPlaybackTitle)
    video.addEventListener('loadedmetadata', syncPlaybackTitle)
    return () => {
      video.removeEventListener('timeupdate', syncPlaybackTitle)
      video.removeEventListener('seeked', syncPlaybackTitle)
      video.removeEventListener('loadedmetadata', syncPlaybackTitle)
      if (syncPlaybackTitleRef.current === syncPlaybackTitle) {
        syncPlaybackTitleRef.current = () => {}
      }
    }
  }, [])

  // Playback analytics (audit sprint Stage G): start once per source, a
  // coarse heartbeat while playing, complete on ended, error on failure.
  // Fail-silent and identifier-free; see src/analytics.ts.
  useEffect(() => {
    const video = videoRef.current
    const context = analyticsRef.current
    if (!video || !context) return

    let started = false
    let heartbeat: number | undefined
    const emit = (
      name: 'playback_start' | 'playback_heartbeat' | 'playback_complete',
      properties: Record<string, string | number | boolean> = {},
    ) => {
      emitAnalyticsEvent(name, { ...analyticsRef.current, properties })
    }
    const onPlay = () => {
      if (!started) {
        started = true
        emit('playback_start')
      }
      if (heartbeat === undefined) {
        heartbeat = window.setInterval(() => {
          if (!video.paused && !video.ended) {
            emit('playback_heartbeat', {
              position_seconds: Math.floor(video.currentTime),
            })
          }
        }, HEARTBEAT_SECONDS * 1000)
      }
    }
    const onEnded = () => {
      emit('playback_complete', { position_seconds: Math.floor(video.currentTime) })
    }
    video.addEventListener('play', onPlay)
    video.addEventListener('ended', onEnded)
    return () => {
      video.removeEventListener('play', onPlay)
      video.removeEventListener('ended', onEnded)
      if (heartbeat !== undefined) window.clearInterval(heartbeat)
    }
  }, [manifestUrl])

  const errorEmittedRef = useRef(false)
  useEffect(() => {
    errorEmittedRef.current = false
  }, [manifestUrl])
  useEffect(() => {
    if (state === 'error' && analyticsRef.current && !errorEmittedRef.current) {
      errorEmittedRef.current = true
      // Generic reason only — never URLs or error internals (privacy posture).
      emitAnalyticsEvent('playback_error', {
        ...analyticsRef.current,
        properties: { reason: 'playback_failed' },
      })
    }
  }, [state])

  useEffect(() => {
    const video = videoRef.current
    if (!video) return
    sourceReadyRef.current = false
    setPlaybackTitle(null)
    let shouldResumeAfterSwitch = resumeAfterSourceChangeRef.current
    resumeAfterSourceChangeRef.current = false
    const onCanPlay = () => {
      setState('ready')
      sourceReadyRef.current = true
      syncPlaybackTitleRef.current()
      if (!shouldResumeAfterSwitch) return
      shouldResumeAfterSwitch = false
      void video.play().catch(() => {
        // A browser may still reject programmatic playback; keep controls usable.
      })
    }
    video.addEventListener('canplay', onCanPlay)

    setState('loading')
    setErrorMessage('')
    setSubtitleTracks([])
    setSelectedSubtitleTrack(-1)
    desiredSubtitleTrackRef.current = null
    hlsRef.current = null

    // Prefer hls.js when it is supported so Chromium/Firefox can parse HLS
    // subtitle tracks. Safari/iOS fall back to the native HLS path below.
    // Note this split is NOT "webkit vs everyone else": hls.js requires
    // MediaSource, and Playwright's Linux WebKit build (what CI's
    // ubuntu-latest runners use) has it, so hls.js is the real branch
    // exercised there too -- only WebKit builds that genuinely lack
    // MediaSource (real Safari/iOS, Playwright's Windows/macOS WebKit
    // builds) take the native branch below. See the a11y spec's caption
    // test for how each is exercised in CI.
    let hls: Hls | null = null
    let cancelled = false
    let cleanupNativeHls: (() => void) | null = null
    let cleanupHlsStartup: (() => void) | null = null

    void import('hls.js').then((mod) => {
      if (cancelled) return
      const HlsCtor = mod.default
      if (!HlsCtor.isSupported()) {
        if (video.canPlayType('application/vnd.apple.mpegurl')) {
          video.src = manifestUrl
          const nativeTracks = video.textTracks
          const syncNativeSubtitleTracks = () => {
            const tracks = Array.from(nativeTracks)
            setSubtitleTracks(
              tracks.map((track, index) => ({
                id: index,
                label: track.label || track.language || `Track ${index + 1}`,
                language: track.language || '',
              })),
            )
            setSelectedSubtitleTrack(
              tracks.findIndex((track) => track.mode === 'showing'),
            )
          }
          const onError = () => {
            cancelSeekResumeRef.current()
            setState('error')
            setErrorMessage('The video could not be loaded. Please try again.')
          }
          syncNativeSubtitleTracks()
          video.addEventListener('loadedmetadata', syncNativeSubtitleTracks)
          video.addEventListener('error', onError)
          nativeTracks.addEventListener('addtrack', syncNativeSubtitleTracks)
          nativeTracks.addEventListener('removetrack', syncNativeSubtitleTracks)
          nativeTracks.addEventListener('change', syncNativeSubtitleTracks)
          cleanupNativeHls = () => {
            video.removeEventListener('loadedmetadata', syncNativeSubtitleTracks)
            video.removeEventListener('error', onError)
            nativeTracks.removeEventListener('addtrack', syncNativeSubtitleTracks)
            nativeTracks.removeEventListener('removetrack', syncNativeSubtitleTracks)
            nativeTracks.removeEventListener('change', syncNativeSubtitleTracks)
          }
          return
        }
        setState('error')
        setErrorMessage(
          'Your browser does not support HLS playback. Please try a recent version of Chrome, Firefox, Safari, or Edge.',
        )
        return
      }

      hls = new HlsCtor({
        enableWorker: true,
        lowLatencyMode: false,
        // Keep the usual three-segment live buffer, but recover when a ready
        // player falls behind the six-segment served window. hls.js defaults
        // its maximum live latency to Infinity, retaining stale buffered video.
        liveSyncDurationCount: 3,
        liveMaxLatencyDurationCount: 6,
      })
      hlsRef.current = hls
      // hls.js's SubtitleTrackController keeps its own trackId in sync with
      // the real browser's native <track> element modes: it polls
      // media.textTracks (WebKit lacks TextTrackList#onchange, so hls.js
      // falls back to sampling every 500ms instead of listening for a real
      // 'change' event) and, if it doesn't see any native track marked
      // "showing", forces hls.subtitleTrack back to -1. That polling can
      // -- and on a slow/loaded runner reliably does -- land in the window
      // between hls.js selecting the manifest's DEFAULT=YES subtitle track
      // (driven purely by playlist metadata, near-instant) and the actual
      // native <track> DOM element existing (only created once the
      // subtitle sub-playlist/segment has actually loaded over the
      // network, i.e. later and jitter-prone). hls.js treats the native
      // DOM as authoritative, so it "corrects" a still-loading selection
      // back to "off" -- a genuine hls.js bug (see e.g. video-dev/hls.js
      // issues #1948 and #4345), not a WebKit or CivicCast one, but one a
      // real Safari/iOS or slow-network viewer can hit exactly like CI
      // did (reproduced locally on Linux/WebKit, matching the
      // ubuntu-latest CI runner: see the caption test's history for the
      // reproduction). Fatal in this app because CivicCast owns the
      // caption UI (the English/Spanish/Off buttons below) and never
      // relies on a browser-native captions menu, so there is no
      // legitimate external source that should ever move the selection
      // out from under CivicCast's own choice.
      //
      // Fix: track what CivicCast's UI actually asked for
      // (desiredSubtitleTrackRef) independently of hls.js's internal
      // trackId, and treat it as authoritative -- seed it from the
      // manifest's own DEFAULT=YES flag (not from hls.subtitleTrack, which
      // can read back a stale -1 mid-selection) and re-push it back into
      // hls.js whenever a SUBTITLE_TRACK_SWITCH disagrees with it. Once
      // the native <track> element genuinely exists, that re-push
      // succeeds and durably converges (toggleTrackModes() finally has a
      // real element to mark "showing" on), so this is a real fix, not a
      // tighter poll: it makes the desired selection eventually consistent
      // regardless of how many spurious corrections hls.js's own polling
      // fires first.
      const syncSubtitleTracks = () => {
        const tracks = hls?.subtitleTracks ?? []
        if (tracks.length === 0) return
        setSubtitleTracks(
          tracks.map((track, index) => ({
            id: index,
            label: track.name || track.lang || `Track ${index + 1}`,
            language: track.lang || '',
          })),
        )
        if (desiredSubtitleTrackRef.current === null) {
          const defaultIndex = tracks.findIndex((track) => track.default)
          desiredSubtitleTrackRef.current = defaultIndex >= 0 ? defaultIndex : -1
        }
        applyDesiredSubtitleTrack()
      }
      const applyDesiredSubtitleTrack = () => {
        const desired = desiredSubtitleTrackRef.current ?? -1
        setSelectedSubtitleTrack(desired)
        if (hls && hls.subtitleTrack !== desired) {
          hls.subtitleTrack = desired
        }
      }
      // AAC/video start timestamps can leave a small initial gap. hls.js
      // can seek across it before WebKit's first native preroll, an ordering
      // observed in stalled startup traces. Let that first frame preroll before
      // applying the jump. Larger gaps and explicit nonzero starts still seek
      // immediately, and normal finite-stream EOS remains available to decoders.
      const nativeTime = Object.getOwnPropertyDescriptor(HTMLMediaElement.prototype, 'currentTime')
      if (nativeTime?.get && nativeTime.set &&
        !Object.hasOwn(video, 'currentTime')) {
        let deferredSeek: number | undefined
        let active = true
        cleanupHlsStartup = () => {
          if (!active) return
          active = false
          delete (video as { currentTime?: number }).currentTime
          video.removeEventListener('loadeddata', releaseStartupSeek)
          video.removeEventListener('seeking', cleanupHlsStartup!)
          if (restoreNativeSeekRef.current === cleanupHlsStartup) {
            restoreNativeSeekRef.current = () => {}
          }
        }
        const releaseStartupSeek = () => {
          if (video.readyState < 2) return
          cleanupHlsStartup?.()
          // Once native playback is ready, the recovery jump is obsolete. A
          // fresh seek can drop readiness before a queued play promise resolves.
          if (video.readyState === 2 && deferredSeek !== undefined && deferredSeek > video.currentTime) {
            video.currentTime = deferredSeek
          }
        }
        Object.defineProperty(video, 'currentTime', {
          configurable: true,
          get: () => nativeTime.get!.call(video),
          set: (target: number) => {
            const buffered = video.buffered
            if (video.currentTime === 0 && video.readyState < 2 && buffered.length &&
              buffered.start(0) > 0 && buffered.start(0) <= 0.05 &&
              target >= buffered.start(0) && target <= 0.1 && target <= buffered.end(0)) {
              deferredSeek = target
              return
            }
            // A deliberate seek takes precedence over any deferred startup jump.
            cleanupHlsStartup?.()
            nativeTime.set!.call(video, target)
          },
        })
        video.addEventListener('loadeddata', releaseStartupSeek)
        video.addEventListener('seeking', cleanupHlsStartup)
        restoreNativeSeekRef.current = cleanupHlsStartup
      }
      hls.attachMedia(video)
      hls.on(HlsCtor.Events.MEDIA_ATTACHED, () => {
        hls?.loadSource(manifestUrl)
      })
      hls.on(HlsCtor.Events.MANIFEST_PARSED, () => {
        syncSubtitleTracks()
        setState('ready')
        if (video.readyState >= 3) onCanPlay()
      })
      hls.on(HlsCtor.Events.SUBTITLE_TRACKS_UPDATED, () => {
        syncSubtitleTracks()
      })
      hls.on(HlsCtor.Events.SUBTITLE_TRACK_SWITCH, (_event, data) => {
        if (data.id === desiredSubtitleTrackRef.current) {
          setSelectedSubtitleTrack(data.id)
          return
        }
        // hls.js moved away from what CivicCast's UI asked for on its own
        // (see the long comment above) -- reassert instead of accepting
        // it, so both the button UI and the actual caption rendering
        // stay durably correct.
        applyDesiredSubtitleTrack()
      })
      hls.on(HlsCtor.Events.ERROR, (_event, data) => {
        if (data.fatal) {
          cancelSeekResumeRef.current()
          cleanupHlsStartup?.()
          setState('error')
          setErrorMessage(
            data.type === HlsCtor.ErrorTypes.NETWORK_ERROR
              ? 'Network error while loading the video. Check your connection and try again.'
              : 'The video could not be played. The stream may be unavailable.',
          )
        }
      })
    })

    return () => {
      cancelled = true
      cancelSeekResumeRef.current()
      // A live source can switch between the station origin and its CDN while
      // the resident is watching. Capture playback before tearing down the
      // old HLS attachment, which may itself pause the media element.
      resumeAfterSourceChangeRef.current =
        shouldResumeAfterSwitch || (!video.paused && !video.ended)
      video.removeEventListener('canplay', onCanPlay)
      cleanupNativeHls?.()
      cleanupHlsStartup?.()
      hls?.destroy()
      if (hlsRef.current === hls) hlsRef.current = null
    }
  }, [manifestUrl])

  const selectSubtitleTrack = (trackId: number) => {
    const hls = hlsRef.current
    if (hls) {
      desiredSubtitleTrackRef.current = trackId
      hls.subtitleTrack = trackId
      hls.subtitleDisplay = trackId >= 0
      setSelectedSubtitleTrack(trackId)
      return
    }

    const nativeTracks = videoRef.current?.textTracks
    if (!nativeTracks) return
    selectNativeTextTrack(nativeTracks, trackId)
    setSelectedSubtitleTrack(trackId)
  }

  return (
    <div className="overflow-hidden rounded-lg bg-black shadow-lg ring-1 ring-white/10">
      <div className="relative w-full">
        <div className="aspect-video w-full">
          <video
            ref={videoRef}
            controls
            playsInline
            poster={posterUrl}
            aria-label="Meeting video player"
            className="h-full w-full"
          />
        </div>

        {state === 'loading' && (
          <div
            role="status"
            aria-live="polite"
            className="absolute inset-0 flex items-center justify-center bg-black/40 text-civiccast-mist"
          >
            <span className="rounded-md bg-black/70 px-4 py-2 text-sm font-medium">
              Loading video&hellip;
            </span>
          </div>
        )}

        {state === 'error' && (
          <div
            role="alert"
            className="absolute inset-0 flex items-center justify-center bg-black/70 p-4 text-center"
          >
            <div className="max-w-md rounded-md border border-red-500/40 bg-red-950/40 p-4 text-sm text-red-100">
              {errorMessage}
            </div>
          </div>
        )}
        <div
          className="absolute bottom-3 left-3 max-w-[calc(100%-1.5rem)] rounded bg-black/75 px-3 py-1.5 text-xs text-white"
          role="status"
          aria-live="polite"
          aria-label="Program at playback position"
        >
          {playbackTitle ? `Now playing: ${playbackTitle}` : 'Program information follows the stream clock'}
        </div>
      </div>

      {subtitleTracks.length > 0 && (
        <fieldset className="border-t border-white/10 bg-[#0f1712] px-4 py-3">
          <legend className="sr-only">Caption track controls</legend>
          <div className="flex flex-wrap items-center gap-2 text-sm">
            <span className="mr-1 text-xs font-semibold uppercase tracking-[0.14em] text-stone-300">
              Captions
            </span>
            <CaptionButton
              label="Off"
              active={selectedSubtitleTrack < 0}
              onClick={() => selectSubtitleTrack(-1)}
            />
            {subtitleTracks.map((track) => (
              <CaptionButton
                key={`${track.language}-${track.id}`}
                label={track.label}
                active={selectedSubtitleTrack === track.id}
                onClick={() => selectSubtitleTrack(track.id)}
              />
            ))}
          </div>
        </fieldset>
      )}
    </div>
  )
}

function CaptionButton({
  label,
  active,
  onClick,
}: {
  label: string
  active: boolean
  onClick: () => void
}) {
  return (
    <button
      type="button"
      aria-pressed={active}
      onClick={onClick}
      className="rounded-md border px-3 py-1.5 text-sm font-medium focus:outline-none focus:ring-2 focus:ring-emerald-200"
      style={{
        background: active ? 'rgb(209 250 229)' : 'rgb(31 41 55)',
        borderColor: active ? 'rgb(167 243 208)' : 'rgb(75 85 99)',
        color: active ? 'rgb(6 78 59)' : 'rgb(243 244 246)',
      }}
    >
      {label}
    </button>
  )
}
