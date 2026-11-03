import { useEffect, useMemo, useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import type Hls from 'hls.js'
import {
  ApiError,
  getStaffAssetPlaybackGrant,
  getStaffAsset,
  updateStaffAsset,
} from '../api/client'
import { useFocusTrap } from '../hooks/useFocusTrap'
import { useToast } from '../components/toast-context'
import type { AssetRow, Chapter } from '../types/asset'

interface Props {
  assetId: string
  onClose: () => void
}

const STEP_FRAME = 1 / 29.97
const STEP_SECOND = 1
const TRIM_PRECISION = 3

function clamp(n: number, lo: number, hi: number): number {
  return Math.max(lo, Math.min(hi, n))
}

function roundTrim(seconds: number): number {
  return Number(seconds.toFixed(TRIM_PRECISION))
}

function fmtTC(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds < 0) return '00:00:00.00'
  const total = Math.max(0, seconds)
  const h = Math.floor(total / 3600)
  const m = Math.floor((total % 3600) / 60)
  const s = Math.floor(total % 60)
  const cs = Math.floor((total % 1) * 100)
  return `${String(h).padStart(2, '0')}:${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}.${String(cs).padStart(2, '0')}`
}

function fmtTCShort(seconds: number): string {
  return fmtTC(seconds).slice(0, 8)
}

function LoadingState() {
  return (
    <div
      className="flex h-full items-center justify-center"
      style={{ background: 'var(--cc-paper)' }}
    >
      <div className="text-sm" style={{ color: 'var(--cc-ink-2)' }}>
        Loading asset…
      </div>
    </div>
  )
}

function ErrorState({ error, onClose }: { error: Error; onClose: () => void }) {
  const isApi = error instanceof ApiError
  return (
    <div className="flex h-full flex-col items-center justify-center gap-3 p-6">
      <div
        role="alert"
        className="max-w-md rounded-md p-4 text-sm"
        style={{ background: 'var(--cc-err-soft)', color: 'var(--cc-err)' }}
      >
        <div className="font-semibold">Could not load asset.</div>
        <div className="mt-1 text-xs" style={{ color: 'var(--cc-ink-2)' }}>
          {isApi && error.detail ? error.detail : error.message}
        </div>
      </div>
      <button
        type="button"
        onClick={onClose}
        className="rounded-md px-3 py-1.5 text-xs font-medium"
        style={{ border: '1px solid var(--cc-line)', color: 'var(--cc-ink-2)' }}
      >
        Back to assets
      </button>
    </div>
  )
}

interface EditorProps {
  asset: AssetRow
  onClose: () => void
  onSaved: () => void
}

