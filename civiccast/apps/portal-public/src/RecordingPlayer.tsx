// SPDX-License-Identifier: Apache-2.0
// Copyright (c) The CivicCast Authors
import { useEffect, useRef, useState, type ComponentProps } from 'react'
import { HlsPlayer } from './HlsPlayer'
import { fetchJson } from './api'

interface PauseDescription { id: number; time: number; until: number; text: string; audio: string }
interface Description {
  original_url: string; described_url: string; captions_url: string; descriptions_url: string
  pause_descriptions: PauseDescription[]
  coverage: { in_gap: number; requires_pause: number; uncertain_visuals: number }
  limitations: string[]; credits: string[]
}
type Props = ComponentProps<typeof HlsPlayer> & { assetId: string }

/** Existing HLS playback remains the fallback when no described derivative exists. */
export function RecordingPlayer(props: Props) { return <RecordingPlayerForAsset key={props.assetId} {...props} /> }

function RecordingPlayerForAsset(props: Props) {
  const [description, setDescription] = useState<Description | null>(null)
  const [useDescription, setUseDescription] = useState(false)
  const container = useRef<HTMLDivElement>(null)
  const [playback, setPlayback] = useState({ time: props.startSeconds ?? 0, playing: false, captions: false })
  useEffect(() => {
    let alive = true
    void fetchJson<Description>(`/api/public/assets/${encodeURIComponent(props.assetId)}/audio-description`)
      .then((data) => { if (alive) setDescription(data) })
      .catch(() => { if (alive) setDescription(null) })
    return () => { alive = false }
  }, [props.assetId])
  return <div ref={container} onLoadedMetadataCapture={(event) => {
    if (useDescription || !playback.playing || !(event.target instanceof HTMLVideoElement)) return
    void event.target.play().catch(() => {})
  }}>
    {description && <label className="my-3 block"><input type="checkbox" checked={useDescription} onChange={(event) => {
      const media = container.current?.querySelector('video')
      if (media) {
        setPlayback({ time: media.currentTime, playing: !media.paused, captions: Array.from(media.textTracks).some((track) => track.mode === 'showing') })
        media.pause()
      }
      setUseDescription(event.target.checked)
    }} /> Use audio-described playback</label>}
    {description && useDescription ? <DescribedRecording key={props.assetId} {...props} startSeconds={playback.time} initiallyPlaying={playback.playing} initiallyCaptioned={playback.captions} description={description} /> : <HlsPlayer {...props} startSeconds={playback.time} />}
  </div>
}

