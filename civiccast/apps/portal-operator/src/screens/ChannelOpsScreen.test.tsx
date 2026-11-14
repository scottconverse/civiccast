// SPDX-License-Identifier: Apache-2.0
// Copyright (c) The CivicCast Authors
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'

// The screen-level Start watchdog tests below mount ChannelOpsScreen itself,
// which imports every one of these. The panel-level tests never call them.
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
  applyHeadendProfile: vi.fn(),
  runComplianceProbe: vi.fn(),
  getAppPlatformConfig: vi.fn(),
  getChannelNowNext: vi.fn(),
  getChannelPlayoutPlan: vi.fn(),
  getChannelProofLog: vi.fn(),
  getCtvFeed: vi.fn(),
  getEgressConfig: vi.fn(),
  getEgressHealth: vi.fn(),
  getCaptionStatus: vi.fn().mockResolvedValue({ caption_status: "not-verified", latest: null }),
  getEgressState: vi.fn(),
  getGraphicsOverlay: vi.fn(),
  listEgressChannels: vi.fn(),
  getStaffIdentity: vi.fn(),
  getStationProfile: vi.fn(),
  listChannelProfiles: vi.fn(),
  listHeadendProfiles: vi.fn(),
  queueEgressCommand: vi.fn(),
  updateAppPlatformChannelBranding: vi.fn(),
  updateAppPlatformConfig: vi.fn(),
  updateEgressConfig: vi.fn(),
  updateGraphicsOverlay: vi.fn(),
}))
// Sibling cards own their own API surface and their own tests; keep them out
// of the screen-level watchdog tests.
vi.mock('./CableVerificationCard', () => ({ CableVerificationCard: () => null }))
vi.mock('./LoudnessPlanCard', () => ({ LoudnessPlanCard: () => null }))
vi.mock('./CaptionStatusCard', () => ({ CaptionStatusCard: () => null }))
vi.mock('./AudioTracksCard', () => ({ AudioTracksCard: () => null }))
vi.mock('./CommitToAirPanel', () => ({ CommitToAirPanel: () => null }))
vi.mock('./TakeoverCard', () => ({ TakeoverCard: () => null }))

import type {
  ChannelNowNext,
  ChannelPlayoutPlan,
  ChannelProfile,
  ChannelProofLog,
  EgressConfig,
  EgressStateRow,
  GraphicsOverlayStateResponse,
  HeadendProfile,
  HeadendProfileApplyResponse,
  StaffIdentityResponse,
} from '../types/api.generated'
import {
  ApiError,
  type EgressHealthSample,
  getAppPlatformConfig,
  getChannelNowNext,
  getChannelPlayoutPlan,
  getChannelProofLog,
  getCtvFeed,
  getEgressConfig,
  getEgressHealth,
  getCaptionStatus,
  getEgressState,
  getGraphicsOverlay,
  getStaffIdentity,
  getStationProfile,
  listChannelProfiles,
  listEgressChannels,
  listHeadendProfiles,
  queueEgressCommand,
  updateEgressConfig,
} from '../api/client'
import {
  START_APPLY_TIMEOUT_MS,
  configCheckFailedReason,
  startDisabledConfigReason,
  startWatchApplied,
  startWithoutConfigReason,
} from './egress-start'
import {
  ChannelOpsScreen,
  ChannelConfidence,
  EgressControlPanel,
  GraphicsOverlayPanel,
  HeadendDeliveryPanel,
  OutputsPanel,
  PlayoutPanel,
  PlayoutPlanPanel,
  ProofPanel,
} from './ChannelOpsScreen'

type ChannelOutput = NonNullable<ChannelProfile['outputs']>[number]

afterEach(cleanup)

const CHANNEL = {
  channel_id: 'public',
  slug: 'public',
  kind: 'public',
  branding: { display_name: 'Public Channel' } as ChannelProfile['branding'],
  fallback_behavior: 'slate',
} as ChannelProfile

describe('PlayoutPlanPanel', () => {
  it('renders a block status through the shared vocabulary, not the raw enum', () => {
    // GauntletGate F1 wired this screen's StatusPill (line 93) through
    // stateLabel(), but no test here exercised that call site, so a revert
    // back to a bare `{label}` would have gone unnoticed. 'fallback' is a
    // real PlayoutBlock['status'] value; stateLabel sentence-cases it to
    // 'Fallback' -- if the wiring is reverted, the DOM shows the raw
    // lowercase enum word instead.
    const plan: ChannelPlayoutPlan = {
      generated_at: '2026-06-15T12:00:00Z',
      channel: CHANNEL,
      source: 'schedule-store',
      blocks: [
        {
          block_id: 'blk-1',
          channel_id: 'public',
          kind: 'live',
          title: 'Council meeting',
          starts_at: '2026-06-15T12:00:00Z',
          duration_seconds: 600,
          source_ref: 'src-1',
          status: 'fallback',
        },
      ],
      gap_blocks: [],
      export_formats: [],
      proof_boundary: 'schedule-to-playout contract',
      not_claimed: [],
    }
    const { getByText, queryByText } = render(<PlayoutPlanPanel plan={plan} />)
    expect(getByText('Fallback')).toBeTruthy()
    expect(queryByText('fallback')).toBeNull()
  })

  it('shows the loading placeholder before a plan has arrived', () => {
    const { container } = render(<PlayoutPlanPanel plan={undefined} />)
    expect(container.textContent).toContain('Loading schedule-to-playout plan...')
  })
})

function egressState(state: EgressStateRow['state']): EgressStateRow {
  return { channel_id: 'public', state, updated_at: '2026-05-31T18:00:00Z' }
}

function renderEgressPill(state: EgressStateRow['state']) {
  return render(
    <EgressControlPanel
      channelId="public"
      state={egressState(state)}
      health={[]}
      pendingCommand={null}
      canControl={false}
      error={null}
      onCommand={() => {}}
    />,
  )
}

