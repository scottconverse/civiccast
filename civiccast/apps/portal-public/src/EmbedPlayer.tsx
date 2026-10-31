import { useEffect, useState } from 'react'
import { HlsPlayer } from './HlsPlayer'
import { RecordingPlayer } from './RecordingPlayer'
import { fetchJson } from './api'

interface Playback { title: string; manifest_url: string | null; poster_url?: string | null }
export function EmbedPlayer({ kind, identity }: { kind: 'asset' | 'channel'; identity: string }) {
  const [playback, setPlayback] = useState<Playback | null>(null)
  const [error, setError] = useState<string | null>(null)
  useEffect(() => {
    let alive = true
    const endpoint = kind === 'channel' ? `/api/public/embed/channel/${encodeURIComponent(identity)}` : `/api/public/embed/${encodeURIComponent(identity)}`
    async function refresh() {
      try {
        const next = await fetchJson<Playback>(endpoint)
        if (alive) { setPlayback(next); setError(null) }
      } catch {
        if (alive) { setPlayback(null); setError('Playback is unavailable or restricted. Open the station portal for current access.') }
      }
    }
    void refresh()
    const timer = window.setInterval(() => { void refresh() }, 10000)
    return () => { alive = false; window.clearInterval(timer) }
  }, [kind, identity])
  return <main aria-label="Embedded station player" className="min-h-screen bg-stone-950 p-2 text-white">
    {playback?.manifest_url ? kind === 'asset' ? <RecordingPlayer key={identity} assetId={identity} manifestUrl={playback.manifest_url} posterUrl={playback.poster_url ?? undefined} /> : <HlsPlayer key={`${identity}:${playback.manifest_url}`} manifestUrl={playback.manifest_url} posterUrl={playback.poster_url ?? undefined} /> : <p role="status">{error ?? (playback ? 'This channel is standing by. Playback will appear when web output is available.' : 'Loading playback…')}</p>}
    <a className="text-sm underline" href={kind === 'asset' ? `/#/watch/${encodeURIComponent(identity)}` : '/'} target="_blank" rel="noreferrer">Open {playback?.title ?? 'the station portal'}</a>
  </main>
}
