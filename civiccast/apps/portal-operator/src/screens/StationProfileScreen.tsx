// SPDX-License-Identifier: Apache-2.0
// Copyright (c) The CivicCast Authors
//
// Operator console: Setup > Station Profile (S1). Two distinct panels, matching
// the backend's deliberate identity/capability split (S1 §2):
//
// 1. Station Identity -- the mutable operator-facing profile (name, timezone,
//    storage roots, default channel) via GET/PUT /api/staff/station/profile.
//    Read: setup_admin, meeting_operator, support_admin. Write: setup_admin.
// 2. StationBoxProfile -- the S1 computed, read-only cable/PEG appliance
//    readiness report (hardware, playout engine, PEG readiness roll-up,
//    cable-grade-OS verdict) via GET /api/staff/station-box-profile.

import { type ReactNode, useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link } from 'react-router'
import { AuthRequiredState } from '../components/AuthRequiredState'
import {
  acknowledgeRecoveryKit,
  ApiError,
  getStaffIdentity,
  getStationBoxProfile,
  getStationProfile,
  regenerateRecoveryKit,
  revokeOtherOperatorSessions,
  updateStationProfile,
} from '../api/client'
import { manualLink } from './manual-link'
import type {
  PegReadinessDimension,
  RecoveryKitRegenerateResponse,
  StaffIdentityResponse,
  StationBoxProfile,
  StationProfile,
} from '../types/api.generated'
import { hasRole } from './contribution-format'
import { RecordedCaptionSettingsPanel } from './RecordedCaptionSettingsPanel'

const READ_ROLES = ['setup_admin', 'meeting_operator', 'support_admin']
const WRITE_ROLES = ['setup_admin']

type Tone = 'neutral' | 'ok' | 'warn' | 'info' | 'red'

const TONE_COLORS: Record<Tone, { bg: string; bd: string }> = {
  neutral: { bg: 'var(--cc-surface-2)', bd: 'var(--cc-line)' },
  ok: { bg: 'var(--cc-ok-soft)', bd: 'var(--cc-ok)' },
  warn: { bg: 'var(--cc-warn-soft)', bd: 'var(--cc-warn)' },
  info: { bg: 'var(--cc-info-soft)', bd: 'var(--cc-info)' },
  red: { bg: 'var(--cc-warn-soft)', bd: 'var(--cc-warn)' },
}

function apiMessage(err: unknown, fallback: string): string {
  if (err instanceof ApiError) return err.detail || err.message || fallback
  if (err instanceof Error) return err.message || fallback
  return fallback
}

// role="alert" reserved for genuine error/notice banners (assertive live
// region) -- never for the per-dimension readiness badges below, which must
// not interrupt a screen reader on render.
function Banner({ tone, children }: { tone: Tone; children: ReactNode }) {
  const c = TONE_COLORS[tone]
  return (
    <div role="alert" className="rounded-md p-3 text-sm" style={{ background: c.bg, border: `1px solid ${c.bd}` }}>
      {children}
    </div>
  )
}

/**
 * Copies a storage-root path to the clipboard.
 *
 * Field evidence (candidate #17): a station's Media/Recordings/Backups
 * default paths live under the Windows service account's own profile
 * (`C:\Windows\System32\config\systemprofile\AppData\Local\CivicCast\...`),
 * which "told recordings are saved locally" does not prepare an operator to
 * find by browsing their own account. There is no reliable way to pop a
 * File Explorer window open from this backend (it runs as a Windows
 * service in Session 0, which cannot show UI on any interactive desktop --
 * see docs/USER-MANUAL.md's "Where Recordings And Backups Live"), so the
 * one thing this control can honestly promise is copying the exact path so
 * it can be pasted into Explorer's address bar or handed to an admin.
 */
function CopyPathButton({ path }: { path: string }) {
  const [copied, setCopied] = useState(false)
  return (
    <button
      type="button"
      onClick={async () => {
        try {
          await navigator.clipboard.writeText(path)
          setCopied(true)
          window.setTimeout(() => setCopied(false), 2000)
        } catch {
          setCopied(false)
        }
      }}
      className="justify-self-start rounded-md px-2 py-1 text-[11px] font-semibold"
      style={{ background: 'var(--cc-surface-2)', border: '1px solid var(--cc-line)', color: 'var(--cc-ink)' }}
    >
      {copied ? 'Copied' : 'Copy path'}
    </button>
  )
}