describe('EgressControlPanel egress-state pill tone', () => {
  it('renders a not-on-air feed as attention-worthy amber, matching System Health', () => {
    // The verify caught this: STOPPED rendered blue (info) here while System
    // Health rendered it amber (warn) -- same feed, two colours. Both now route
    // through toneForEgressState, so this pill must be warn-soft, not info-soft.
    const { getByText } = renderEgressPill('STOPPED')
    const pill = getByText('Stopped')
    expect(pill.style.background).toBe('var(--cc-warn-soft)')
    expect(pill.style.background).not.toBe('var(--cc-info-soft)')
  })

  it('renders ON_AIR as ok and ERROR as err', () => {
    expect(renderEgressPill('ON_AIR').getByText('On air').style.background).toBe('var(--cc-ok-soft)')
    cleanup()
    expect(renderEgressPill('ERROR').getByText('Needs attention').style.background).toBe('var(--cc-err-soft)')
  })
})

describe('EgressControlPanel sink health wording', () => {
  const config = (kind: 'udp-ts' | 'srt'): EgressConfig => ({
    channel_id: 'public',
    enabled: true,
    slate_message: 'Stand by.',
    sinks: [{ kind, label: 'Cable headend', uri: kind === 'udp-ts' ? 'udp://239.0.0.1:5000' : 'srt://example' }],
  })

  const health = (state: EgressHealthSample['state'], connected: boolean): EgressHealthSample => ({
    channel_id: 'public',
    sampled_at: new Date().toISOString(),
    state,
    sink_connected: { 'Cable headend': connected },
    encoder_fps: 30,
    encoder_bitrate_kbps: 2000,
    dropped_frames: 0,
    seconds_on_air: 30,
  })

  function renderSink(kind: 'udp-ts' | 'srt', state: EgressStateRow['state'], connected = true) {
    return render(
      <EgressControlPanel
        channelId="public"
        state={{ ...egressState(state), pid: state === 'ON_AIR' ? 4321 : null }}
        health={[health(state, connected)]}
        config={config(kind)}
        pendingCommand={null}
        canControl={false}
        error={null}
        onCommand={() => {}}
      />,
    )
  }

  it('qualifies an active UDP sink as local send with receiver not verified', () => {
    expect(renderSink('udp-ts', 'ON_AIR').container.textContent).toContain(
      'Cable headend: local send: active (receiver not verified)',
    )
  })

  it('does not treat an old true UDP sample as connected after the channel stops', () => {
    expect(renderSink('udp-ts', 'STOPPED').container.textContent).toContain(
      'Cable headend: local send: not verified (receiver not verified)',
    )
  })

  it('preserves connected wording for transports with connection semantics', () => {
    expect(renderSink('srt', 'ON_AIR').container.textContent).toContain('Cable headend: connected')
  })
})

function graphicsOverlayState(overrides: Partial<GraphicsOverlayStateResponse> = {}): GraphicsOverlayStateResponse {
  return {
    channel_id: 'public',
    graphics_overlay_enabled: false,
    graphics_overlay_lower_third_text: '',
    ...overrides,
  }
}

describe('GraphicsOverlayPanel', () => {
  // WP-11 item 2 (audit UX-007): pin the help copy so it can't regress back
  // to "station bug graphics overlay" (a different broadcast graphic from
  // the lower-third this control actually edits) and stays explicit that
  // the change lands on the channel's next pipeline build or a scheduled
  // swap, never as a hot-change to an already-live pipeline.
  it('explains next-build/scheduled-swap timing and never says "station bug"', () => {
    const { getByText, queryByText } = render(
      <GraphicsOverlayPanel
        channelId="public"
        state={graphicsOverlayState()}
        loadError={null}
        saving={false}
        canEdit
        saveError={null}
        onSave={() => {}}
      />,
    )
    expect(
      getByText(
        'Changes this channel’s lower-third banner on the next pipeline build or scheduled swap. It does not hot-change an already-live pipeline.',
      ),
    ).toBeTruthy()
    expect(queryByText(/station bug/i)).toBeNull()
  })

  it('reflects the returned/persisted enabled state in the toggle and badge', () => {
    const { getByText, getByDisplayValue, queryByText } = render(
      <GraphicsOverlayPanel
        channelId="public"
        state={graphicsOverlayState({ graphics_overlay_enabled: true, graphics_overlay_lower_third_text: 'Town Council -- Live' })}
        loadError={null}
        saving={false}
        canEdit
        saveError={null}
        onSave={() => {}}
      />,
    )
    expect(getByText('On air')).toBeTruthy()
    expect(getByText('Take off air')).toBeTruthy()
    expect(queryByText('Put on air')).toBeNull()
    expect(getByDisplayValue('Town Council -- Live')).toBeTruthy()
  })

  it('shows Off air and Put on air when the state is disabled', () => {
    const { getByText } = render(
      <GraphicsOverlayPanel
        channelId="public"
        state={graphicsOverlayState()}
        loadError={null}
        saving={false}
        canEdit
        saveError={null}
        onSave={() => {}}
      />,
    )
    expect(getByText('Off air')).toBeTruthy()
    expect(getByText('Put on air')).toBeTruthy()
  })

  it('requires a two-step confirm before calling the endpoint to change on-air state', () => {
    const onSave = vi.fn()
    const { getByText, getByPlaceholderText, queryByText } = render(
      <GraphicsOverlayPanel
        channelId="public"
        state={graphicsOverlayState()}
        loadError={null}
        saving={false}
        canEdit
        saveError={null}
        onSave={onSave}
      />,
    )
    fireEvent.change(getByPlaceholderText('e.g. Town Council -- Live'), {
      target: { value: 'Breaking: Council votes tonight' },
    })
    fireEvent.click(getByText('Put on air'))
    // First click only reveals the confirm row -- the endpoint is not called yet.
    expect(onSave).not.toHaveBeenCalled()
    expect(getByText('Confirm: put on air')).toBeTruthy()

    fireEvent.click(getByText('Confirm: put on air'))
    expect(onSave).toHaveBeenCalledWith({
      graphics_overlay_enabled: true,
      graphics_overlay_lower_third_text: 'Breaking: Council votes tonight',
    })
    expect(queryByText('Confirm: put on air')).toBeNull()
  })

  it('cancelling the confirm row does not call the endpoint', () => {
    const onSave = vi.fn()
    const { getByText, getByPlaceholderText, queryByText } = render(
      <GraphicsOverlayPanel
        channelId="public"
        state={graphicsOverlayState({ graphics_overlay_enabled: true, graphics_overlay_lower_third_text: 'x' })}
        loadError={null}
        saving={false}
        canEdit
        saveError={null}
        onSave={onSave}
      />,
    )
    fireEvent.click(getByText('Take off air'))
    expect(getByText('Confirm: take off air')).toBeTruthy()
    fireEvent.click(getByText('Cancel'))
    expect(queryByText('Confirm: take off air')).toBeNull()
    expect(onSave).not.toHaveBeenCalled()
    // Cancelling stays on air -- placeholder input untouched, still exists.
    expect(getByPlaceholderText('e.g. Town Council -- Live')).toBeTruthy()
  })

  it('disables Put on air until the operator enters banner text', () => {
    const { getByText } = render(
      <GraphicsOverlayPanel
        channelId="public"
        state={graphicsOverlayState()}
        loadError={null}
        saving={false}
        canEdit
        saveError={null}
        onSave={() => {}}
      />,
    )
    const button = getByText('Put on air') as HTMLButtonElement
    expect(button.disabled).toBe(true)
    expect(getByText('Enter banner text before putting it on air.')).toBeTruthy()
  })

  it('disables editing when the operator lacks the role', () => {
    const { getByText } = render(
      <GraphicsOverlayPanel
        channelId="public"
        state={graphicsOverlayState()}
        loadError={null}
        saving={false}
        canEdit={false}
        saveError={null}
        onSave={() => {}}
      />,
    )
    expect(getByText(/requires the meeting operator or setup admin role/)).toBeTruthy()
    const button = getByText('Put on air') as HTMLButtonElement
    expect(button.disabled).toBe(true)
  })
})