function TrimEditor({ asset, onClose, onSaved }: EditorProps) {
  const dur = Math.max(1, asset.duration_seconds ?? 0)
  const [pos, setPos] = useState<number>(asset.trim_in_seconds ?? 0)
  const [inPt, setInPt] = useState<number>(asset.trim_in_seconds ?? 0)
  const [outPt, setOutPt] = useState<number>(
    asset.trim_out_seconds && asset.trim_out_seconds <= dur
      ? asset.trim_out_seconds
      : dur,
  )
  const [chapters, setChapters] = useState<Chapter[]>(asset.chapters ?? [])
  const [renamingIdx, setRenamingIdx] = useState<number | null>(null)
  const [saveError, setSaveError] = useState<string | null>(null)
  const [previewError, setPreviewError] = useState<string | null>(null)
  const [previewState, setPreviewState] = useState<'loading' | 'ready' | 'unavailable'>(
    'loading',
  )
  const [previewAttempt, setPreviewAttempt] = useState(0)
  const [previewDuration, setPreviewDuration] = useState<number | null>(null)
  const trackRef = useRef<HTMLDivElement>(null)
  const dialogRef = useRef<HTMLDivElement>(null)
  const videoRef = useRef<HTMLVideoElement>(null)
  const hlsRef = useRef<Hls | null>(null)
  const queryClient = useQueryClient()
  useFocusTrap(dialogRef)

  const previewQuery = useQuery({
    queryKey: ['staff-asset-playback-grant', asset.asset_id],
    queryFn: () => getStaffAssetPlaybackGrant(asset.asset_id),
    enabled: Boolean(asset.file_path),
    retry: false,
  })
  const previewUrl = previewQuery.data?.packaged_url ?? null
  const sourceOffset = previewQuery.data?.source_offset_seconds ?? 0

  const dirty = useMemo(() => {
    const sameIn = (asset.trim_in_seconds ?? 0) === roundTrim(inPt)
    const sameOut =
      (asset.trim_out_seconds ?? dur) === roundTrim(outPt)
    const sameChapters =
      JSON.stringify(asset.chapters ?? []) === JSON.stringify(chapters)
    return !(sameIn && sameOut && sameChapters)
  }, [asset, inPt, outPt, chapters, dur])

  const toast = useToast()

  const saveMutation = useMutation<AssetRow, Error, void>({
    mutationFn: () =>
      updateStaffAsset(asset.asset_id, {
        // QA-008 (audit-team v0.3.0): echo the version we last observed
        // so the store rejects with 409 if another writer landed first.
        expected_version: asset.version,
        trim_in_seconds: roundTrim(inPt),
        trim_out_seconds: roundTrim(outPt),
        chapters: chapters.map((c) => ({
          t: Number(c.t.toFixed(2)),
          name: c.name,
          sub: c.sub ?? null,
        })),
      }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['staff-assets'] })
      void queryClient.invalidateQueries({
        queryKey: ['staff-asset', asset.asset_id],
      })
      toast.push({
        tone: 'success',
        message: 'Saved.',
        detail: `Saved trim and chapter metadata. Live recordings are re-packaged when the finalization worker detects changed trims; other assets use their normal packaging flow. · ${asset.title}`,
      })
      onSaved()
    },
    onError: (err) => {
      const detail = err instanceof ApiError ? err.detail : undefined
      setSaveError(detail ?? err.message)
    },
  })

  const setIn = () => setInPt(roundTrim(clamp(pos, 0, outPt - STEP_FRAME)))
  const setOut = () => setOutPt(roundTrim(clamp(pos, inPt + STEP_FRAME, dur)))

  const addChapter = () => {
    const t = Math.round(pos * 100) / 100
    if (chapters.some((c) => Math.abs(c.t - t) < 0.05)) return
    const next: Chapter[] = [
      ...chapters,
      { t, name: 'New chapter', sub: null },
    ].sort((a, b) => a.t - b.t)
    setChapters(next)
    setRenamingIdx(next.findIndex((c) => Math.abs(c.t - t) < 0.05))
  }

  const removeChapter = (idx: number) => {
    setChapters(chapters.filter((_, i) => i !== idx))
    if (renamingIdx === idx) setRenamingIdx(null)
  }

  const renameChapter = (idx: number, name: string) => {
    if (!name.trim()) return
    const next = chapters.slice()
    next[idx] = { ...next[idx], name: name.trim().slice(0, 200) }
    setChapters(next)
  }

  // Keyboard shortcuts. Live state is read via the latest-value ref pattern
  // so the listener registered once at mount stays in sync with React state
  // without a re-subscribe per state change. ``onClose`` follows the same
  // pattern so the Escape handler always invokes the latest prop without
  // re-subscribing on every parent re-render.
  const stateRef = useRef({ pos, dur, inPt, outPt, chapters, sourceOffset })
  const onCloseRef = useRef(onClose)
  useEffect(() => {
    stateRef.current = { pos, dur, inPt, outPt, chapters, sourceOffset }
    onCloseRef.current = onClose
  })

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      // Escape closes the dialog regardless of focus target. UX-005
      // (audit-team v0.3.0): the schedule drawer has this binding; the
      // trim editor was missing it. Operators reach for Escape on any
      // fullscreen dialog.
      if (e.key === 'Escape') {
        e.preventDefault()
        onCloseRef.current()
        return
      }
      const tag = (e.target as HTMLElement | null)?.tagName
      if (tag === 'INPUT' || tag === 'TEXTAREA') return
      const s = stateRef.current
      if (e.key === ' ') {
        // A focused native <video controls> owns Space; avoid toggling it twice.
        if (tag === 'VIDEO') return
        e.preventDefault()
        const video = videoRef.current
        if (video) {
          if (video.paused) {
            const minimum = Math.max(s.inPt, s.sourceOffset)
            const sourceTime = video.currentTime + s.sourceOffset
            if (sourceTime < minimum || sourceTime >= s.outPt) {
              video.currentTime = Math.max(0, minimum - s.sourceOffset)
              setPos(minimum)
            }
            void video.play().catch(() => {})
          } else {
            video.pause()
          }
        }
        return
      }
      if (e.key === 'ArrowLeft') {
        e.preventDefault()
        setPos((p) =>
          roundTrim(clamp(p - (e.shiftKey ? STEP_SECOND : STEP_FRAME), 0, s.dur)),
        )
      }
      if (e.key === 'ArrowRight') {
        e.preventDefault()
        setPos((p) =>
          roundTrim(clamp(p + (e.shiftKey ? STEP_SECOND : STEP_FRAME), 0, s.dur)),
        )
      }
      if (e.key.toLowerCase() === 'i') {
        setInPt(roundTrim(clamp(s.pos, 0, s.outPt - STEP_FRAME)))
      }
      if (e.key.toLowerCase() === 'o') {
        setOutPt(roundTrim(clamp(s.pos, s.inPt + STEP_FRAME, s.dur)))
      }
      if (e.key.toLowerCase() === 'm') {
        const t = Math.round(s.pos * 100) / 100
        if (s.chapters.some((c) => Math.abs(c.t - t) < 0.05)) return
        const next: Chapter[] = [
          ...s.chapters,
          { t, name: 'New chapter', sub: null },
        ].sort((a, b) => a.t - b.t)
        setChapters(next)
        setRenamingIdx(next.findIndex((c) => Math.abs(c.t - t) < 0.05))
      }
      if (e.key === 'Home') setPos(0)
      if (e.key === 'End') setPos(roundTrim(s.dur))
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [])

  useEffect(() => {
    const video = videoRef.current
    if (!video || !previewUrl) {
      setPreviewState(previewUrl ? 'loading' : 'unavailable')
      return
    }
    let cancelled = false
    let hls: Hls | null = null
    setPreviewState('loading')
    setPreviewDuration(null)
    setPreviewError(null)

    const onLoadedMetadata = () => {
      const mediaEnd = Number.isFinite(video.duration) ? video.duration : 0
      const latest = stateRef.current
      video.currentTime = Math.max(
        0,
        clamp(
          latest.pos,
          latest.sourceOffset,
          latest.sourceOffset + mediaEnd,
        ) - latest.sourceOffset,
      )
      setPreviewDuration(Number.isFinite(video.duration) ? video.duration : null)
      setPreviewState('ready')
    }
    const onNativeError = () => {
      setPreviewState('unavailable')
      setPreviewError('The packaged preview could not be loaded. Repackage the asset or retry.')
    }
    video.addEventListener('loadedmetadata', onLoadedMetadata)
    video.addEventListener('error', onNativeError)

    void import('hls.js')
      .then(({ default: HlsConstructor }) => {
        if (cancelled) return
        if (HlsConstructor.isSupported()) {
          hls = new HlsConstructor({ enableWorker: true, lowLatencyMode: false })
          hlsRef.current = hls
          hls.on(HlsConstructor.Events.MANIFEST_PARSED, () => {
            setPreviewState('ready')
            if (video.readyState >= 1) onLoadedMetadata()
          })
          hls.on(HlsConstructor.Events.ERROR, (_event, data) => {
            if (!data.fatal) return
            setPreviewState('unavailable')
            setPreviewError('The packaged preview could not be played. Repackage the asset or retry.')
          })
          hls.attachMedia(video)
          hls.loadSource(previewUrl)
        } else if (video.canPlayType('application/vnd.apple.mpegurl')) {
          video.src = previewUrl
        } else {
          setPreviewState('unavailable')
          setPreviewError('This browser does not support HLS video playback.')
        }
      })
      .catch(() => {
        if (cancelled) return
        setPreviewState('unavailable')
        setPreviewError('The HLS playback component could not be loaded. Refresh the page and retry.')
      })

    return () => {
      cancelled = true
      video.removeEventListener('loadedmetadata', onLoadedMetadata)
      video.removeEventListener('error', onNativeError)
      hls?.destroy()
      if (hlsRef.current === hls) hlsRef.current = null
      if (video.src === new URL(previewUrl, window.location.href).href) {
        video.removeAttribute('src')
        video.load()
      }
    }
  }, [previewUrl, sourceOffset, previewAttempt])

  useEffect(() => {
    const video = videoRef.current
    if (!video || video.readyState < 1) return
    const mediaEnd = Number.isFinite(video.duration) ? video.duration : outPt
    const target = Math.max(
      0,
      clamp(pos, sourceOffset, sourceOffset + mediaEnd) - sourceOffset,
    )
    if (Math.abs(video.currentTime - target) > 0.15) video.currentTime = target
  }, [pos, outPt, sourceOffset, previewUrl, previewState])

  const handlePreviewTime = () => {
    const video = videoRef.current
    if (!video) return
    const sourceTime = video.currentTime + sourceOffset
    if (!video.paused && sourceTime >= outPt) {
      video.pause()
      video.currentTime = Math.max(0, outPt - sourceOffset)
      setPos(roundTrim(outPt))
      return
    }
    const mediaEnd = Number.isFinite(video.duration) ? video.duration : dur
    setPos(roundTrim(clamp(sourceTime, sourceOffset, sourceOffset + mediaEnd)))
  }

  const packageEnd =
    previewDuration !== null && Number.isFinite(previewDuration)
      ? sourceOffset + previewDuration
      : null
  const selectedRangePackaged =
    previewUrl !== null &&
    packageEnd !== null &&
    inPt >= sourceOffset - 0.05 &&
    outPt <= packageEnd + 0.05

  const pct = (t: number) => `${(clamp(t, 0, dur) / dur) * 100}%`

  // Pointer-driven seek + handle drag — works on mouse and touch.
  const seekFromClient = (clientX: number): number => {
    const r = trackRef.current?.getBoundingClientRect()
    if (!r) return pos
    const f = clamp((clientX - r.left) / r.width, 0, 1)
    return f * dur
  }

  const startDrag = (kind: 'in' | 'out' | 'play') => (
    e: React.PointerEvent,
  ) => {
    e.preventDefault()
    ;(e.target as Element).setPointerCapture?.(e.pointerId)
    const move = (ev: PointerEvent) => {
      const v = seekFromClient(ev.clientX)
      if (kind === 'in') setInPt(roundTrim(clamp(v, 0, outPt - STEP_FRAME)))
      else if (kind === 'out') setOutPt(roundTrim(clamp(v, inPt + STEP_FRAME, dur)))
      else setPos(roundTrim(v))
    }
    const up = () => {
      window.removeEventListener('pointermove', move)
      window.removeEventListener('pointerup', up)
      window.removeEventListener('pointercancel', up)
    }
    window.addEventListener('pointermove', move)
    window.addEventListener('pointerup', up)
    window.addEventListener('pointercancel', up)
  }

  const selectedDur = Math.max(0, outPt - inPt)
  const submitDisabled = saveMutation.isPending || !dirty

  return (
    <div
      ref={dialogRef}
      className="fixed inset-0 z-50 flex flex-col"
      role="dialog"
      aria-modal="true"
      aria-labelledby="trim-editor-heading"
      style={{ background: 'var(--cc-paper)' }}
    >
      <header
        className="flex flex-wrap items-center gap-3 px-4 py-3 sm:px-6"
        style={{
          background: 'var(--cc-surface)',
          borderBottom: '1px solid var(--cc-line)',
        }}
      >
        <button
          type="button"
          onClick={onClose}
          aria-label="Close trim editor"
          className="rounded-md px-2 py-1 text-sm"
          style={{
            border: '1px solid var(--cc-line)',
            color: 'var(--cc-ink-2)',
          }}
        >
          ←
        </button>
        <div className="min-w-0 flex-1">
          <div
            className="text-[10px] font-semibold uppercase tracking-wider"
            style={{ color: 'var(--cc-ink-3)' }}
          >
            Trim &amp; chapters · non-destructive
          </div>
          <h1
            id="trim-editor-heading"
            className="cc-truncate m-0 text-base font-semibold tracking-tight"
          >
            {asset.title}
          </h1>
          <div
            className="cc-mono text-[11px]"
            style={{ color: 'var(--cc-ink-3)' }}
          >
            {asset.asset_id}
          </div>
        </div>
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={onClose}
            className="rounded-md px-3 py-1.5 text-xs font-medium"
            style={{
              border: '1px solid var(--cc-line)',
              color: 'var(--cc-ink-2)',
            }}
          >
            Discard
          </button>
          <button
            type="button"
            onClick={() => {
              setSaveError(null)
              saveMutation.mutate()
            }}
            disabled={submitDisabled}
            className="rounded-md px-3 py-1.5 text-xs font-semibold"
            style={{
              background: submitDisabled
                ? 'var(--cc-surface-3)'
                : 'var(--cc-brand)',
              color: submitDisabled
                ? 'var(--cc-ink-3)'
                : 'var(--cc-brand-ink)',
              cursor: submitDisabled ? 'not-allowed' : 'pointer',
            }}
          >
            {saveMutation.isPending ? 'Saving…' : 'Save trim & chapters'}
          </button>
        </div>
      </header>

      <div className="flex-1 overflow-y-auto">
        <div className="grid gap-4 p-4 sm:p-6 lg:grid-cols-[1fr_320px]">
          <section
            aria-label="Preview"
            className="flex flex-col gap-3"
          >
            {previewUrl && !previewError ? (
              <video
                ref={videoRef}
                controls
                preload="metadata"
                aria-label={`Packaged preview for ${asset.title}`}
                onLoadedMetadata={handlePreviewTime}
                onTimeUpdate={handlePreviewTime}
                onPlay={() => {
                  const video = videoRef.current
                  if (!video) return
                  const mediaEnd = Number.isFinite(video.duration) ? video.duration : 0
                  if (inPt < sourceOffset - 0.05 || outPt > sourceOffset + mediaEnd + 0.05) {
                    video.pause()
                    return
                  }
                  const sourceTime = video.currentTime + sourceOffset
                  if (sourceTime < inPt || sourceTime >= outPt) {
                    video.currentTime = Math.max(0, inPt - sourceOffset)
                    setPos(roundTrim(inPt))
                  }
                }}
                onError={() =>
                  setPreviewError(
                    'The packaged preview could not be loaded. Repackage the asset or retry.',
                  )
                }
                className="aspect-video w-full rounded-md"
                style={{ background: 'var(--cc-ink)' }}
              />
            ) : (
              <div
                className="flex aspect-video items-center justify-center rounded-md p-4 text-center"
                style={{ background: 'var(--cc-ink)', color: 'var(--cc-ink-inv)' }}
              >
                <div>
                  <div className="cc-mono text-[10px] uppercase tracking-wider" style={{ opacity: 0.6 }}>
                    Packaged preview
                  </div>
                  <div className="mt-2 text-sm font-semibold">
                    {previewQuery.isLoading || (previewUrl && previewState === 'loading')
                      ? 'Preparing packaged preview…'
                      : previewQuery.isError || previewError
                        ? 'Preview is unavailable'
                        : previewQuery.data && !previewUrl
                          ? 'This asset needs an HLS package before its trim can be previewed.'
                          : 'This asset has no source file to preview.'}
                  </div>
                  {(previewQuery.isError || previewError) && (
                    <>
                      <div className="mt-1 text-xs" style={{ opacity: 0.8 }}>
                        {previewError ??
                          'Source preview needs an active staff account with source-view access. Sign in again or ask a station administrator.'}
                      </div>
                      <button
                        type="button"
                        onClick={() => {
                          void previewQuery.refetch().then((result) => {
                            setPreviewError(
                              result.isError
                                ? 'The secure preview could not be refreshed. Sign in again or ask a station administrator.'
                                : null,
                            )
                            if (!result.isError && result.data?.packaged_url) {
                              setPreviewAttempt((attempt) => attempt + 1)
                            }
                          })
                        }}
                        className="mt-3 rounded-md px-3 py-1.5 text-xs font-medium"
                        style={{ background: 'var(--cc-surface)', color: 'var(--cc-ink)' }}
                      >
                        Retry preview
                      </button>
                    </>
                  )}
                  <div className="cc-mono cc-tabular mt-3 text-xl font-semibold">
                    {fmtTCShort(pos)} / {fmtTCShort(dur)}
                  </div>
                </div>
              </div>
            )}

            {previewUrl && packageEnd !== null && !selectedRangePackaged && (
              <p role="status" className="text-xs" style={{ color: 'var(--cc-warn-text)' }}>
                This package contains source time {fmtTCShort(sourceOffset)}–
                {fmtTCShort(packageEnd)}. The selected range extends beyond it; save the trim,
                wait for packaging to finish, then reopen the editor to preview the full range.
              </p>
            )}

            <div
              className="flex items-center gap-2 rounded-md p-3 text-[11px]"
              style={{
                background: 'var(--cc-surface-2)',
                color: 'var(--cc-ink-2)',
              }}
            >
              <strong style={{ color: 'var(--cc-ink)' }}>Non-destructive.</strong>
              The original file is never modified. Preview plays only the selected range;
              saving trim and chapter metadata does not alter the source. The live
              finalization worker re-packages changed trims; other assets use
              their normal packaging flow.
            </div>
          </section>

          <aside
            aria-label="Chapters"
            className="flex flex-col gap-2 rounded-md p-3"
            style={{
              background: 'var(--cc-surface)',
              border: '1px solid var(--cc-line)',
            }}
          >
            <div className="flex items-center justify-between">
              <div>
                <div
                  className="text-[10px] font-semibold uppercase tracking-wider"
                  style={{ color: 'var(--cc-ink-3)' }}
                >
                  Chapters
                </div>
                <div className="text-sm font-medium">
                  {chapters.length} marker{chapters.length === 1 ? '' : 's'}
                </div>
              </div>
              <button
                type="button"
                onClick={addChapter}
                className="rounded-md px-2.5 py-1.5 text-[11px] font-medium"
                style={{
                  border: '1px solid var(--cc-line)',
                  color: 'var(--cc-ink-2)',
                }}
                title="Add chapter at playhead (M)"
              >
                + Add at playhead
              </button>
            </div>
            {chapters.length === 0 ? (
              <div
                className="rounded-md p-3 text-[11px]"
                style={{
                  background: 'var(--cc-surface-2)',
                  color: 'var(--cc-ink-3)',
                }}
              >
                No chapters yet. Move the playhead and press <kbd>M</kbd> or
                tap "Add at playhead."
              </div>
            ) : (
              <ul className="flex flex-col gap-1" aria-label="Chapter list">
                {chapters.map((c, idx) => (
                  <li
                    key={`${c.t}-${idx}`}
                    className="flex items-start gap-2 rounded-md p-2"
                    style={{
                      background: 'var(--cc-surface-2)',
                      border: '1px solid var(--cc-line)',
                    }}
                  >
                    <button
                      type="button"
                      onClick={() => setPos(c.t)}
                      className="cc-mono cc-tabular shrink-0 rounded-md px-2 py-1 text-[11px]"
                      style={{
                        background: 'var(--cc-surface-3)',
                        color: 'var(--cc-ink-2)',
                      }}
                      aria-label={`Seek to ${fmtTCShort(c.t)}`}
                    >
                      {fmtTCShort(c.t)}
                    </button>
                    <div className="min-w-0 flex-1">
                      {renamingIdx === idx ? (
                        <input
                          aria-label="Chapter name"
                          autoFocus
                          defaultValue={c.name}
                          onBlur={(e) => {
                            renameChapter(idx, e.currentTarget.value)
                            setRenamingIdx(null)
                          }}
                          onKeyDown={(e) => {
                            if (e.key === 'Enter') {
                              renameChapter(idx, e.currentTarget.value)
                              setRenamingIdx(null)
                            }
                            if (e.key === 'Escape') setRenamingIdx(null)
                          }}
                          className="w-full rounded-md px-2 py-1 text-sm"
                          style={{
                            background: 'var(--cc-surface)',
                            border: '1px solid var(--cc-line)',
                            color: 'var(--cc-ink)',
                          }}
                        />
                      ) : (
                        <button
                          type="button"
                          onClick={() => setRenamingIdx(idx)}
                          className="block w-full text-left text-sm font-medium"
                          style={{
                            color: 'var(--cc-ink)',
                            background: 'transparent',
                            border: 0,
                          }}
                        >
                          {c.name}
                        </button>
                      )}
                    </div>
                    <button
                      type="button"
                      onClick={() => removeChapter(idx)}
                      aria-label={`Remove chapter ${c.name}`}
                      className="shrink-0 rounded-md px-2 py-1 text-xs"
                      style={{
                        color: 'var(--cc-ink-3)',
                        background: 'transparent',
                        border: '1px solid transparent',
                      }}
                    >
                      ✕
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </aside>
        </div>

        <section
          aria-label="Timeline"
          className="px-4 pb-4 sm:px-6"
        >
          <div
            className="flex flex-wrap items-center gap-3 rounded-md p-3"
            style={{
              background: 'var(--cc-surface)',
              border: '1px solid var(--cc-line)',
            }}
          >
            <div
              className="flex items-baseline gap-1 text-[11px]"
              style={{ color: 'var(--cc-ink-3)' }}
            >
              In <strong className="cc-mono cc-tabular text-sm" style={{ color: 'var(--cc-ink)' }}>{fmtTCShort(inPt)}</strong>
            </div>
            <div
              className="flex items-baseline gap-1 text-[11px]"
              style={{ color: 'var(--cc-ink-3)' }}
            >
              Out <strong className="cc-mono cc-tabular text-sm" style={{ color: 'var(--cc-ink)' }}>{fmtTCShort(outPt)}</strong>
            </div>
            <div
              className="flex items-baseline gap-1 text-[11px]"
              style={{ color: 'var(--cc-ink-3)' }}
            >
              Selected <strong className="cc-mono cc-tabular text-sm" style={{ color: 'var(--cc-ink)' }}>{fmtTCShort(selectedDur)}</strong>
            </div>
            <div
              className="ml-auto cc-mono text-[10px]"
              style={{ color: 'var(--cc-ink-3)' }}
            >
              Shortcuts: I/O · ←/→ · Shift+arrow · M · Home/End
            </div>
          </div>

          <div
            ref={trackRef}
            onPointerDown={(e) => {
              startDrag('play')(e)
              const v = seekFromClient(e.clientX)
              setPos(roundTrim(v))
            }}
            className="relative mt-3 w-full touch-none select-none rounded-md"
            style={{
              height: 64,
              background: 'var(--cc-surface-3)',
              border: '1px solid var(--cc-line)',
              cursor: 'pointer',
            }}
          >
            <div
              className="absolute"
              style={{
                top: 0,
                bottom: 0,
                left: pct(inPt),
                width: `calc(${pct(outPt - inPt)})`,
                background: 'var(--cc-brand-soft)',
                opacity: 0.7,
              }}
            />
            {chapters.map((c, idx) => (
              <div
                key={`marker-${idx}`}
                title={c.name}
                className="absolute"
                style={{
                  top: 4,
                  bottom: 4,
                  left: pct(c.t),
                  width: 2,
                  background: 'var(--cc-warn)',
                  pointerEvents: 'none',
                }}
              />
            ))}
            <div
              role="slider"
              aria-label="In point"
              aria-valuemin={0}
              aria-valuemax={roundTrim(outPt)}
              aria-valuenow={roundTrim(inPt)}
              aria-valuetext={fmtTCShort(inPt)}
              tabIndex={0}
              onPointerDown={(e) => {
                e.stopPropagation()
                startDrag('in')(e)
              }}
              onKeyDown={(e) => {
                if (e.key === 'ArrowLeft' || e.key === 'ArrowRight') {
                  e.preventDefault()
                  const delta = e.key === 'ArrowLeft' ? -STEP_FRAME : STEP_FRAME
                  setInPt((v) => roundTrim(clamp(v + delta, 0, outPt - STEP_FRAME)))
                }
              }}
              className="absolute -top-1 -bottom-1 flex items-center justify-center rounded-md text-[10px] font-bold"
              style={{
                left: pct(inPt),
                width: 44,
                marginLeft: -22,
                background: 'var(--cc-brand)',
                color: 'var(--cc-brand-ink)',
                cursor: 'ew-resize',
                touchAction: 'none',
              }}
            >
              IN
            </div>
            <div
              role="slider"
              aria-label="Out point"
              aria-valuemin={roundTrim(inPt)}
              aria-valuemax={roundTrim(dur)}
              aria-valuenow={roundTrim(outPt)}
              aria-valuetext={fmtTCShort(outPt)}
              tabIndex={0}
              onPointerDown={(e) => {
                e.stopPropagation()
                startDrag('out')(e)
              }}
              onKeyDown={(e) => {
                if (e.key === 'ArrowLeft' || e.key === 'ArrowRight') {
                  e.preventDefault()
                  const delta = e.key === 'ArrowLeft' ? -STEP_FRAME : STEP_FRAME
                  setOutPt((v) => roundTrim(clamp(v + delta, inPt + STEP_FRAME, dur)))
                }
              }}
              className="absolute -top-1 -bottom-1 flex items-center justify-center rounded-md text-[10px] font-bold"
              style={{
                left: pct(outPt),
                width: 44,
                marginLeft: -22,
                background: 'var(--cc-brand)',
                color: 'var(--cc-brand-ink)',
                cursor: 'ew-resize',
                touchAction: 'none',
              }}
            >
              OUT
            </div>
            <div
              aria-hidden="true"
              className="absolute pointer-events-none"
              style={{
                top: 0,
                bottom: 0,
                left: pct(pos),
                width: 2,
                background: 'var(--cc-err)',
                marginLeft: -1,
              }}
            />
          </div>

          <div className="mt-3 flex flex-wrap items-center gap-2">
            <button
              type="button"
              onClick={() => setPos(0)}
              className="rounded-md px-3 py-2 text-xs"
              style={{
                border: '1px solid var(--cc-line)',
                color: 'var(--cc-ink-2)',
                minHeight: 44,
                minWidth: 44,
              }}
              aria-label="Go to start (Home)"
            >
              ⏮
            </button>
            <button
              type="button"
              onClick={() =>
                setPos((p) => roundTrim(clamp(p - STEP_SECOND, 0, dur)))
              }
              className="rounded-md px-3 py-2 text-xs"
              style={{
                border: '1px solid var(--cc-line)',
                color: 'var(--cc-ink-2)',
                minHeight: 44,
                minWidth: 44,
              }}
              aria-label="Step back one second (Shift+←)"
            >
              −1s
            </button>
            <button
              type="button"
              onClick={() =>
                setPos((p) => roundTrim(clamp(p - STEP_FRAME, 0, dur)))
              }
              className="rounded-md px-3 py-2 text-xs"
              style={{
                border: '1px solid var(--cc-line)',
                color: 'var(--cc-ink-2)',
                minHeight: 44,
                minWidth: 44,
              }}
              aria-label="Step back one frame (Left arrow)"
            >
              −1f
            </button>
            <span
              className="cc-mono cc-tabular px-3 text-sm font-semibold"
              style={{ color: 'var(--cc-ink)' }}
              aria-label={`Preview time ${fmtTC(pos)}`}
            >
              {fmtTC(pos)}
            </span>
            <button
              type="button"
              onClick={() =>
                setPos((p) => roundTrim(clamp(p + STEP_FRAME, 0, dur)))
              }
              className="rounded-md px-3 py-2 text-xs"
              style={{
                border: '1px solid var(--cc-line)',
                color: 'var(--cc-ink-2)',
                minHeight: 44,
                minWidth: 44,
              }}
              aria-label="Step forward one frame (Right arrow)"
            >
              +1f
            </button>
            <button
              type="button"
              onClick={() =>
                setPos((p) => roundTrim(clamp(p + STEP_SECOND, 0, dur)))
              }
              className="rounded-md px-3 py-2 text-xs"
              style={{
                border: '1px solid var(--cc-line)',
                color: 'var(--cc-ink-2)',
                minHeight: 44,
                minWidth: 44,
              }}
              aria-label="Step forward one second (Shift+→)"
            >
              +1s
            </button>
            <button
              type="button"
              onClick={() => setPos(roundTrim(dur))}
              className="rounded-md px-3 py-2 text-xs"
              style={{
                border: '1px solid var(--cc-line)',
                color: 'var(--cc-ink-2)',
                minHeight: 44,
                minWidth: 44,
              }}
              aria-label="Go to end (End)"
            >
              ⏭
            </button>
            <div className="ml-auto flex items-center gap-2">
              <button
                type="button"
                onClick={setIn}
                className="rounded-md px-3 py-2 text-xs font-medium"
                style={{
                  background: 'var(--cc-brand-soft)',
                  color: 'var(--cc-brand-2)',
                  minHeight: 44,
                  minWidth: 64,
                }}
              >
                Set IN
              </button>
              <button
                type="button"
                onClick={setOut}
                className="rounded-md px-3 py-2 text-xs font-medium"
                style={{
                  background: 'var(--cc-brand-soft)',
                  color: 'var(--cc-brand-2)',
                  minHeight: 44,
                  minWidth: 64,
                }}
              >
                Set OUT
              </button>
              <button
                type="button"
                onClick={addChapter}
                className="rounded-md px-3 py-2 text-xs font-medium"
                style={{
                  background: 'var(--cc-warn-soft)',
                  color: 'var(--cc-ink)',
                  border: '1px solid var(--cc-line)',
                  minHeight: 44,
                  minWidth: 64,
                }}
              >
                + Mark
              </button>
            </div>
          </div>

          {saveError && (
            <div
              role="alert"
              className="mt-3 rounded-md p-3 text-xs"
              style={{
                background: 'var(--cc-err-soft)',
                color: 'var(--cc-err)',
              }}
            >
              <strong>Save failed.</strong>{' '}
              <span style={{ color: 'var(--cc-ink-2)' }}>{saveError}</span>
            </div>
          )}
        </section>
      </div>
    </div>
  )
}

export function TrimEditorScreen({ assetId, onClose }: Props) {
  const query = useQuery<AssetRow, Error>({
    queryKey: ['staff-asset', assetId],
    queryFn: () => getStaffAsset(assetId),
    retry: false,
  })

  if (query.isLoading) return <LoadingState />
  if (query.isError) return <ErrorState error={query.error} onClose={onClose} />
  if (!query.data) return <ErrorState error={new Error('No data')} onClose={onClose} />
  return <TrimEditor asset={query.data} onClose={onClose} onSaved={onClose} />
}