function colorTone(color: 'green' | 'yellow' | 'red'): Tone {
  if (color === 'green') return 'ok'
  if (color === 'yellow') return 'warn'
  return 'red'
}

// A plain inline badge, never color-only (S20): the color word is always in
// the visible text, not just the background.
function ReadinessBadge({ overall }: { overall: 'green' | 'yellow' | 'red' }) {
  const c = TONE_COLORS[colorTone(overall)]
  return (
    <span
      className="rounded px-2 py-0.5 text-xs font-semibold uppercase"
      style={{ background: c.bg, border: `1px solid ${c.bd}` }}
    >
      {overall}
    </span>
  )
}

function ReadinessDimensionRow({ dimension }: { dimension: PegReadinessDimension }) {
  const c = TONE_COLORS[colorTone(dimension.color)]
  return (
    <li
      className="flex flex-col gap-0.5 rounded-md p-2 text-xs"
      style={{ background: c.bg, border: `1px solid ${c.bd}` }}
    >
      <div className="flex items-center justify-between gap-2">
        <span className="font-semibold">{dimension.label}</span>
        <span className="uppercase" style={{ color: 'var(--cc-ink-3)' }}>
          {dimension.color}
        </span>
      </div>
      <span>{dimension.message}</span>
      {dimension.next_step ? (
        <span style={{ color: 'var(--cc-ink-3)' }}>Next step: {dimension.next_step}</span>
      ) : null}
    </li>
  )
}

interface EditableFields {
  station_name: string
  station_timezone: string
  public_base_url: string
  default_channel_id: string
  media_library: string
  recordings: string
  backups: string
  live_captions_enabled: boolean
}

function fieldsFromProfile(profile: StationProfile): EditableFields {
  return {
    station_name: profile.station_name,
    station_timezone: profile.station_timezone ?? 'local',
    public_base_url: profile.public_base_url ?? '',
    default_channel_id: profile.default_channel_id ?? '',
    media_library: profile.storage_locations.media_library,
    recordings: profile.storage_locations.recordings,
    backups: profile.storage_locations.backups,
    // Absent only when talking to a control plane older than this setting.
    // beta.5 ships live captions OFF by default (the caption embed leg held
    // video for 25-30 s and burst-released it every 1-2 minutes in the
    // 2026-09-09 sandbox soak), so a missing value reads as OFF -- the same
    // default the server itself reports for a profile without the key.
    live_captions_enabled: profile.live_captions_enabled ?? false,
  }
}