describe('EgressControlPanel captions row', () => {
  const sample: EgressHealthSample = {
    channel_id: 'public',
    sampled_at: new Date().toISOString(),
    state: 'ON_AIR',
    sink_connected: { head: true },
            dropped_frames: 0,
            seconds_on_air: 30,
    caption_status: 'not-verified',
  }

  function renderCaptions(liveCaptionsEnabled: boolean | undefined) {
    return render(
      <EgressControlPanel
        channelId="public"
        state={egressState('ON_AIR')}
        health={[sample]}
        pendingCommand={null}
        canControl={false}
        error={null}
        onCommand={() => {}}
        liveCaptionsEnabled={liveCaptionsEnabled}
      />,
    )
  }

  it('says the switch is off instead of waiting for a check that cannot pass', () => {
    const { container } = renderCaptions(false)
    expect(container.textContent).toContain('Off (switched off in the station profile)')
    expect(container.textContent).not.toContain('Not yet confirmed')
  })

  it('keeps the fail-closed wording while the switch is on or unknown', () => {
    expect(renderCaptions(true).container.textContent).toContain('Not verified; open channel caption proof')
    cleanup()
    expect(renderCaptions(undefined).container.textContent).toContain('Not verified; open channel caption proof')
  })
})

describe('OutputsPanel', () => {
  // beta.5 clean-machine walkthrough: the Channels screen printed
  // /api/public/channels/public/live.m3u8 as a working URL while it answered
  // 404. The backend now marks an hls output `enabled: false` when the channel
  // has no hls sink; this card must say so instead of printing the link.
  const notEnabled: ChannelOutput = {
    kind: 'hls',
    label: 'Resident and CTV HLS',
    target: '/api/public/channels/public/live.m3u8',
    proof_boundary: 'hls-output-not-enabled',
    next_step:
      "HLS web output is not enabled for this channel. Apply the 'Local rehearsal (web preview, HLS)' preset under Cable headend delivery, or add an hls sink to the channel's egress config, and this URL starts serving.",
    enabled: false,
  }
  const enabled: ChannelOutput = {
    kind: 'hls',
    label: 'Resident and CTV HLS',
    target: '/media/live/public/playlist.m3u8',
    proof_boundary: 'hls-sink-configured',
    next_step: 'Serves the channel\'s hls egress sink while the channel is on air.',
    enabled: true,
  }

  it('says web output is not enabled instead of printing a dead URL', () => {
    const { getByText, queryByText } = render(<OutputsPanel outputs={[notEnabled]} />)
    expect(getByText('HLS web output is not enabled for this channel.')).toBeTruthy()
    expect(getByText('Not enabled')).toBeTruthy()
    expect(queryByText('/api/public/channels/public/live.m3u8')).toBeNull()
    // The fix is still spelled out.
    expect(getByText(/Local rehearsal \(web preview, HLS\)/)).toBeTruthy()
  })

  it('prints the real manifest URL when the hls sink is configured', () => {
    const { getByText, queryByText } = render(<OutputsPanel outputs={[enabled]} />)
    expect(getByText('/media/live/public/playlist.m3u8')).toBeTruthy()
    expect(queryByText('Not enabled')).toBeNull()
    expect(queryByText('HLS web output is not enabled for this channel.')).toBeNull()
  })

  it('treats a missing enabled flag as enabled (older API payloads)', () => {
    const legacy = { ...enabled } as Partial<ChannelOutput>
    delete legacy.enabled
    const { getByText, queryByText } = render(<OutputsPanel outputs={[legacy as ChannelOutput]} />)
    expect(getByText('/media/live/public/playlist.m3u8')).toBeTruthy()
    expect(queryByText('Not enabled')).toBeNull()
  })
})

