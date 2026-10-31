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
    if (shouldResume) void video.current?.play().catch(() => setStatus('Press Play to continue.'))
  }

  async function speak(item: PauseDescription) {
    if (!video.current || !narration.current || active.current) return
    resume.current = !video.current.paused
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
    if (!media || media.paused || !pauseEnabled || active.current) return
    const item = description.pause_descriptions.find((entry) => !played.current.has(entry.id) && entry.time <= media.currentTime && media.currentTime <= entry.until)
    if (item) void speak(item)
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
    if (media.textTracks[0]) media.textTracks[0].mode = captions ? 'showing' : 'disabled'
    if (position.current.playing) void media.play().catch(() => setStatus('Press Play to continue.'))
  }

  return <section aria-label="Recording with audio descriptions" className="grid gap-3 rounded-lg bg-stone-950 p-3 text-white">
    <video ref={video} controls playsInline poster={posterUrl} aria-label="Meeting video player" className="w-full"
      src={described ? description.described_url : description.original_url}
      onLoadedMetadata={loaded} onTimeUpdate={checkDescriptions}
      onPlay={() => { if (active.current) video.current?.pause(); else checkDescriptions() }}
      onSeeking={() => {
        finish(false)
        for (const item of description.pause_descriptions) if (item.time >= (video.current?.currentTime ?? 0)) played.current.delete(item.id)
      }}
      onError={() => setStatus('The recording could not play. Retry or select the original audio.')}
    ><track kind="captions" src={description.captions_url} srcLang="en" label="English" /></video>
    <fieldset className="grid gap-3 border border-stone-500 p-3"><legend>Listening options</legend>
      <label><input type="checkbox" checked={described} onChange={(event) => switchSource(event.target.checked)} /> Audio descriptions in safe pauses</label>
      <label><input type="checkbox" checked={pauseEnabled} onChange={(event) => { setPauseEnabled(event.target.checked); if (!event.target.checked) finish(true) }} /> Pause the video to hear descriptions that do not fit between dialogue</label>
      <label><input type="checkbox" checked={captions} onChange={(event) => setCaptions(event.target.checked)} /> Show English captions</label>
    </fieldset>
    <p role="status" aria-live="polite">{status}</p>
    {speaking && <button type="button" className="min-h-11 rounded border p-2" onClick={() => { finish(true); setStatus('Description skipped.') }}>Skip description and resume video</button>}
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
    <audio ref={narration} preload="none" onEnded={() => { finish(true); setStatus('Description complete.') }} onError={() => { finish(false); setStatus('Description audio failed. The video remains paused.') }} />
  </section>
}