function StationIdentityPanel({ canWrite }: { canWrite: boolean }) {
  const qc = useQueryClient()
  const profileQuery = useQuery<StationProfile>({
    queryKey: ['station-profile'],
    queryFn: getStationProfile,
    retry: false,
  })
  // Derived-state pattern (no effect): `fields` starts as whatever the
  // server last returned, and local edits are layered on top. `resetKey`
  // remembers which server snapshot `fields` was initialized from, so a
  // fresh fetch (e.g. after `onSuccess` refetches) can re-derive without a
  // useEffect synchronizing state from props/query data.
  const [fields, setFields] = useState<EditableFields | null>(null)
  const [resetKey, setResetKey] = useState<string | null>(null)
  const [dirty, setDirty] = useState(false)

  const serverKey = profileQuery.data ? profileQuery.data.recovery_kit_generated_at : null
  if (profileQuery.data && resetKey !== serverKey && !dirty) {
    // Safe to call setState during render here (not inside an effect): this
    // mirrors React's documented "adjusting state when a prop changes"
    // pattern -- it re-renders once more with the derived value instead of
    // committing a stale frame, and never fires when `dirty` is true.
    setFields(fieldsFromProfile(profileQuery.data))
    setResetKey(serverKey)
  }

  const saveMut = useMutation({
    mutationFn: (v: EditableFields) =>
      updateStationProfile({
        station_name: v.station_name,
        station_timezone: v.station_timezone,
        public_base_url: v.public_base_url || null,
        default_channel_id: v.default_channel_id,
        storage_locations: {
          media_library: v.media_library,
          recordings: v.recordings,
          backups: v.backups,
        },
        live_captions_enabled: v.live_captions_enabled,
      }),
    onSuccess: (data) => {
      qc.setQueryData(['station-profile'], data)
      void qc.invalidateQueries({ queryKey: ['station-timezone'] })
      setFields(fieldsFromProfile(data))
      setDirty(false)
    },
  })

  if (profileQuery.isLoading) {
    return (
      <p className="text-xs" style={{ color: 'var(--cc-ink-3)' }}>
        Loading station identity…
      </p>
    )
  }

  if (profileQuery.isError) {
    const notSetUp = profileQuery.error instanceof ApiError && profileQuery.error.status === 404
    if (notSetUp) {
      return (
        <Banner tone="info">
          No station identity yet -- complete First Setup to create the station profile before
          editing it here.
        </Banner>
      )
    }
    return <Banner tone="warn">{apiMessage(profileQuery.error, 'Could not load the station identity.')}</Banner>
  }

  if (!fields) {
    return (
      <Banner tone="info">No station identity is available yet.</Banner>
    )
  }

  function update<K extends keyof EditableFields>(key: K, value: EditableFields[K]) {
    setFields((prev) => (prev ? { ...prev, [key]: value } : prev))
    setDirty(true)
  }

  const canSave = canWrite && dirty && fields.station_name.trim().length > 0 && !saveMut.isPending
  const resolvedPaths = (
    profileQuery.data as StationProfile & {
      runtime_paths?: { installation_root: string; database_data: string; media_uploads: string } | null
    }
  ).runtime_paths

  return (
    <section
      aria-label="Station identity"
      className="space-y-3 rounded-md p-3 text-sm"
      style={{ background: 'var(--cc-surface)', border: '1px solid var(--cc-line)' }}
    >
      <div className="flex items-center justify-between gap-2">
        <h2 className="text-sm font-semibold">Station identity</h2>
        {!canWrite && (
          <span className="text-xs" style={{ color: 'var(--cc-ink-3)' }}>
            read-only
          </span>
        )}
      </div>

      {saveMut.isError && (
        <Banner tone="warn">{apiMessage(saveMut.error, 'Could not save the station profile.')}</Banner>
      )}
      {saveMut.isSuccess && !dirty && <Banner tone="ok">Station profile saved.</Banner>}
      {saveMut.isSuccess && !dirty && saveMut.data?.live_captions_notice && (
        // rc.6.1 item 3: the live-caption switch applies at each channel's next
        // start; the server says so, and how to apply it now.
        <Banner tone="info">{saveMut.data.live_captions_notice}</Banner>
      )}

      <div className="grid gap-3 sm:grid-cols-2">
        <label className="grid gap-1 text-xs">
          <span style={{ color: 'var(--cc-ink-3)' }}>Station name</span>
          <input
            aria-label="Station name"
            value={fields.station_name}
            disabled={!canWrite || saveMut.isPending}
            onChange={(e) => update('station_name', e.target.value)}
            className="rounded-md px-2 py-1.5"
            style={{ background: 'var(--cc-surface)', border: '1px solid var(--cc-line)', color: 'var(--cc-ink)' }}
          />
        </label>
        <label className="grid gap-1 text-xs">
          <span style={{ color: 'var(--cc-ink-3)' }}>Timezone (IANA name, or "local")</span>
          <input
            aria-label="Station timezone"
            value={fields.station_timezone}
            disabled={!canWrite || saveMut.isPending}
            onChange={(e) => update('station_timezone', e.target.value)}
            placeholder="America/New_York"
            className="rounded-md px-2 py-1.5"
            style={{ background: 'var(--cc-surface)', border: '1px solid var(--cc-line)', color: 'var(--cc-ink)' }}
          />
        </label>
        <label className="grid gap-1 text-xs">
          <span style={{ color: 'var(--cc-ink-3)' }}>Default channel id</span>
          <input
            aria-label="Default channel id"
            value={fields.default_channel_id}
            disabled={!canWrite || saveMut.isPending}
            onChange={(e) => update('default_channel_id', e.target.value)}
            className="rounded-md px-2 py-1.5"
            style={{ background: 'var(--cc-surface)', border: '1px solid var(--cc-line)', color: 'var(--cc-ink)' }}
          />
        </label>
        <label className="grid gap-1 text-xs">
          <span style={{ color: 'var(--cc-ink-3)' }}>Public base URL (optional)</span>
          <input
            aria-label="Public base URL"
            value={fields.public_base_url}
            disabled={!canWrite || saveMut.isPending}
            onChange={(e) => update('public_base_url', e.target.value)}
            placeholder="https://watch.example.gov"
            className="rounded-md px-2 py-1.5"
            style={{ background: 'var(--cc-surface)', border: '1px solid var(--cc-line)', color: 'var(--cc-ink)' }}
          />
        </label>
      </div>

      {/* Not a <fieldset disabled>: that would also disable the Copy path
          buttons below for a read-only meeting_operator/support_admin
          viewer (READ_ROLES), who can read this panel but not write it --
          exactly the audience most likely to be hunting for a recording,
          not editing the station profile (PR #74 review). Each editable
          <input> is disabled individually instead; Copy path stays a
          read-only action, always enabled for anyone who can see this
          panel at all. */}
      <div className="grid gap-3 sm:grid-cols-3">
        <div className="text-xs sm:col-span-3" style={{ color: 'var(--cc-ink-3)' }}>
          Storage roots
        </div>
        <div className="grid gap-1 text-xs">
          <span style={{ color: 'var(--cc-ink-3)' }}>Media library</span>
          <input
            aria-label="Media library path"
            value={fields.media_library}
            disabled={!canWrite || saveMut.isPending}
            onChange={(e) => update('media_library', e.target.value)}
            className="rounded-md px-2 py-1.5"
            style={{ background: 'var(--cc-surface)', border: '1px solid var(--cc-line)', color: 'var(--cc-ink)' }}
          />
          <CopyPathButton path={fields.media_library} />
        </div>
        <div className="grid gap-1 text-xs">
          <span style={{ color: 'var(--cc-ink-3)' }}>Recordings</span>
          <input
            aria-label="Recordings path"
            value={fields.recordings}
            disabled={!canWrite || saveMut.isPending}
            onChange={(e) => update('recordings', e.target.value)}
            className="rounded-md px-2 py-1.5"
            style={{ background: 'var(--cc-surface)', border: '1px solid var(--cc-line)', color: 'var(--cc-ink)' }}
          />
          <CopyPathButton path={fields.recordings} />
        </div>
        <div className="grid gap-1 text-xs">
          <span style={{ color: 'var(--cc-ink-3)' }}>Backups</span>
          <input
            aria-label="Backups path"
            value={fields.backups}
            disabled={!canWrite || saveMut.isPending}
            onChange={(e) => update('backups', e.target.value)}
            className="rounded-md px-2 py-1.5"
            style={{ background: 'var(--cc-surface)', border: '1px solid var(--cc-line)', color: 'var(--cc-ink)' }}
          />
          <CopyPathButton path={fields.backups} />
        </div>
      </div>

      {resolvedPaths && (
        <div className="grid gap-1 text-xs" style={{ color: 'var(--cc-ink-2)' }}>
          <div className="text-[10px] font-semibold uppercase" style={{ color: 'var(--cc-ink-3)' }}>
            Resolved service paths
          </div>
          <div className="break-all">Installation: {resolvedPaths.installation_root}</div>
          <div className="break-all">Database: {resolvedPaths.database_data}</div>
          <div className="break-all">Media uploads: {resolvedPaths.media_uploads}</div>
        </div>
      )}

      <p className="text-xs" style={{ color: 'var(--cc-ink-3)' }}>
        These are the station&apos;s configured library, recording and backup roots. The read-only
        resolved service paths below show where CivicCast is installed, stores its database and
        receives media uploads. Use <strong>Assets</strong> to find a recording in CivicCast. If you
        need a configured folder, use Copy path above and paste it into File Explorer; ask an
        administrator if Windows denies access.{' '}
        <Link to={manualLink('configuration-storage')} style={{ color: 'var(--cc-brand)' }}>
          Read more in the manual
        </Link>
        . An environment override for the storage roots wins over what is saved here — this form
        shows the value currently in effect.
      </p>

      {/* Live captions. A real switch for a feature that, on an activated
          station, is otherwise ON for every channel with no way to stop it.
          A checkbox rather than a styled toggle: it is keyboard-operable and
          announces its own checked state without any ARIA of our own, and the
          <label> wraps the control so the whole row is a hit target. The
          explanation sits in its own <p> referenced by aria-describedby, so a
          screen reader reads the consequence, not just the label. */}
      <div
        className="grid gap-2 rounded-md p-3"
        style={{ background: 'var(--cc-surface-2)', border: '1px solid var(--cc-line)' }}
      >
        <label className="flex items-start gap-2 text-sm">
          <input
            type="checkbox"
            className="mt-0.5"
            checked={fields.live_captions_enabled}
            disabled={!canWrite || saveMut.isPending}
            aria-describedby="live-captions-help"
            onChange={(e) => update('live_captions_enabled', e.target.checked)}
          />
          <span style={{ color: 'var(--cc-ink)' }}>Show live captions on air</span>
        </label>
        <p id="live-captions-help" className="text-xs" style={{ color: 'var(--cc-ink-2)' }}>
          Live captions are off on a newly installed station. When enabled, they share computer
          resources with playout, so captions may have gaps under heavy load; picture and sound
          take priority. Turning captions off stops speech recognition and drains queued audio on
          the next worker scan; caption routing is removed at the next channel start. Turning
          captions on resumes speech recognition on the next worker scan, with caption routing
          added at the next channel start. Recording captions are produced separately and are
          <strong> not</strong> affected by this setting.{' '}
          {/* Deliberately NOT another "Read more in the manual": this screen
              already has one, and two links with identical text are ambiguous
              to anyone navigating by link list rather than by eye. */}
          <Link to={manualLink('live-captions-what-the-settings-change')} style={{ color: 'var(--cc-brand)' }}>
            More about live captions in the manual
          </Link>
          .
        </p>
      </div>

      {canWrite && (
        <div className="flex justify-end">
          <button
            type="button"
            aria-label="Save station profile"
            disabled={!canSave}
            onClick={() => fields && saveMut.mutate(fields)}
            className="rounded-md px-3 py-1.5 font-semibold disabled:opacity-50"
            style={{ background: 'var(--cc-surface)', border: '1px solid var(--cc-line)' }}
          >
            {saveMut.isPending ? 'Saving…' : 'Save'}
          </button>
        </div>
      )}
    </section>
  )
}