describe('HeadendDeliveryPanel', () => {
  // Round-2 delta review, BLOCKER 1: a saved preset says nothing about the
  // air. The apply response now carries `on_air_effect` + `on_air_detail`
  // (restarted from the slate / restart required for a program on air / next
  // start) and the card must show it, or the operator is left believing the
  // web preview is live when the running pipeline never picked it up.
  const localHls: HeadendProfile = {
    profile_id: 'local-rehearsal-hls',
    label: 'Local rehearsal (web preview, HLS)',
    vendor: 'CivicCast',
    source_urls: ['https://example.invalid/rfc8216'],
    canonical_profile: {} as HeadendProfile['canonical_profile'],
    muxrate_kbps: 0,
    transport: 'local-hls',
  }
  const result = (
    effect: HeadendProfileApplyResponse['on_air_effect'],
    detail: string,
  ): HeadendProfileApplyResponse => ({
    config: {
      channel_id: 'public',
      enabled: true,
      slate_message: 'x',
      sinks: [{ kind: 'hls', label: 'Web preview (HLS)', uri: 'C:\\CivicCast\\egress\\live-hls\\public' }],
    } as HeadendProfileApplyResponse['config'],
    on_air_effect: effect,
    on_air_detail: detail,
  })
  const renderPanel = (applyResult: HeadendProfileApplyResponse | undefined) =>
    render(
      <HeadendDeliveryPanel
        channelId="public"
        profiles={[localHls]}
        config={applyResult?.config}
        applying={false}
        canEdit
        applyError={null}
        applyResult={applyResult}
        onApply={() => {}}
        verifying={false}
        verifyResult={undefined}
        verifyError={null}
        onVerify={() => {}}
      />,
    )

  it('shows nothing about the air before an apply', () => {
    const { queryByRole } = renderPanel(undefined)
    expect(queryByRole('status')).toBeNull()
  })

  it('tells the operator a program on air needs a restart, in the API\'s own words', () => {
    const detail =
      'The channel is on air. A running pipeline does not pick up output changes, so this preset takes effect when the channel is next started. To put it on air now, Stop and then Start the channel.'
    const { getByRole, getByText } = renderPanel(result('restart_required', detail))
    expect(getByRole('status').textContent).toContain('Restart the channel to put it on air')
    expect(getByText(detail)).toBeTruthy()
  })

  it('says the slate channel was restarted and is going on air', () => {
    const detail =
      'The channel was standing by on its slate, so it is being restarted with the new output. It is back on air within a few seconds.'
    const { getByRole, getByText } = renderPanel(result('restart_queued', detail))
    expect(getByRole('status').textContent).toContain('going on air')
    expect(getByText(detail)).toBeTruthy()
  })

  it('says a dark channel picks the preset up at its next start', () => {
    const detail =
      'The channel is not running. The preset takes effect when the channel is next started.'
    const { getByRole } = renderPanel(result('next_start', detail))
    expect(getByRole('status').textContent).toContain('next start')
    expect(getByRole('status').textContent).toContain(detail)
  })
})

// beta.5 walkthrough F-27: Channels used to render sample-contract rows
// ("Public live programming -- Playing", "Captions: Attached") on a channel
// whose feed was Stopped. The API now reports nulls/empties until something
// real exists, and these panels must say so in plain words.
describe('PlayoutPanel honest empty states (F-27)', () => {
  it('says "No program on air" and "Nothing scheduled" when both slots are null', () => {
    const nowNext: ChannelNowNext = {
      generated_at: '2026-06-15T12:00:00Z',
      channel: CHANNEL,
      current: null,
      next: null,
      fallback_active: false,
      proof_boundary: 'egress-state-and-schedule-store',
    }
    const { getByText, queryByText } = render(<PlayoutPanel nowNext={nowNext} />)
    expect(getByText('No program on air')).toBeTruthy()
    expect(getByText('Nothing scheduled')).toBeTruthy()
    expect(queryByText(/live programming/)).toBeNull()
    expect(queryByText('Playing')).toBeNull()
  })

  it('renders a real on-air block in the Now slot and still says Nothing scheduled for Next', () => {
    const nowNext: ChannelNowNext = {
      generated_at: '2026-06-15T12:00:00Z',
      channel: CHANNEL,
      current: {
        block_id: 'public-egress-on_air',
        channel_id: 'public',
        kind: 'live',
        title: 'Council chamber camera',
        starts_at: '2026-06-15T11:55:00Z',
        duration_seconds: 300,
        source_ref: 'Council chamber camera',
        status: 'playing',
      },
      next: null,
      fallback_active: false,
      proof_boundary: 'egress-state-and-schedule-store',
    }
    const { getByText, queryByText } = render(<PlayoutPanel nowNext={nowNext} />)
    expect(getByText('Council chamber camera')).toBeTruthy()
    expect(getByText('Playing')).toBeTruthy()
    expect(queryByText('No program on air')).toBeNull()
    expect(getByText('Nothing scheduled')).toBeTruthy()
  })
})

describe('PlayoutPlanPanel honest empty state (F-27)', () => {
  it('explains the repeat selection, gaps and late channel starts', () => {
    const plan: ChannelPlayoutPlan = {
      generated_at: '2026-06-15T12:00:00Z', channel: CHANNEL, source: 'schedule-store',
      blocks: [], gap_blocks: [], repeat_schedule: true, repeat_block_ids: [],
      export_formats: ['json'], proof_boundary: 'software-schedule-to-playout-plan', not_claimed: [],
    }
    const { getByText } = render(<PlayoutPlanPanel plan={plan} />)
    expect(getByText(/last published group with no gap greater than one second/)).toBeTruthy()
    expect(getByText(/joins the current scheduled program at its elapsed position/)).toBeTruthy()
    expect(getByText(/repeat does not pull later programs forward/)).toBeTruthy()
  })

  it('says "Nothing scheduled" for an empty schedule-store plan', () => {
    const plan: ChannelPlayoutPlan = {
      generated_at: '2026-06-15T12:00:00Z',
      channel: CHANNEL,
      source: 'schedule-store',
      blocks: [],
      gap_blocks: [],
      export_formats: ['json'],
      proof_boundary: 'software-schedule-to-playout-plan',
      not_claimed: [],
    }
    const { getByText, queryByText } = render(<PlayoutPlanPanel plan={plan} />)
    expect(getByText('Nothing scheduled')).toBeTruthy()
    expect(queryByText(/channel slate/)).toBeNull()
    expect(queryByText('Loading schedule-to-playout plan...')).toBeNull()
  })
})

