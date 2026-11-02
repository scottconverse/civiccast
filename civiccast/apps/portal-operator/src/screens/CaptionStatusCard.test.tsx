import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render } from '@testing-library/react'

import type { CaptionStatusResponse, EgressCaptionProofSample } from '../types/api.generated'
import { CaptionStatusView } from './CaptionStatusCard'

beforeEach(() => {
  vi.useFakeTimers()
  vi.setSystemTime(new Date('2026-01-01T12:00:00Z'))
})
afterEach(() => { cleanup(); vi.useRealTimers() })

function proof(overrides: Partial<EgressCaptionProofSample> = {}): EgressCaptionProofSample {
  return {
    channel_id: 'gov',
    sampled_at: '2026-01-01T12:00:00Z',
    status: 'PASS',
    caption_status: 'on',
    mode: 'cea-708',
    decoder_name: 'ffmpeg-subcc',
    expected_cue_count: 2,
    decoded_cue_count: 2,
    matched_cue_count: 2,
    max_timing_delta_seconds: 0.1,
    proof_boundary: 'egress-caption-embed-to-emitted-stream-decode-back',
    ...overrides,
  } as EgressCaptionProofSample
}

const ON: CaptionStatusResponse = { channel_id: 'gov', caption_status: 'on', latest: proof() }
const NOT_VERIFIED: CaptionStatusResponse = {
  channel_id: 'gov',
  caption_status: 'not-verified',
  latest: null,
}

describe('CaptionStatusView', () => {
  it('shows Captions on for a proven channel and states the honesty boundary', () => {
    const { container } = render(<CaptionStatusView status={ON} proofs={[proof()]} />)
    const text = container.textContent ?? ''
    expect(text).toContain('Captions on')
    expect(text).toContain('not a claim of FCC Part')
    expect(text).toContain('2/2')
    expect(text).toContain('PASS')
  })

  it('shows Not verified and an empty proof state', () => {
    const { container } = render(<CaptionStatusView status={NOT_VERIFIED} proofs={[]} />)
    expect(container.textContent).toContain('Not verified')
    expect(container.textContent).toContain('No caption decode-back proofs yet')
  })

  it('renders a FAIL proof with its blocker and unmatched count', () => {
    const failing = proof({
      status: 'FAIL',
      caption_status: 'not-verified',
      matched_cue_count: 0,
      blocker: 'EGRESS_CAPTION_DECODE_BACK_MISMATCH',
    })
    const { container } = render(<CaptionStatusView status={NOT_VERIFIED} proofs={[failing]} />)
    expect(container.textContent).toContain('FAIL')
    expect(container.textContent).toContain('EGRESS_CAPTION_DECODE_BACK_MISMATCH')
    expect(container.textContent).toContain('0/2')
    expect(container.textContent).toContain('Caption proof failed')
    expect(container.textContent).not.toMatch(/Not verified/)
  })

  it('distinguishes unavailable capture from a decoder that actually failed', () => {
    const unavailable = proof({
      status: 'FAIL',
      caption_status: 'not-verified',
      decoder_name: 'not-run',
      blocker: 'EGRESS_CAPTION_DECODE_BACK_CAPTURE_UNAVAILABLE',
      expected_cue_count: 0,
      matched_cue_count: 0,
    })
    const { container } = render(
      <CaptionStatusView status={{ ...NOT_VERIFIED, latest: unavailable }} proofs={[unavailable]} />,
    )
    const text = container.textContent ?? ''
    expect(text).toContain('Caption proof unavailable')
    expect(text).toContain('caption decoder did not run')
    expect(text).toContain('Sampled')
    expect(text).toContain('Not checked')
    expect(text).not.toContain('Caption proof failed')
    expect(text).not.toContain('Captions on')
    expect(text).not.toContain('0/0')
  })

  it('keeps an unavailable capture stale after its freshness window', () => {
    const unavailable = proof({
      status: 'FAIL',
      caption_status: 'not-verified',
      sampled_at: '2026-01-01T11:55:00Z',
      decoder_name: 'not-run',
      blocker: 'EGRESS_CAPTION_DECODE_BACK_CAPTURE_UNAVAILABLE',
    })
    const { container } = render(
      <CaptionStatusView status={{ ...NOT_VERIFIED, latest: unavailable }} proofs={[unavailable]} />,
    )
    expect(container.textContent).toContain('Caption proof stale')
    expect(container.textContent).toContain('Caption proof unavailable')
    expect(container.textContent).not.toContain('Caption proof failed')
    expect(container.textContent).not.toContain('Captions on')
  })

  it('marks a latest proof older than 120 seconds as stale', () => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date('2026-01-01T12:05:00Z'))
    try {
      const { container } = render(<CaptionStatusView status={ON} proofs={[proof()]} />)
      expect(container.textContent).toContain('Stale (older than 120 seconds or invalid sample time)')
      expect(container.textContent).not.toContain('Captions on')
      expect(container.textContent).toContain('Sampled')
    } finally {
      vi.useRealTimers()
    }
  })

  it('surfaces a load error', () => {
    const { getByRole } = render(
      <CaptionStatusView status={undefined} proofs={undefined} error={new Error('boom')} />,
    )
    expect(getByRole('alert').textContent).toContain('boom')
  })

  it.each(['invalid', '2026-01-01T12:05:00Z'])('never treats a %s proof time as current delivery', (sampled_at) => {
    const latest = proof({ sampled_at })
    const { container } = render(<CaptionStatusView status={{ ...ON, latest }} proofs={[latest]} />)
    expect(container.textContent).toContain('Caption proof stale')
    expect(container.textContent).not.toContain('Captions on')
  })

  it('does not retain a green caption claim after a refresh fails', () => {
    const { container } = render(<CaptionStatusView status={ON} proofs={[proof()]} error={new Error('Connection lost')} />)
    expect(container.textContent).toContain('Caption status unavailable')
    expect(container.textContent).not.toContain('Captions on')
    expect(container.textContent).toContain('Speech generation, embedded-stream delivery')
  })
})

describe('CaptionStatusView with live captions switched off', () => {
  it('says Captions off, not Not verified, and explains why', () => {
    // Round-2 review BLOCKER 1: with the station-profile switch off nothing is
    // written into the picture, so "Not verified" read as a failure waiting to
    // clear on every channel forever.
    const { container } = render(
      <CaptionStatusView status={NOT_VERIFIED} proofs={[]} liveCaptionsEnabled={false} />,
    )
    const text = container.textContent ?? ''
    expect(text).toContain('Captions off')
    expect(text).not.toContain('Not verified')
    expect(text).toContain('switched off in the station profile')
  })

  it('keeps the fail-closed Not verified wording while the switch is unknown or on', () => {
    const unknown = render(<CaptionStatusView status={NOT_VERIFIED} proofs={[]} />)
    expect(unknown.container.textContent).toContain('Not verified')
    unknown.unmount()
    const on = render(<CaptionStatusView status={NOT_VERIFIED} proofs={[]} liveCaptionsEnabled />)
    expect(on.container.textContent).toContain('Not verified')
  })

  it('a proven channel says Captions on regardless of the switch', () => {
    const { container } = render(<CaptionStatusView status={ON} proofs={[proof()]} liveCaptionsEnabled={false} />)
    expect(container.textContent).toContain('Captions on')
  })
})