function DescribedRecording({ description, handleRef, startSeconds, posterUrl, initiallyPlaying, initiallyCaptioned }: Props & { description: Description; initiallyPlaying: boolean; initiallyCaptioned: boolean }) {
  const video = useRef<HTMLVideoElement>(null)
  const narration = useRef<HTMLAudioElement>(null)
  const played = useRef(new Set<number>())
  const cursor = useRef(startSeconds ?? 0)
  const pending = useRef<PauseDescription[]>([])
  const active = useRef<PauseDescription | null>(null)
  const resume = useRef(false)
  const position = useRef({ time: startSeconds ?? 0, playing: initiallyPlaying })
  const [described, setDescribed] = useState(true)
  const [pauseEnabled, setPauseEnabled] = useState(false)
  const [captions, setCaptions] = useState(initiallyCaptioned)
  const [status, setStatus] = useState('Audio-described playback selected.')
  const [speaking, setSpeaking] = useState(false)

  function finish(continueVideo: boolean) {
    narration.current?.pause()
    const shouldResume = continueVideo && resume.current
    active.current = null
    resume.current = false
    setSpeaking(false)
    if (continueVideo && pending.current.length) {
      const next = pending.current.shift()!
      void speak(next, shouldResume)
      return
    }
    pending.current = []
    if (shouldResume) void video.current?.play().catch(() => setStatus('Press Play to continue.'))
  }

  async function speak(item: PauseDescription, continueProgram?: boolean) {
    if (!video.current || !narration.current || active.current) return
    resume.current = continueProgram ?? !video.current.paused
    active.current = item
    played.current.add(item.id)
    video.current.pause()
    narration.current.src = item.audio
    setSpeaking(true)
    setStatus(`Description: ${item.text}`)
    try { await narration.current.play() }
    catch { finish(false); setStatus('Description could not play. Use its Listen button to retry.') }
  }

  function checkDescriptions() {
    const media = video.current
    if (!media || (media.paused && !media.ended) || !pauseEnabled || active.current) return
    // timeupdate is sparse: queue all cues crossed since the preceding tick,
    // rather than hoping one tick lands inside a 40ms window. Seeking resets
    // the cursor explicitly so a deliberate jump does not narrate old scenes.
    const now = media.currentTime
    const due = description.pause_descriptions.filter((entry) => !played.current.has(entry.id) && (
      (cursor.current < entry.time && entry.time <= now) || (entry.time <= now && now <= entry.until)
    )).sort((a, b) => a.time - b.time || a.id - b.id)
    cursor.current = now
    if (due.length) {
      pending.current = due.slice(1)
      void speak(due[0], !media.paused)
    }
  }

  useEffect(() => {
    if (!handleRef) return
    handleRef.current = { seekTo: (seconds) => {
      const media = video.current
      if (!media || !Number.isFinite(seconds)) return
      media.currentTime = Math.max(0, Math.min(seconds, Number.isFinite(media.duration) ? media.duration : seconds))
      void media.play().catch(() => setStatus('Press Play to continue.'))
    } }
    return () => { handleRef.current = null }
  }, [handleRef])

  useEffect(() => {
    const media = video.current
    if (media?.textTracks[0]) media.textTracks[0].mode = captions ? 'showing' : 'disabled'
  }, [captions])

  useEffect(() => {
    const audio = narration.current
    return () => { audio?.pause() }
  }, [])

  function switchSource(enabled: boolean) {
    const media = video.current
    if (!media) return
    position.current = { time: media.currentTime, playing: !media.paused || resume.current }
    finish(false)
    setDescribed(enabled)
    setStatus(enabled ? 'Audio descriptions in safe pauses selected.' : 'Original audio selected.')
  }

  function loaded() {
    const media = video.current
    if (!media) return
    media.currentTime = Math.max(0, Math.min(position.current.time, media.duration))
    cursor.current = media.currentTime
    if (media.textTracks[0]) media.textTracks[0].mode = captions ? 'showing' : 'disabled'
    if (position.current.playing) void media.play().catch(() => setStatus('Press Play to continue.'))
  }

  return <section aria-label="Recording with audio descriptions" className="grid gap-3 rounded-lg bg-stone-950 p-3 text-white">
    <video ref={video} controls playsInline poster={posterUrl} aria-label="Meeting video player" className="w-full"
      src={described ? description.described_url : description.original_url}
      onLoadedMetadata={loaded} onTimeUpdate={checkDescriptions} onEnded={checkDescriptions} onSeeked={checkDescriptions}
      onPlay={() => { if (active.current) video.current?.pause(); else checkDescriptions() }}
      onSeeking={() => {
        pending.current = []
        finish(false)
        cursor.current = video.current?.currentTime ?? 0
        for (const item of description.pause_descriptions) if (item.time >= (video.current?.currentTime ?? 0)) played.current.delete(item.id)
      }}
      onError={() => setStatus('The recording could not play. Retry or select the original audio.')}
    ><track kind="captions" src={description.captions_url} srcLang="en" label="English" /></video>
    <fieldset className="grid gap-3 border border-stone-500 p-3"><legend>Listening options</legend>
      <label><input type="checkbox" checked={described} onChange={(event) => switchSource(event.target.checked)} /> Audio descriptions in safe pauses</label>
      <label><input type="checkbox" checked={pauseEnabled} onChange={(event) => { cursor.current = video.current?.currentTime ?? 0; pending.current = []; setPauseEnabled(event.target.checked); if (!event.target.checked) finish(true) }} /> Pause the video to hear descriptions that do not fit between dialogue</label>
      <label><input type="checkbox" checked={captions} onChange={(event) => setCaptions(event.target.checked)} /> Show English captions</label>
    </fieldset>
    <p role="status" aria-live="polite">{status}</p>
    {speaking && <button type="button" className="min-h-11 rounded border p-2" onClick={() => { setStatus('Description skipped.'); finish(true) }}>Skip description and resume video</button>}
    <p>{description.coverage.in_gap} descriptions fit safe pauses; {description.coverage.requires_pause} require pausing; {description.coverage.uncertain_visuals} visual observations contain uncertainty.</p>
    <p>Pause descriptions play here only; the downloadable described video contains in-gap descriptions.</p>
    <a className="underline" href={description.described_url} download>Download described video</a>
    <details><summary>Description script and limitations</summary>
      {description.pause_descriptions.map((item) => <div key={item.id} className="my-3 border p-3">
        <p>At {item.time.toFixed(1)} seconds: {item.text}</p>
        <button type="button" className="min-h-11 rounded border p-2" disabled={speaking} onClick={() => void speak(item)}>Listen to this description</button>
      </div>)}
      <ul>{description.limitations.map((item) => <li key={item}>{item}</li>)}</ul>
      <p className="text-sm">{description.credits.join(' ')}</p>
    </details>
    <audio ref={narration} preload="none" onEnded={() => { setStatus('Description complete.'); finish(true) }} onError={() => { finish(false); setStatus('Description audio failed. The video remains paused.') }} />
  </section>
}