describe('ProofPanel honest empty state (F-27)', () => {
  const emptyProof: ChannelProofLog = {
    generated_at: '2026-06-15T12:00:00Z',
    channel: CHANNEL,
    events: [],
    export_formats: ['json', 'csv-ready'],
    not_claimed: ['SDI or DeckLink output'],
  }

  it('says "No proof events yet" and hides the empty table', () => {
    const { getByText, queryByText, container } = render(<ProofPanel proof={emptyProof} />)
    expect(getByText('No proof events yet')).toBeTruthy()
    expect(queryByText('Attached')).toBeNull()
    const table = container.querySelector('table')
    expect(table?.closest('[hidden]')).toBeTruthy()
  })

  it('renders a daemon proof event with captions "Not verified", never "Attached", when unproven', () => {
    const proof: ChannelProofLog = {
      ...emptyProof,
      events: [
        {
          event_id: 'proof-1',
          observed_at: '2026-06-15T12:00:00Z',
          channel_id: 'public',
          scheduled_block_id: null,
          actual_kind: 'live',
          actual_status: 'playing',
          title: 'Council chamber camera',
          source_ref: 'Council chamber camera',
          failover_from: null,
          failover_reason: null,
          captions_attached: null,
          machine_summary: 'public:ON_AIR:chamber',
        },
      ],
    }
    const { getByText, queryByText } = render(<ProofPanel proof={proof} />)
    expect(getByText('Council chamber camera')).toBeTruthy()
    expect(getByText('Not verified')).toBeTruthy()
    expect(queryByText('Attached')).toBeNull()
    expect(queryByText('No proof events yet')).toBeNull()
  })
})

// beta.5 walkthrough F-29: Start was enabled (and accepted with 202) on a
// channel with no outgoing-feed configuration; the daemon dropped it and the
// state stayed Stopped with no reason shown.
describe('EgressControlPanel Start gating (F-29)', () => {
  function renderPanel(props: Partial<Parameters<typeof EgressControlPanel>[0]> = {}) {
    return render(
      <EgressControlPanel
        channelId="public"
        state={egressState('STOPPED')}
        health={[]}
        pendingCommand={null}
        canControl
        error={null}
        onCommand={() => {}}
        {...props}
      />,
    )
  }

  it('disables Start with the inline reason when the channel has no egress configuration', () => {
    const onCommand = vi.fn()
    const { getByRole, getByText } = renderPanel({ configState: 'missing', onCommand })
    const start = getByRole('button', { name: 'Start' }) as HTMLButtonElement
    expect(start.disabled).toBe(true)
    expect(getByText(startWithoutConfigReason('public'))).toBeTruthy()
    expect(start.getAttribute('aria-describedby')).toBe('egress-start-reason')
    fireEvent.click(start)
    expect(onCommand).not.toHaveBeenCalled()
    // Stop stays available: it is a harmless no-op the daemon accepts.
    expect((getByRole('button', { name: 'Stop' }) as HTMLButtonElement).disabled).toBe(false)
  })

  it('disables Start with its own reason when the configuration exists but is disabled (m1)', () => {
    const onCommand = vi.fn()
    const { getByRole, getByText } = renderPanel({ configState: 'disabled', onCommand })
    const start = getByRole('button', { name: 'Start' }) as HTMLButtonElement
    expect(start.disabled).toBe(true)
    expect(getByText(startDisabledConfigReason('public'))).toBeTruthy()
    fireEvent.click(start)
    expect(onCommand).not.toHaveBeenCalled()
  })

  it('says the check failed, not that the configuration is missing, when the list fetch errored (m7)', () => {
    const onCommand = vi.fn()
    const onRetryConfigCheck = vi.fn()
    const { getByRole, getByText, queryByText } = renderPanel({
      configState: 'unknown',
      onCommand,
      onRetryConfigCheck,
    })
    const start = getByRole('button', { name: 'Start' }) as HTMLButtonElement
    expect(start.disabled).toBe(true)
    expect(getByText(configCheckFailedReason('public'))).toBeTruthy()
    expect(queryByText(startWithoutConfigReason('public'))).toBeNull()
    fireEvent.click(start)
    expect(onCommand).not.toHaveBeenCalled()
    fireEvent.click(getByRole('button', { name: 'Retry check' }))
    expect(onRetryConfigCheck).toHaveBeenCalledTimes(1)
  })

  it('keeps Start disabled with a checking notice until the configuration list has loaded', () => {
    const { getByRole, getByText } = renderPanel({ configState: undefined })
    expect((getByRole('button', { name: 'Start' }) as HTMLButtonElement).disabled).toBe(true)
    expect(getByText('Checking for an outgoing-feed configuration...')).toBeTruthy()
  })

  it('enables Start once a configuration exists', () => {
    const onCommand = vi.fn()
    const { getByRole, queryByText } = renderPanel({ configState: 'configured', onCommand })
    const start = getByRole('button', { name: 'Start' }) as HTMLButtonElement
    expect(start.disabled).toBe(false)
    expect(queryByText(startWithoutConfigReason('public'))).toBeNull()
    fireEvent.click(start)
    expect(onCommand).toHaveBeenCalledWith('start')
  })

  it('surfaces a not-applied alert when the daemon never left Stopped after a queued Start', () => {
    const { getByRole } = renderPanel({
      configState: 'configured',
      startNotApplied: { channelId: 'public', waitedSeconds: 20 },
    })
    const alert = getByRole('alert')
    expect(alert.textContent).toContain('Start was queued but the feed did not start.')
    expect(alert.textContent).toContain('within 20s')
  })

  it('shows the API 409 reason when the router refuses the start', () => {
    const { getByRole } = renderPanel({
      configState: 'configured',
      error: new ApiError('Conflict', 409, startWithoutConfigReason('public')),
    })
    expect(getByRole('alert').textContent).toContain('No outgoing-feed configuration for public.')
  })
})

// Hostile review M1: the daemon is the authority on what is on air. When the
// schedule covers "now" with a different program, the API says so in
// schedule_note and the panel must show it next to the daemon's block.
describe('PlayoutPanel schedule disagreement (M1)', () => {
  it('renders the schedule_note when the daemon airs something other than the scheduled block', () => {
    const nowNext: ChannelNowNext = {
      generated_at: '2026-06-15T12:00:00Z',
      channel: CHANNEL,
      current: {
        block_id: 'public-egress-on_air',
        channel_id: 'public',
        kind: 'live',
        title: 'Emergency bulletin',
        starts_at: '2026-06-15T11:58:00Z',
        duration_seconds: 120,
        source_ref: 'Emergency bulletin',
        status: 'playing',
        caption_refs: [],
        failover_from: null,
        failover_reason: null,
      },
      next: null,
      fallback_active: false,
      proof_boundary: 'egress-state-and-schedule-store',
      schedule_note:
        "Schedule lists 'Council Meeting' from 11:50 UTC, but the outgoing feed reports 'Emergency bulletin' on air. The schedule is not what is airing.",
    }
    const { getByRole, getByText, queryByText } = render(<PlayoutPanel nowNext={nowNext} />)
    expect(getByText('Emergency bulletin')).toBeTruthy()
    expect(getByRole('note').textContent).toContain('Schedule differs from what is on air.')
    expect(getByRole('note').textContent).toContain("Schedule lists 'Council Meeting'")
    expect(queryByText('Council Meeting')).toBeNull()
  })
})

