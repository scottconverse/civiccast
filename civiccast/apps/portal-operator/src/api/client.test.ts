import { afterEach, describe, expect, it, vi } from 'vitest'
import {
  ApiError,
  downloadUnderwriterAffidavitExport,
  downloadReportsExport,
  fireControlRoomCue,
  getControlRoomReadiness,
  getStationSetupState,
  queueCaptionBatch,
  uploadAssetFile,
} from './client'

describe('recording caption batch requests', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
    window.localStorage.clear()
    window.sessionStorage.clear()
  })

  it('sends the selected asset IDs as a JSON object the batch API can parse', async () => {
    const assetIds = ['recording-en-es', 'another-recording']
    const results = assetIds.map((asset_id) => ({ asset_id, error: null }))
    const fetchMock = vi.fn<typeof fetch>(async (path, init) => {
      expect(path).toBe('/api/staff/captions/offline-jobs/batch')
      expect(init?.method).toBe('POST')
      expect(new Headers(init?.headers).get('Content-Type')).toBe('application/json')
      // Exercise the real request serializer at the fetch boundary. A JSON
      // string here is rejected by CaptionBatchRequest with HTTP 422.
      expect(JSON.parse(String(init?.body))).toEqual({ asset_ids: assetIds })
      return new Response(JSON.stringify(results), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      })
    })
    vi.stubGlobal('fetch', fetchMock)

    await expect(queueCaptionBatch(assetIds)).resolves.toEqual(results)
    expect(fetchMock).toHaveBeenCalledTimes(1)
  })
})

describe('rehearsal media upload', () => {
  afterEach(() => {
    vi.restoreAllMocks()
    window.localStorage.clear()
  })

  it('marks a local test clip as the exact private-rehearsal sample', async () => {
    const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(
      new Response(JSON.stringify({ asset_id: 'test-clip' }), {
        status: 201,
        headers: { 'Content-Type': 'application/json' },
      }),
    )
    vi.stubGlobal('fetch', fetchMock)

    await uploadAssetFile({
      assetId: 'test-clip',
      title: 'Test clip',
      file: new File(['video'], 'test clip.mp4', { type: 'video/mp4' }),
      selectForRehearsal: true,
    })

    const request = fetchMock.mock.calls[0]![1]!
    const body = request.body as FormData
    expect(body.get('select_for_rehearsal')).toBe('true')
    expect(body.get('asset_id')).toBe('test-clip')
    expect(body.get('file')).toBeInstanceOf(File)
  })
})

describe('staff authentication throttling', () => {
  afterEach(() => {
    vi.restoreAllMocks()
    window.localStorage.clear()
  })

  it('preserves Retry-After as an actionable duration', async () => {
    window.localStorage.setItem('civiccast.staffToken', 'bad-token')
    vi.stubGlobal(
      'fetch',
      vi.fn(async () =>
        new Response(
          JSON.stringify({ detail: 'Too many failed staff authentication attempts. Wait and retry.' }),
          {
            status: 429,
            headers: { 'Content-Type': 'application/json', 'Retry-After': '37' },
          },
        ),
      ),
    )

    await expect(getControlRoomReadiness()).rejects.toMatchObject({
      status: 429,
      retryAfterSeconds: 37,
      detail: 'Too many failed attempts to authenticate with the staff API. Wait 37 seconds, then try again.',
    } satisfies Partial<ApiError>)
  })

  it('uses singular copy for a one-second Retry-After', () => {
    const error = new ApiError(
      'request failed',
      429,
      'Too many failed staff authentication attempts. Wait and retry.',
      undefined,
      1,
    )

    expect(error.detail).toBe(
      'Too many failed attempts to authenticate with the staff API. Wait 1 second, then try again.',
    )
  })

  it('never claims a sign-in was attempted or blames a shared network', () => {
    // Day-one-lockout audit finding #3: the old copy ("Too many unsuccessful
    // sign-in attempts from this network") asserted a cause -- deliberate
    // sign-in attempts, plural, from an entire shared network -- that a
    // visitor who never typed a password never took, and blamed everyone on
    // a shared NAT/building for one connection's failed requests. The new
    // copy must describe only what is verifiably true and never invoke
    // "sign-in" or "network" framing.
    const error = new ApiError(
      'request failed',
      429,
      'Too many failed staff authentication attempts. Wait and retry.',
      undefined,
      5,
    )

    expect(error.detail).not.toMatch(/sign-in attempt/i)
    expect(error.detail).not.toMatch(/network/i)
    expect(error.detail).toContain('Wait 5 seconds')
  })
})

describe('setup requests carry no nonce', () => {
  afterEach(() => {
    vi.restoreAllMocks()
    window.localStorage.clear()
    window.sessionStorage.clear()
  })

  it('never sends an X-CivicCast-Setup-Nonce header -- first setup is admitted by loopback IP alone', async () => {
    // Even a stray query-string value or a leftover sessionStorage key from
    // before the nonce/handoff mechanism was retired must not resurface as
    // a header: the backend no longer recognizes one, and the frontend must
    // not manufacture one either.
    window.history.replaceState(null, '', '/operator/?nonce=stray-leftover-value#/setup')
    window.sessionStorage.setItem('civiccast.setupNonce', 'stale-leftover-value')
    const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(
      new Response(JSON.stringify({ setup_complete: false }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }),
    )
    vi.stubGlobal('fetch', fetchMock)

    await getStationSetupState()

    const init = fetchMock.mock.calls[0]![1]
    const headers = init?.headers as Record<string, string>
    expect(headers).not.toHaveProperty('X-CivicCast-Setup-Nonce')
  })
})