function StationBoxProfilePanel() {
  const boxQuery = useQuery<StationBoxProfile>({
    queryKey: ['station-box-profile'],
    queryFn: getStationBoxProfile,
    retry: false,
  })

  if (boxQuery.isLoading) {
    return (
      <p className="text-xs" style={{ color: 'var(--cc-ink-3)' }}>
        Probing station hardware and playout-engine readiness…
      </p>
    )
  }
  if (boxQuery.isError) {
    return <Banner tone="warn">{apiMessage(boxQuery.error, 'Could not load the station box profile.')}</Banner>
  }
  const profile = boxQuery.data
  if (!profile) {
    return <Banner tone="info">No station box profile is available yet.</Banner>
  }

  return (
    <section
      aria-label="Station box profile"
      className="space-y-3 rounded-md p-3 text-sm"
      style={{ background: 'var(--cc-surface)', border: '1px solid var(--cc-line)' }}
    >
      <div className="flex items-center justify-between gap-2">
        <h2 className="text-sm font-semibold">Station box profile (S1)</h2>
        <ReadinessBadge overall={profile.peg_readiness.overall} />
      </div>

      <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-xs">
        <dt style={{ color: 'var(--cc-ink-3)' }}>CPU</dt>
        <dd>{profile.hardware.cpu.brand}</dd>
        <dt style={{ color: 'var(--cc-ink-3)' }}>RAM</dt>
        <dd>{profile.system_ram_total_gb} GB</dd>
        <dt style={{ color: 'var(--cc-ink-3)' }}>Recommended tier</dt>
        <dd className="cc-mono">{profile.hardware.recommended_tier}</dd>
        <dt style={{ color: 'var(--cc-ink-3)' }}>Playout engine</dt>
        <dd>
          {profile.engine.gstreamer_present
            ? `GStreamer ${profile.engine.gstreamer_version ?? ''}`.trim()
            : 'GStreamer not detected'}
          {' — qualifies for '}
          <span className="cc-mono">{profile.qualified_engine_tier.qualifies_for}</span>
        </dd>
        <dt style={{ color: 'var(--cc-ink-3)' }}>AI summary default</dt>
        <dd>
          {profile.ai_default.summary_model} ({profile.ai_default.basis})
        </dd>
      </dl>

      {/* S1 §5: while soak-pending, this line must read exactly the §13.1
          caveat -- no green single-Windows-PC cable claim before the
          decision. The text is server-rendered verbatim from
          cable_os_verdict.rationale, never rephrased client-side. */}
      <p className="text-xs" style={{ color: 'var(--cc-ink-3)' }}>
        <strong>Cable-grade OS:</strong> {profile.cable_os_verdict.rationale}
      </p>

      <div>
        <h3 className="mb-1 text-xs font-semibold" style={{ color: 'var(--cc-ink-3)' }}>
          PEG readiness
        </h3>
        <ul className="space-y-1">
          {(profile.peg_readiness.dimensions ?? []).map((dimension) => (
            <ReadinessDimensionRow key={dimension.id} dimension={dimension} />
          ))}
        </ul>
      </div>
    </section>
  )
}