// Hostile review M3: the Start watchdog compared the station server's
// updated_at against the browser clock, so a station clock >60s behind, or a
// Start on an already-on-air channel (the daemon no-ops, updated_at stays
// old), raised "Start was queued but the feed did not start." on a channel
// that was airing. These mount ChannelOpsScreen itself.
function identity(roles: string[]): StaffIdentityResponse {
  return { operator_display_name: 'Dana', roles } as unknown as StaffIdentityResponse
}

function stateRow(state: EgressStateRow['state'], updatedAt: string): EgressStateRow {
  return { channel_id: 'public', state, updated_at: updatedAt }
}

function stubScreenQueries() {
  vi.mocked(listChannelProfiles).mockResolvedValue([CHANNEL])
  vi.mocked(getStaffIdentity).mockResolvedValue(identity(['meeting_operator']))
  vi.mocked(getAppPlatformConfig).mockResolvedValue({
    station_id: 'station-1',
    station_name: 'Test Station',
    generated_at: '2026-06-15T12:00:00Z',
    default_channel_id: 'public',
    build_profile: { tier: 'unbranded', app_name: 'Test Station', platform_targets: ['web_pwa'] },
    channels: [],
    support_url: 'https://example.test/support',
    privacy_url: 'https://example.test/privacy',
  } as unknown as Awaited<ReturnType<typeof getAppPlatformConfig>>)
  vi.mocked(getChannelNowNext).mockResolvedValue({
    generated_at: '2026-06-15T12:00:00Z',
    channel: CHANNEL,
    current: null,
    next: null,
    fallback_active: false,
    proof_boundary: 'egress-state-and-schedule-store',
  } as ChannelNowNext)
  vi.mocked(getChannelProofLog).mockResolvedValue({
    generated_at: '2026-06-15T12:00:00Z',
    channel: CHANNEL,
    events: [],
    export_formats: ['json'],
    not_claimed: [],
  } as unknown as ChannelProofLog)
  vi.mocked(getChannelPlayoutPlan).mockResolvedValue({
    generated_at: '2026-06-15T12:00:00Z',
    channel: CHANNEL,
    source: 'schedule-store',
    blocks: [],
    gap_blocks: [],
    export_formats: ['json'],
    proof_boundary: 'software-schedule-to-playout-plan',
    not_claimed: [],
  } as unknown as ChannelPlayoutPlan)
  vi.mocked(getEgressHealth).mockResolvedValue([])
  vi.mocked(listEgressChannels).mockResolvedValue([
    { channel_id: 'public', enabled: true, sink_count: 1, state: null, latest_health: null },
  ] as unknown as Awaited<ReturnType<typeof listEgressChannels>>)
  vi.mocked(getEgressConfig).mockResolvedValue({
    channel_id: 'public',
    enabled: true,
    slate_message: '',
    sinks: [],
  } as unknown as Awaited<ReturnType<typeof getEgressConfig>>)
  vi.mocked(getGraphicsOverlay).mockResolvedValue({} as unknown as GraphicsOverlayStateResponse)
  vi.mocked(listHeadendProfiles).mockResolvedValue([])
  vi.mocked(getCtvFeed).mockResolvedValue({
    station_name: 'Test Station',
    items: [],
  } as unknown as Awaited<ReturnType<typeof getCtvFeed>>)
  vi.mocked(getStationProfile).mockResolvedValue({
    live_captions_enabled: false,
  } as unknown as Awaited<ReturnType<typeof getStationProfile>>)
  vi.mocked(queueEgressCommand).mockResolvedValue({
    accepted: true,
  } as unknown as Awaited<ReturnType<typeof queueEgressCommand>>)
}

function renderScreen() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <ChannelOpsScreen />
    </QueryClientProvider>,
  )
}

async function pressStartAndConfirm() {
  const start = (await screen.findByRole('button', { name: 'Start' })) as HTMLButtonElement
  await waitFor(() => expect(start.disabled).toBe(false))
  fireEvent.click(start)
  fireEvent.click(await screen.findByRole('button', { name: 'Start feed' }))
  await waitFor(() => expect(vi.mocked(queueEgressCommand)).toHaveBeenCalledWith('public', 'start'))
}

const NOT_APPLIED = 'Start was queued but the feed did not start.'

describe('startWatchApplied (M3)', () => {
  const watch = {
    channelId: 'public',
    issuedAt: Date.parse('2026-06-15T12:00:00Z'),
    baselineKnown: true,
    baselineState: 'STOPPED',
    baselineUpdatedAt: '2026-06-15T11:00:00Z',
  }

  it('treats a start-ish state as applied even when updated_at is far behind the browser clock', () => {
    expect(startWatchApplied(watch, stateRow('ON_AIR', '2026-06-15T11:50:00Z'))).toBe(true)
    expect(startWatchApplied(watch, stateRow('STARTING', '2026-06-15T11:00:00Z'))).toBe(true)
  })

  it('treats a changed row as applied and an unchanged row as not applied', () => {
    expect(startWatchApplied(watch, stateRow('STOPPED', '2026-06-15T11:00:00Z'))).toBe(false)
    expect(startWatchApplied(watch, stateRow('STOPPED', '2026-06-15T12:00:01Z'))).toBe(true)
    expect(startWatchApplied(watch, null)).toBe(true)
    expect(startWatchApplied(watch, undefined)).toBe(false)
  })

  it('never lets a late first load pass as a change when the baseline was unknown', () => {
    const unknown = { ...watch, baselineKnown: false, baselineState: null, baselineUpdatedAt: null }
    expect(startWatchApplied(unknown, stateRow('STOPPED', '2026-06-15T12:00:01Z'))).toBe(false)
    expect(startWatchApplied(unknown, stateRow('ON_AIR', '2026-06-15T11:00:00Z'))).toBe(true)
  })

  it('never treats an unchanged ERROR or FALLBACK_SLATE row as an applied start (M4)', () => {
    const stuckInError = { ...watch, baselineState: 'ERROR' }
    expect(startWatchApplied(stuckInError, stateRow('ERROR', '2026-06-15T11:00:00Z'))).toBe(false)
    // The daemon re-tried and re-failed: the row moved, so the start was acted on.
    expect(startWatchApplied(stuckInError, stateRow('ERROR', '2026-06-15T12:00:01Z'))).toBe(true)
    expect(startWatchApplied(stuckInError, stateRow('STARTING', '2026-06-15T11:00:00Z'))).toBe(true)
    const parkedOnSlate = { ...watch, baselineState: 'FALLBACK_SLATE' }
    expect(startWatchApplied(parkedOnSlate, stateRow('FALLBACK_SLATE', '2026-06-15T11:00:00Z'))).toBe(false)
    // Stopped -> slate is a real transition the daemon made in answer to the Start.
    expect(startWatchApplied(watch, stateRow('FALLBACK_SLATE', '2026-06-15T11:00:00Z'))).toBe(true)
    const unknown = { ...watch, baselineKnown: false, baselineState: null, baselineUpdatedAt: null }
    expect(startWatchApplied(unknown, stateRow('ERROR', '2026-06-15T12:00:01Z'))).toBe(false)
  })
})

