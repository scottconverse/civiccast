import { useQuery } from '@tanstack/react-query'
import { ApiError, getCaptionProofs, getCaptionStatus } from '../api/client'
import type { CaptionStatusResponse, EgressCaptionProofSample } from '../types/api.generated'
import { freshReading } from './status-language'

function apiMessage(error: unknown, fallback: string): string {
  if (error instanceof ApiError) return error.detail ?? error.message
  if (error instanceof Error) return error.message
  return fallback
}

function fmtTime(iso: string): string {
  const d = new Date(iso)
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString()
}

function captureUnavailable(proof: EgressCaptionProofSample | undefined): boolean {
  return proof?.status === 'FAIL' && proof.decoder_name === 'not-run'
    && proof.blocker === 'EGRESS_CAPTION_DECODE_BACK_CAPTURE_UNAVAILABLE'
}

// Presentational only (no data fetching) so it unit-tests without a network.
export function CaptionStatusView({
  status,
  proofs,
  loading,
  error,
  liveCaptionsEnabled,
}: {
  status: CaptionStatusResponse | undefined
  proofs: EgressCaptionProofSample[] | undefined
  loading?: boolean
  error?: unknown
  // Station-profile live-captions switch; undefined until it loads. Off means
  // no caption is written into the picture, so "not verified" would read as a
  // failure waiting to clear when nothing is being checked at all.
  liveCaptionsEnabled?: boolean
}) {
  const latest = status?.latest ?? proofs?.[0]
  const unavailable = captureUnavailable(latest)
  const failed = latest?.status === 'FAIL' && !unavailable
  const stale = latest ? !freshReading(latest.sampled_at) : false
  const on = !error && !stale && latest?.status === 'PASS' && status?.caption_status === 'on'
  const switchedOff = !on && liveCaptionsEnabled === false
  return (
    <section
      aria-label="Captions"
      className="grid gap-2 rounded-md p-4"
      style={{ background: 'var(--cc-surface)', border: '1px solid var(--cc-line)' }}
    >
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 className="m-0 text-base font-semibold">Captions</h2>
        <span
          className="rounded-full px-2 py-0.5 text-[10px] font-semibold uppercase"
          style={{
            background: failed
              ? 'var(--cc-err-soft)'
              : on
                ? 'var(--cc-ok-soft)'
                : 'var(--cc-surface-3)',
            color: failed ? 'var(--cc-err)' : 'var(--cc-ink)',
          }}
        >
          {loading
            ? 'Checking…'
            : error
              ? 'Caption status unavailable'
            : stale
              ? 'Caption proof stale'
            : unavailable
              ? 'Caption proof unavailable'
            : failed
              ? 'Caption proof failed'
              : on
                ? 'Captions on'
                : switchedOff
                  ? 'Captions off'
                  : 'Not verified'}
        </span>
      </div>
      {latest && (
        <div
          className="grid gap-1 rounded-md p-2 text-xs"
          style={{ background: failed ? 'var(--cc-err-soft)' : 'var(--cc-surface-3)' }}
        >
          <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
            <strong>{unavailable ? 'Caption proof unavailable' : failed ? 'Caption proof failed' : `Latest proof: ${latest.status}`}</strong>
            {!unavailable && <span>{latest.matched_cue_count}/{latest.expected_cue_count} cues matched</span>}
            <span>Sampled {fmtTime(latest.sampled_at)}</span>
            {stale && <span className="font-semibold">Stale (older than 120 seconds or invalid sample time)</span>}
          </div>
          {latest.blocker && <div>Blocker: {latest.blocker}</div>}
          {unavailable && <div>No emitted-stream segment was available to capture. The caption decoder did not run; caption delivery remains unverified. Check the channel output and capture availability.</div>}
          {failed && <div>Check the caption source and the decode-back proof details below.</div>}
          <div>Checks refresh every 30 seconds; readings older than 2 minutes are stale.</div>
        </div>
      )}
      {switchedOff && (
        <p className="m-0 text-sm" style={{ color: 'var(--cc-ink-2)' }}>
          Live captions are switched off in the station profile (Setup &rarr; Station Profile), so
          nothing is written into the picture and there is nothing for the on-air check to
          confirm. Captions on published recordings are unaffected.
        </p>
      )}
      <p className="m-0 text-sm" style={{ color: 'var(--cc-ink-2)' }}>
        Captions show as <strong>on</strong> only after CivicCast decodes them back from the
        emitted stream and they match. This proves carriage at the egress boundary; it is not a
        claim of FCC Part&nbsp;79 compliance.
      </p>
      <p className="m-0 text-xs" style={{ color: 'var(--cc-ink-3)' }}>
        Speech generation, embedded-stream delivery and captions visible in the resident player are separate observations.
        An absent decode-back proof means delivery is unverified; it does not by itself prove speech recognition failed.
        Check the resident preview for captions matching the current program.
      </p>
      {Boolean(error) && (
        <div
          role="alert"
          className="rounded-md p-2 text-xs"
          style={{ background: 'var(--cc-err-soft)', color: 'var(--cc-err)' }}
        >
          {apiMessage(error, 'Caption status could not load.')}
        </div>
      )}
      <details>
        <summary className="cursor-pointer text-sm" style={{ color: 'var(--cc-ink-2)' }}>
          Decode-back proofs
        </summary>
        {proofs && proofs.length === 0 && (
          <p className="m-0 mt-2 text-sm" style={{ color: 'var(--cc-ink-3)' }}>
            No caption decode-back proofs yet for this channel.
          </p>
        )}
        {proofs && proofs.length > 0 && (
          <table className="mt-2 w-full border-collapse text-sm">
            <thead>
              <tr className="text-left text-xs uppercase" style={{ color: 'var(--cc-ink-3)' }}>
                <th scope="col" className="py-1 pr-3 font-medium">
                  When
                </th>
                <th scope="col" className="py-1 pr-3 font-medium">
                  Result
                </th>
                <th scope="col" className="py-1 pr-3 font-medium">
                  Matched
                </th>
                <th scope="col" className="py-1 font-medium">
                  Detail
                </th>
              </tr>
            </thead>
            <tbody>
              {proofs.map((proof, index) => (
                <tr
                  key={`${proof.sampled_at}-${index}`}
                  style={{ borderTop: '1px solid var(--cc-line)' }}
                >
                  <td className="py-1.5 pr-3 text-xs" style={{ color: 'var(--cc-ink-2)' }}>
                    {fmtTime(proof.sampled_at)}
                  </td>
                  <td className="py-1.5 pr-3">
                    <span
                      className="rounded-full px-2 py-0.5 text-[11px] font-semibold"
                      style={{
                        background:
                          proof.status === 'PASS' ? 'var(--cc-ok-soft)' : 'var(--cc-warn-soft)',
                        color: 'var(--cc-ink)',
                      }}
                    >
                      {captureUnavailable(proof) ? 'Unavailable' : proof.status}
                    </span>
                  </td>
                  <td className="cc-mono py-1.5 pr-3" style={{ color: 'var(--cc-ink)' }}>
                    {captureUnavailable(proof) ? 'Not checked' : `${proof.matched_cue_count}/${proof.expected_cue_count}`}
                  </td>
                  <td className="py-1.5 text-xs" style={{ color: 'var(--cc-ink-3)' }}>
                    {captureUnavailable(proof) ? 'Capture unavailable; decoder did not run' : proof.blocker ?? 'matched within tolerance'}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </details>
    </section>
  )
}

export function CaptionStatusCard({
  channelId,
  liveCaptionsEnabled,
}: {
  channelId: string
  liveCaptionsEnabled?: boolean
}) {
  const statusQuery = useQuery({
    queryKey: ['caption-status', channelId],
    queryFn: () => getCaptionStatus(channelId),
    retry: false,
    refetchInterval: 30_000,
  })
  const proofsQuery = useQuery({
    queryKey: ['caption-proofs', channelId],
    queryFn: () => getCaptionProofs(channelId, 10),
    retry: false,
    refetchInterval: 30_000,
  })
  return (
    <CaptionStatusView
      status={statusQuery.data}
      proofs={proofsQuery.data}
      loading={statusQuery.isLoading}
      error={statusQuery.error ?? proofsQuery.error}
      liveCaptionsEnabled={liveCaptionsEnabled}
    />
  )
}