/**
 * Newly-minted recovery codes shown exactly once, right after a successful
 * regenerate. Mirrors the discipline of the first-run RecoveryKitPanel
 * (SetupScreen.tsx) -- save-or-print, then explicitly confirm -- so an admin
 * can never lose a second kit the same way the first one could be lost.
 */
function RegeneratedKitPanel({
  kit,
  onDone,
}: {
  kit: RecoveryKitRegenerateResponse['recovery_kit']
  onDone: () => void
}) {
  const [kitActionTaken, setKitActionTaken] = useState(false)
  const [confirmed, setConfirmed] = useState(false)
  const ackMutation = useMutation({
    mutationFn: acknowledgeRecoveryKit,
    onSuccess: onDone,
  })
  const printable = useMemo(
    () =>
      [
        `CivicCast recovery kit (regenerated): ${kit.station_name}`,
        `Admin username: ${kit.admin_username}`,
        `Kit: ${kit.kit_id}`,
        '',
        'Emergency recovery codes (use ONLY if the admin password is lost --',
        'each code works once and immediately sets a new password):',
        ...kit.recovery_codes.map((code) => `- ${code}`),
        '',
        ...kit.instructions,
      ].join('\n'),
    [kit],
  )

  const download = () => {
    const blob = new Blob([printable], { type: 'text/plain;charset=utf-8' })
    const url = URL.createObjectURL(blob)
    const anchor = document.createElement('a')
    anchor.href = url
    anchor.download = `civiccast-recovery-kit-${kit.kit_id}.txt`
    anchor.click()
    URL.revokeObjectURL(url)
    setKitActionTaken(true)
  }
  const print = () => {
    window.print()
    setKitActionTaken(true)
  }

  return (
    <section
      aria-label="Newly regenerated recovery kit"
      className="grid gap-3 rounded-md p-3 text-sm"
      style={{ background: 'var(--cc-ok-soft)', border: '1px solid var(--cc-ok)' }}
    >
      <div>
        <h3 className="m-0 text-sm font-semibold">New recovery kit ready</h3>
        <p className="m-0 mt-1 text-xs" style={{ color: 'var(--cc-ink-2)' }}>
          This is the only time these 8 codes are shown. Every code from the previous kit stopped
          working the moment this kit was created. Save or print now.
        </p>
      </div>
      <div className="grid gap-2 sm:grid-cols-2">
        {kit.recovery_codes.map((code) => (
          <div
            key={code}
            className="cc-mono rounded-md px-3 py-2 text-sm font-semibold"
            style={{ background: 'var(--cc-surface)' }}
          >
            {code}
          </div>
        ))}
      </div>
      <div className="flex flex-wrap gap-2">
        <button
          type="button"
          onClick={print}
          className="rounded-md px-3 py-2 text-sm font-semibold"
          style={{ background: 'var(--cc-ink)', color: 'var(--cc-ink-inv)' }}
        >
          Print kit
        </button>
        <button
          type="button"
          onClick={download}
          className="rounded-md px-3 py-2 text-sm font-semibold"
          style={{ background: 'var(--cc-surface)', border: '1px solid var(--cc-line)' }}
        >
          Save kit
        </button>
      </div>
      <label className="flex items-start gap-2 text-sm">
        <input
          type="checkbox"
          checked={confirmed}
          disabled={!kitActionTaken}
          onChange={(event) => setConfirmed(event.target.checked)}
        />
        <span>
          I have saved or printed the new recovery codes and stored them away from this computer.
          {!kitActionTaken && (
            <span className="block text-xs" style={{ color: 'var(--cc-ink-3)' }}>
              Use Print kit or Save kit first.
            </span>
          )}
        </span>
      </label>
      <div>
        <button
          type="button"
          disabled={!confirmed || ackMutation.isPending}
          onClick={() => ackMutation.mutate()}
          className="rounded-md px-4 py-2 text-sm font-semibold disabled:opacity-50"
          style={{ background: 'var(--cc-brand)', color: 'var(--cc-brand-ink)' }}
        >
          {ackMutation.isPending ? 'Recording confirmation…' : 'Done'}
        </button>
      </div>
      {ackMutation.isError && (
        <Banner tone="warn">
          {apiMessage(ackMutation.error, 'Could not record the confirmation. Try again.')}
        </Banner>
      )}
    </section>
  )
}