describe('ChannelOpsScreen Start watchdog (M3)', () => {
  beforeEach(() => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    stubScreenQueries()
  })
  afterEach(() => {
    vi.useRealTimers()
    vi.clearAllMocks()
  })

  it('does not cry wolf when the station clock is more than 60s behind the browser', async () => {
    vi.setSystemTime(new Date('2026-06-15T12:00:00Z'))
    // Station clock five minutes behind the workstation: even the post-start
    // ON_AIR row carries an updated_at older than the command's issuedAt.
    vi.mocked(getEgressState)
      .mockResolvedValueOnce(stateRow('STOPPED', '2026-06-15T11:40:00Z'))
      .mockResolvedValue(stateRow('ON_AIR', '2026-06-15T11:55:00Z'))
    renderScreen()
    await pressStartAndConfirm()

    await waitFor(() => expect(screen.getAllByText(/On air/).length).toBeGreaterThan(0))
    await act(async () => {
      vi.advanceTimersByTime(START_APPLY_TIMEOUT_MS + 1_000)
    })

    expect(screen.queryByText(NOT_APPLIED)).toBeNull()
  })

  it('does not cry wolf on a Start pressed while the channel is already on air', async () => {
    vi.setSystemTime(new Date('2026-06-15T12:00:00Z'))
    // The daemon no-ops a start on an airing channel: the row never changes.
    vi.mocked(getEgressState).mockResolvedValue(stateRow('ON_AIR', '2026-06-15T09:00:00Z'))
    renderScreen()
    await pressStartAndConfirm()

    await act(async () => {
      vi.advanceTimersByTime(START_APPLY_TIMEOUT_MS + 1_000)
    })

    expect(screen.queryByText(NOT_APPLIED)).toBeNull()
  })

  it('raises the alert when the row never moves after an accepted Start', async () => {
    vi.setSystemTime(new Date('2026-06-15T12:00:00Z'))
    vi.mocked(getEgressState).mockResolvedValue(stateRow('STOPPED', '2026-06-15T11:00:00Z'))
    renderScreen()
    await pressStartAndConfirm()

    expect(screen.queryByText(NOT_APPLIED)).toBeNull()
    await act(async () => {
      vi.advanceTimersByTime(START_APPLY_TIMEOUT_MS + 1_000)
    })

    const alert = await screen.findByText(NOT_APPLIED)
    expect(alert.closest('[role="alert"]')?.textContent).toContain('within 20s')
  })

  // Hostile review M4: the F-29 case itself -- a valid, enabled configuration,
  // so the API answers 202, but the daemon cannot launch and left the row in
  // ERROR. It drops the Start and never rewrites the row.
  it('raises the alert when a Start is dropped on a channel already sitting in ERROR (M4)', async () => {
    vi.setSystemTime(new Date('2026-06-15T12:00:00Z'))
    vi.mocked(getEgressState).mockResolvedValue({
      ...stateRow('ERROR', '2026-06-15T11:00:00Z'),
      last_error: 'encoder unavailable: srt sink refused',
    })
    renderScreen()
    await pressStartAndConfirm()

    expect(screen.queryByText(NOT_APPLIED)).toBeNull()
    await act(async () => {
      vi.advanceTimersByTime(START_APPLY_TIMEOUT_MS + 1_000)
    })

    const alert = await screen.findByText(NOT_APPLIED)
    const text = alert.closest('[role="alert"]')?.textContent ?? ''
    expect(text).toContain('within 20s')
    expect(text).toContain('encoder unavailable: srt sink refused')
  })

  it('raises the alert when a Start is dropped on a channel parked on FALLBACK_SLATE (M4)', async () => {
    vi.setSystemTime(new Date('2026-06-15T12:00:00Z'))
    vi.mocked(getEgressState).mockResolvedValue(stateRow('FALLBACK_SLATE', '2026-06-15T09:00:00Z'))
    renderScreen()
    await pressStartAndConfirm()

    await act(async () => {
      vi.advanceTimersByTime(START_APPLY_TIMEOUT_MS + 1_000)
    })

    await screen.findByText(NOT_APPLIED)
  })

  it('does not cry wolf when a parked ERROR row moves after the Start (M4)', async () => {
    vi.setSystemTime(new Date('2026-06-15T12:00:00Z'))
    vi.mocked(getEgressState)
      .mockResolvedValueOnce(stateRow('ERROR', '2026-06-15T11:00:00Z'))
      .mockResolvedValue(stateRow('ERROR', '2026-06-15T11:00:30Z'))
    renderScreen()
    await pressStartAndConfirm()

    await act(async () => {
      vi.advanceTimersByTime(START_APPLY_TIMEOUT_MS + 1_000)
    })

    expect(screen.queryByText(NOT_APPLIED)).toBeNull()
  })
})