describe('authenticated staff downloads', () => {
  afterEach(() => {
    vi.restoreAllMocks()
    window.localStorage.clear()
    window.sessionStorage.clear()
  })

  it('attaches the staff bearer token to report exports', async () => {
    window.history.replaceState(null, '', '/operator/#/reports')
    window.localStorage.setItem('civiccast.staffToken', 'staff-token')
    const fetchMock = vi.fn(async () => new Response('asset_id,title\n', { status: 200 }))
    vi.stubGlobal('fetch', fetchMock)

    await downloadReportsExport({
      type: 'shows',
      format: 'csv',
      from: '2026-06-01T00:00:00Z',
      to: '2026-06-02T00:00:00Z',
      channel: 'public',
    })

    expect(fetchMock).toHaveBeenCalledWith(
      '/api/staff/reports/export?from=2026-06-01T00%3A00%3A00Z&to=2026-06-02T00%3A00%3A00Z&channel=public&type=shows&format=csv',
      expect.objectContaining({
        headers: expect.objectContaining({
          Authorization: 'Bearer staff-token',
        }),
      }),
    )
  })

  it('authenticates affidavit exports and preserves the response filename and media type', async () => {
    window.localStorage.setItem('civiccast.staffToken', 'staff-token')
    const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(
      new Response('aired_at_iso,channel_id\n', {
        status: 200,
        headers: {
          'Content-Type': 'text/csv; charset=utf-8',
          'Content-Disposition':
            `attachment; filename="affidavit-2026-07-01-2026-07-31.csv"; filename*=UTF-8''affidavit-Acme%20Co-op-2026-07-01-2026-07-31.csv`,
        },
      }),
    )
    vi.stubGlobal('fetch', fetchMock)

    const file = await downloadUnderwriterAffidavitExport({
      underwriter: 'Acme Co-op',
      from: '2026-07-01',
      to: '2026-07-31',
      format: 'csv',
    })

    expect(fetchMock).toHaveBeenCalledWith(
      '/api/staff/underwriting/affidavits/export?underwriter=Acme+Co-op&from=2026-07-01&to=2026-07-31&format=csv',
      expect.objectContaining({
        headers: expect.objectContaining({ Authorization: 'Bearer staff-token' }),
      }),
    )
    expect(file.filename).toBe('affidavit-Acme Co-op-2026-07-01-2026-07-31.csv')
    expect(file.blob.type).toBe('text/csv;charset=utf-8')
  })
})

describe('control-room live fire client', () => {
  afterEach(() => {
    vi.restoreAllMocks()
    window.localStorage.clear()
    window.sessionStorage.clear()
  })

  it('sends the dry-run material fingerprint with the fire request', async () => {
    window.localStorage.setItem('civiccast.staffToken', 'staff-token')
    const fetchMock = vi.fn(async () => (
      new Response(JSON.stringify({
        event_id: 'e1',
        session_id: 's1',
        cue_id: 'c1',
        operator_id: 'op',
        device_id: 'dev',
        action: 'scene',
        result: 'fired',
        fired_at: '2026-01-01T00:00:00Z',
        detail: {},
      }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      })
    ))
    vi.stubGlobal('fetch', fetchMock)

    await fireControlRoomCue('s1', 'c1', { material_state_fingerprint: 'abc123' })

    expect(fetchMock).toHaveBeenCalledWith(
      '/api/staff/control-room/sessions/s1/cues/c1/fire',
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({ material_state_fingerprint: 'abc123' }),
        headers: expect.objectContaining({
          Authorization: 'Bearer staff-token',
        }),
      }),
    )
  })

  it('fetches the control-room readiness report with the staff token', async () => {
    window.localStorage.setItem('civiccast.staffToken', 'staff-token')
    const fetchMock = vi.fn(async () => (
      new Response(JSON.stringify({
        generated_at: '2026-06-30T12:00:00Z',
        ready_for_on_air: false,
        station_device_ready: false,
        summary: 'Control-room configuration has blockers before On-Air use.',
        devices_configured: 0,
        devices_enabled: 0,
        devices_missing_profile: [],
        surfaces_configured: 0,
        cues_configured: 0,
        open_sessions: 0,
        open_on_air_sessions: 0,
        checks: [],
        lpm_profiles: [],
        proof_boundary: 'Readiness is not station-device evidence.',
      }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      })
    ))
    vi.stubGlobal('fetch', fetchMock)

    await getControlRoomReadiness()

    expect(fetchMock).toHaveBeenCalledWith(
      '/api/staff/control-room/readiness',
      expect.objectContaining({
        headers: expect.objectContaining({
          Authorization: 'Bearer staff-token',
        }),
      }),
    )
  })
})