function SecurityPanel({ canWrite }: { canWrite: boolean }) {
  const revokeMutation = useMutation({ mutationFn: revokeOtherOperatorSessions })
  const regenerateMutation = useMutation({ mutationFn: regenerateRecoveryKit })
  const [revokeArmed, setRevokeArmed] = useState(false)
  const [regenerateArmed, setRegenerateArmed] = useState(false)

  const newKit = regenerateMutation.data?.recovery_kit ?? null

  return (
    <section
      aria-label="Security"
      className="space-y-4 rounded-md p-3 text-sm"
      style={{ background: 'var(--cc-surface)', border: '1px solid var(--cc-line)' }}
    >
      <div className="flex items-center justify-between gap-2">
        <h2 className="text-sm font-semibold">Security</h2>
        {!canWrite && (
          <span className="text-xs" style={{ color: 'var(--cc-ink-3)' }}>
            read-only
          </span>
        )}
      </div>

      <div className="grid gap-2 rounded-md p-3" style={{ background: 'var(--cc-surface-2)' }}>
        <h3 className="m-0 text-xs font-semibold uppercase tracking-wide" style={{ color: 'var(--cc-ink-3)' }}>
          Sessions
        </h3>
        <p className="m-0 text-xs" style={{ color: 'var(--cc-ink-2)' }}>
          A routine sign-in never signs out other already-open browsers or devices, so a lost or
          stolen laptop&apos;s session otherwise stays signed in indefinitely. Use this to end
          every OTHER operator-console session right now -- this browser stays signed in.
        </p>
        {revokeMutation.isSuccess && (
          <Banner tone="ok">{revokeMutation.data.message}</Banner>
        )}
        {revokeMutation.isError && (
          <Banner tone="warn">
            {apiMessage(revokeMutation.error, 'Could not sign out other sessions. Try again.')}
          </Banner>
        )}
        {canWrite && !revokeArmed && (
          <button
            type="button"
            onClick={() => setRevokeArmed(true)}
            className="justify-self-start rounded-md px-3 py-1.5 text-xs font-semibold"
            style={{ background: 'var(--cc-surface)', border: '1px solid var(--cc-line)' }}
          >
            Sign out other sessions
          </button>
        )}
        {canWrite && revokeArmed && (
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-xs" style={{ color: 'var(--cc-ink-2)' }}>
              This immediately signs out every other browser and device signed in as this admin.
            </span>
            <button
              type="button"
              disabled={revokeMutation.isPending}
              onClick={() => {
                revokeMutation.mutate(undefined, { onSuccess: () => setRevokeArmed(false) })
              }}
              className="rounded-md px-3 py-1.5 text-xs font-semibold disabled:opacity-50"
              style={{ background: 'var(--cc-warn)', color: 'var(--cc-ink-inv)' }}
            >
              {revokeMutation.isPending ? 'Signing out…' : 'Confirm — sign out other sessions'}
            </button>
            <button
              type="button"
              onClick={() => setRevokeArmed(false)}
              className="rounded-md px-3 py-1.5 text-xs font-semibold"
              style={{ background: 'var(--cc-surface)', border: '1px solid var(--cc-line)' }}
            >
              Cancel
            </button>
          </div>
        )}
      </div>

      <div className="grid gap-2 rounded-md p-3" style={{ background: 'var(--cc-surface-2)' }}>
        <h3 className="m-0 text-xs font-semibold uppercase tracking-wide" style={{ color: 'var(--cc-ink-3)' }}>
          Recovery kit
        </h3>
        <p className="m-0 text-xs" style={{ color: 'var(--cc-ink-2)' }}>
          If the recovery kit from first-run setup was lost, never saved, or you just want a fresh
          set, mint a new one now. This requires being signed in with the current admin password --
          it is not a way back in if you are locked out.
        </p>
        {newKit ? (
          <RegeneratedKitPanel kit={newKit} onDone={() => regenerateMutation.reset()} />
        ) : (
          <>
            {regenerateMutation.isError && (
              <Banner tone="warn">
                {apiMessage(regenerateMutation.error, 'Could not regenerate the recovery kit. Try again.')}
              </Banner>
            )}
            {canWrite && !regenerateArmed && (
              <button
                type="button"
                onClick={() => setRegenerateArmed(true)}
                className="justify-self-start rounded-md px-3 py-1.5 text-xs font-semibold"
                style={{ background: 'var(--cc-surface)', border: '1px solid var(--cc-line)' }}
              >
                Regenerate recovery kit
              </button>
            )}
            {canWrite && regenerateArmed && (
              <div className="flex flex-wrap items-center gap-2">
                <span className="text-xs" style={{ color: 'var(--cc-ink-2)' }}>
                  This permanently invalidates every existing recovery code and replaces them with
                  8 new ones.
                </span>
                <button
                  type="button"
                  disabled={regenerateMutation.isPending}
                  onClick={() => {
                    regenerateMutation.mutate(undefined, {
                      onSuccess: () => setRegenerateArmed(false),
                    })
                  }}
                  className="rounded-md px-3 py-1.5 text-xs font-semibold disabled:opacity-50"
                  style={{ background: 'var(--cc-warn)', color: 'var(--cc-ink-inv)' }}
                >
                  {regenerateMutation.isPending ? 'Regenerating…' : 'Confirm — regenerate kit'}
                </button>
                <button
                  type="button"
                  onClick={() => setRegenerateArmed(false)}
                  className="rounded-md px-3 py-1.5 text-xs font-semibold"
                  style={{ background: 'var(--cc-surface)', border: '1px solid var(--cc-line)' }}
                >
                  Cancel
                </button>
              </div>
            )}
          </>
        )}
        {!canWrite && (
          <p className="m-0 text-xs" style={{ color: 'var(--cc-ink-3)' }}>
            Sessions and recovery-kit actions require the setup admin role.
          </p>
        )}
      </div>
    </section>
  )
}