describe('ChannelOpsScreen configuration check failure (m7)', () => {
  beforeEach(() => {
    stubScreenQueries()
  })
  afterEach(() => {
    vi.clearAllMocks()
  })

  it('disables Start with the failed-check reason, never the missing-configuration reason', async () => {
    vi.mocked(listEgressChannels).mockRejectedValue(new Error('gateway timeout'))
    renderScreen()

    await screen.findByText(configCheckFailedReason('public'))
    expect(screen.queryByText(startWithoutConfigReason('public'))).toBeNull()
    expect((screen.getByRole('button', { name: 'Start' }) as HTMLButtonElement).disabled).toBe(true)

    vi.mocked(listEgressChannels).mockResolvedValue([
      { channel_id: 'public', enabled: true, sink_count: 1, state: null, latest_health: null },
    ] as unknown as Awaited<ReturnType<typeof listEgressChannels>>)
    fireEvent.click(screen.getByRole('button', { name: 'Retry check' }))
    await waitFor(() =>
      expect((screen.getByRole('button', { name: 'Start' }) as HTMLButtonElement).disabled).toBe(false),
    )
  })
})


describe('all-channel confidence', () => {
  it('distinguishes fresh measurements from stale and unavailable channels', async () => {
    const now = new Date().toISOString()
    const old = new Date(Date.now() - 180_000).toISOString()
    vi.mocked(getEgressState).mockImplementation(async (id) => ({ channel_id: id,
      state: 'ON_AIR', current_source_label: id === 'fresh' ? 'Council meeting' : 'Old program', updated_at: now }))
    vi.mocked(getEgressHealth).mockImplementation(async (id) => {
      if (id === 'missing') throw new Error('offline')
      return [{ channel_id: id, sampled_at: id === 'fresh' ? now : old, state: 'ON_AIR',
        sink_connected: {}, dropped_frames: 0, seconds_on_air: 10, last_loudness_lufs: -23 }]
    })
    vi.mocked(getCaptionStatus).mockImplementation(async (id) => ({ channel_id: id, caption_status: 'on',
      latest: { sampled_at: id === 'fresh' ? now : old, status: 'PASS' } as NonNullable<Awaited<ReturnType<typeof getCaptionStatus>>['latest']> }))
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    const { findByText, getByLabelText } = render(<QueryClientProvider client={client}>
      <ChannelConfidence channelId="fresh" /><ChannelConfidence channelId="stale" /><ChannelConfidence channelId="missing" />
    </QueryClientProvider>)
    expect(await findByText('Prepared audio: -23.0 LUFS last recorded (not live output)')).toBeTruthy()
    expect(getByLabelText('Confidence for fresh').textContent).toContain('Program: Council meeting')
    expect(getByLabelText('Confidence for fresh').textContent).toContain('Captions: Verified on')
    await waitFor(() => expect(getByLabelText('Confidence for stale').textContent).toContain('Prepared audio: Stale'))
    expect(getByLabelText('Confidence for stale').textContent).toContain('Captions: Stale')
    await waitFor(() => expect(getByLabelText('Confidence for missing').textContent).toContain('Output: Unavailable'))
  })
})


describe('R9 operator controls', () => {
  it('shows pending status without claiming the channel is stopped', () => {
    const view = render(<EgressControlPanel channelId="public" state={undefined} loading
      health={[]} pendingCommand={null} canControl={false} error={null} onCommand={() => {}} />)
    expect(view.getByText('Checking status…')).toBeTruthy()
    expect(view.queryByText('Stopped')).toBeNull()
  })
  it('saves repeat separately from automatic startup through the normal UI', async () => {
    stubScreenQueries()
    vi.mocked(getStaffIdentity).mockResolvedValue(identity(['setup_admin']))
    vi.mocked(getEgressState).mockResolvedValue(null)
    const current = { channel_id: 'public', enabled: true, auto_start: false, repeat_schedule: false,
      slate_message: 'Preparing', sinks: [{kind: 'file', label: 'Test', uri: 'test.ts'}] } as EgressConfig
    vi.mocked(getEgressConfig).mockResolvedValue(current)
    vi.mocked(updateEgressConfig).mockResolvedValue({...current, repeat_schedule: true})
    renderScreen()
    const repeat = await screen.findByRole('checkbox', {name: /Repeat the published sequence/})
    fireEvent.click(repeat)
    fireEvent.click(screen.getByRole('button', {name: 'Save automation settings'}))
    await waitFor(() => expect(updateEgressConfig).toHaveBeenCalledWith('public', expect.objectContaining({
      repeat_schedule: true, auto_start: false,
    })))
  })
})

// rc.6 walkthrough on the 5060 Ti: switching channels stacked stale
// "Channel startup and repeat" panels, because the automation panel and the
// embed panel were siblings keyed by the same bare channel id. An operator
// could then save the wrong channel's settings.
describe('channel switch keeps one automation panel', () => {
  it('shows exactly one Channel startup and repeat panel after switching channels', async () => {
    stubScreenQueries()
    const EDUCATION = { ...CHANNEL, channel_id: 'education', slug: 'education', kind: 'education',
      branding: { display_name: 'Education Channel' } as ChannelProfile['branding'] } as ChannelProfile
    vi.mocked(listChannelProfiles).mockResolvedValue([CHANNEL, EDUCATION])
    vi.mocked(getStaffIdentity).mockResolvedValue(identity(['setup_admin']))
    vi.mocked(getEgressState).mockResolvedValue(null)
    vi.mocked(getEgressConfig).mockImplementation(async (id) => ({ channel_id: id, enabled: true,
      auto_start: false, repeat_schedule: false, slate_message: '', sinks: [] }) as unknown as EgressConfig)
    const errors = vi.spyOn(console, 'error').mockImplementation(() => {})
    renderScreen()
    await screen.findByText(/Applies only to selected channel public\./)
    fireEvent.click(await screen.findByRole('button', { name: /Education Channel/ }))
    await screen.findByText(/Applies only to selected channel education\./)
    fireEvent.click(screen.getByRole('button', { name: /Public Channel/ }))
    await screen.findByText(/Applies only to selected channel public\./)
    expect(screen.getAllByRole('region', { name: 'Channel automation settings' })).toHaveLength(1)
    expect(screen.queryByText(/Applies only to selected channel education\./)).toBeNull()
    const dupKey = errors.mock.calls.filter((c) => String(c[0]).includes('same key'))
    errors.mockRestore()
    expect(dupKey).toHaveLength(0)
  })
})