export function StationProfileScreen() {
  const identityQuery = useQuery<StaffIdentityResponse>({
    queryKey: ['staff-identity'],
    queryFn: getStaffIdentity,
    retry: false,
  })
  const canRead = hasRole(identityQuery.data, READ_ROLES)
  const canWrite = hasRole(identityQuery.data, WRITE_ROLES)

  if (identityQuery.isLoading) {
    return (
      <div className="px-6 py-10 text-sm" style={{ color: 'var(--cc-ink-3)' }}>
        Loading…
      </div>
    )
  }
  if (identityQuery.isError) {
    return (
      <div className="px-6 py-10">
        <AuthRequiredState error={identityQuery.error} />
      </div>
    )
  }
  if (!canRead) {
    return (
      <div className="px-6 py-10">
        <Banner tone="info">
          Station Profile requires the setup admin, meeting operator, or support admin role. Ask
          your station admin for access.
        </Banner>
      </div>
    )
  }

  return (
    <div className="space-y-4 px-6 py-6">
      <div>
        <h1 className="text-lg font-semibold">Station Profile</h1>
        <p className="text-xs" style={{ color: 'var(--cc-ink-3)' }}>
          The station's identity (name, timezone, storage roots) and its computed cable/PEG
          appliance-readiness report -- separate concerns, per S1: identity is what you edit,
          readiness is what the box actually detects.
        </p>
      </div>
      <div className="grid gap-3 lg:grid-cols-2">
        <StationIdentityPanel canWrite={canWrite} />
        <StationBoxProfilePanel />
      </div>
      <RecordedCaptionSettingsPanel canWrite={canWrite} />
      <SecurityPanel canWrite={canWrite} />
    </div>
  )
}
