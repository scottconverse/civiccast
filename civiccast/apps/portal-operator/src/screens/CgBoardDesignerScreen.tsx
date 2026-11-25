import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  ApiError,
  addCgFeed,
  addCgZone,
  createCgBoard,
  deleteCgBoardAudio,
  deleteCgFeed,
  deleteCgZone,
  getAppPlatformConfig,
  getCgBoard,
  getCgBoardAudio,
  getGraphicsOverlay,
  getStaffIdentity,
  listCgBoardAudit,
  listLiveSources,
  listStaffAssets,
  previewCgBoard,
  setCgBoardAudio,
  updateCgBoard,
  updateCgFeed,
  updateCgZone,
  type BoardAudio,
} from '../api/client'
import { FeedApprovalQueue } from './FeedApprovalQueue'
import { hasOperatorRole } from '../auth/roles'
import type {
  BoardView,
  CgFeedSource,
  CgZoneConfig,
  FeedInput,
  FeedUpdateInput,
  LiveSourceResponse,
  ResolvedBoard,
  ZoneInput,
} from '../types/api.generated'
import {
  CONTENT_SOURCES,
  FEED_KINDS,
  REGIONS,
  TEMPLATE_OPTIONS,
  TRUST_TIERS,
  ZONE_KINDS,
  auditSummary,
  feedFetchStatus,
  formatTags,
  humanize,
  parseTags,
  zoneNeedsFeed,
  zoneSummary,
} from './cg-board-format'
import { EmptyState } from '../components/EmptyState'
import type { AssetRow } from '../types/asset'

const POLL_MS = 30_000

const panelStyle = { background: 'var(--cc-surface)', border: '1px solid var(--cc-line)' }
const insetStyle = { background: 'var(--cc-surface-2)', border: '1px solid var(--cc-line)' }
const fieldStyle = {
  background: 'var(--cc-surface)',
  border: '1px solid var(--cc-line)',
  color: 'var(--cc-ink)',
}

function apiMessage(error: unknown, fallback: string): string {
  if (error instanceof ApiError) return error.detail ?? fallback
  if (error instanceof Error) return error.message
  return fallback
}

function AccessNote({ what }: { what: string }) {
  return (
    <div className="rounded-md p-3 text-sm" style={{ background: 'var(--cc-surface-2)', color: 'var(--cc-ink-2)' }}>
      Viewing and managing {what} requires the publish operator, setup admin, or support admin role.
    </div>
  )
}

function PrimaryButton({
  label,
  disabled,
  onClick,
}: {
  label: string
  disabled?: boolean
  onClick: () => void
}) {
  return (
    <button
      type="button"
      disabled={disabled}
      onClick={onClick}
      className="rounded-md px-3 py-1.5 text-xs font-semibold"
      style={{
        background: disabled ? 'var(--cc-surface-3)' : 'var(--cc-brand)',
        color: disabled ? 'var(--cc-ink-3)' : 'var(--cc-brand-ink)',
      }}
    >
      {label}
    </button>
  )
}

function ConfirmDeleteButton({ onDelete }: { onDelete: () => void }) {
  const [confirming, setConfirming] = useState(false)
  if (!confirming) {
    return (
      <button
        type="button"
        onClick={() => setConfirming(true)}
        className="rounded-md px-2.5 py-1 text-[11px] font-medium"
        style={{ border: '1px solid var(--cc-line)', color: 'var(--cc-err)', background: 'var(--cc-surface)' }}
      >
        Delete
      </button>
    )
  }
  return (
    <span className="flex items-center gap-1">
      <button
        type="button"
        onClick={onDelete}
        className="rounded-md px-2.5 py-1 text-[11px] font-semibold"
        style={{ border: '1px solid var(--cc-err)', color: 'var(--cc-err)', background: 'var(--cc-surface)' }}
      >
        Confirm delete?
      </button>
      <button
        type="button"
        onClick={() => setConfirming(false)}
        className="rounded-md px-2.5 py-1 text-[11px] font-medium"
        style={{ border: '1px solid var(--cc-line)', color: 'var(--cc-ink-2)', background: 'var(--cc-surface)' }}
      >
        Cancel
      </button>
    </span>
  )
}

export function BoardAudioForm({ initial, assets, submitting, onSubmit }: {
  initial: BoardAudio | null
  assets: AssetRow[]
  submitting: boolean
  onSubmit: (payload: { asset_ref: string; gain_db: number }) => void
}) {
  const [assetRef, setAssetRef] = useState(initial?.asset_ref ?? '')
  const [gainDb, setGainDb] = useState(initial?.gain_db ?? 0)
  const audioAssets = assets.filter((asset) => asset.file_path && asset.codec_audio && (asset.duration_seconds ?? 0) > 0)
  const currentMissing = initial && !audioAssets.some((asset) => asset.asset_id === initial.asset_ref)

  return (
    <form className="mt-3 grid gap-3" onSubmit={(event) => {
      event.preventDefault()
      if (assetRef) onSubmit({ asset_ref: assetRef, gain_db: gainDb })
    }}>
      <label className="grid gap-1 text-xs">
        <span>Audio track</span>
        <select aria-label="Board audio track" value={assetRef} onChange={(event) => setAssetRef(event.target.value)} className="rounded-md px-2 py-1.5" style={fieldStyle}>
          <option value="">Select an uploaded audio track…</option>
          {currentMissing && <option value={initial.asset_ref}>Unavailable asset: {initial.asset_ref}</option>}
          {audioAssets.map((asset) => <option key={asset.asset_id} value={asset.asset_id}>{asset.title}</option>)}
        </select>
      </label>
      <label className="grid gap-1 text-xs">
        <span>Board audio level: {gainDb} dB</span>
        <input aria-label="Board audio level" type="range" min="-36" max="0" step="1" value={gainDb} onChange={(event) => setGainDb(Number(event.target.value))} />
      </label>
      <PrimaryButton label={initial ? 'Save audio changes' : 'Set board audio'} disabled={submitting || !assetRef} onClick={() => onSubmit({ asset_ref: assetRef, gain_db: gainDb })} />
    </form>
  )
}

// --- Board create -----------------------------------------------------------

export function BoardCreateForm({
  submitting,
  onSubmit,
}: {
  submitting: boolean
  onSubmit: (payload: { template_id: string }) => void
}) {
  const [templateId, setTemplateId] = useState<string>(TEMPLATE_OPTIONS[0].id)
  return (
    <div className="grid gap-2 rounded-md p-3" style={insetStyle}>
      <div className="text-xs font-semibold">Create a CG board for this channel</div>
      <label className="grid gap-1 text-xs">
        <span style={{ color: 'var(--cc-ink-3)' }}>Template</span>
        <select
          aria-label="Template"
          value={templateId}
          onChange={(e) => setTemplateId(e.target.value)}
          className="rounded-md px-2 py-1.5 text-sm"
          style={fieldStyle}
        >
          {TEMPLATE_OPTIONS.map((t) => (
            <option key={t.id} value={t.id}>
              {t.label}
            </option>
          ))}
        </select>
      </label>
      <div>
        <PrimaryButton
          label={submitting ? 'Creating…' : 'Create board'}
          disabled={submitting}
          onClick={() => onSubmit({ template_id: templateId })}
        />
      </div>
    </div>
  )
}

// --- Zone form --------------------------------------------------------------

export function ZoneForm({
  feeds,
  liveSources = [],
  stationLogoPresent = false,
  submitting,
  onSubmit,
  initial,
}: {
  feeds: CgFeedSource[]
  liveSources?: Pick<LiveSourceResponse, 'live_source_id' | 'channel_id' | 'name' | 'source_type'>[]
  // Issue #348: whether this channel already has a station logo uploaded.
  stationLogoPresent?: boolean
  submitting: boolean
  onSubmit: (payload: ZoneInput) => void
  initial?: CgZoneConfig
}) {
  const [region, setRegion] = useState<ZoneInput['region']>(initial?.region ?? 'lower')
  const [zoneKind, setZoneKind] = useState<ZoneInput['zone_kind']>(initial?.zone_kind ?? 'ticker')
  const [contentSource, setContentSource] = useState<ZoneInput['content_source']>(
    initial?.content_source ?? 'manual',
  )
  const [feedSourceId, setFeedSourceId] = useState<string>(initial?.feed_source_id ?? '')
  const [liveSourceId, setLiveSourceId] = useState<string>(initial?.live_source_id ?? '')
  const [manualText, setManualText] = useState<string>(initial?.manual_text ?? '')
  const [imageAssetRef, setImageAssetRef] = useState<string>(initial?.image_asset_ref ?? '')
  const [approvalRequired, setApprovalRequired] = useState<boolean>(initial?.approval_required ?? false)
  const [allowedTags, setAllowedTags] = useState<string>(formatTags(initial?.allowed_tags))

  const needsFeed = zoneNeedsFeed(contentSource)
  const feedMissing = needsFeed && !feedSourceId
  const liveSourceMissing = contentSource === 'live_video' && !liveSourceId
  const disabled = submitting || feedMissing || liveSourceMissing

  return (
    <div className="grid gap-2 rounded-md p-3" style={insetStyle}>
      <div className="grid gap-2 sm:grid-cols-2">
        <label className="grid gap-1 text-xs">
          <span style={{ color: 'var(--cc-ink-3)' }}>Region</span>
          <select aria-label="Region" value={region} onChange={(e) => setRegion(e.target.value as ZoneInput['region'])} className="rounded-md px-2 py-1.5" style={fieldStyle}>
            {REGIONS.map((r) => (
              <option key={r} value={r}>
                {humanize(r)}
              </option>
            ))}
          </select>
        </label>
        <label className="grid gap-1 text-xs">
          <span style={{ color: 'var(--cc-ink-3)' }}>Zone kind</span>
          <select aria-label="Zone kind" value={zoneKind} onChange={(e) => setZoneKind(e.target.value as ZoneInput['zone_kind'])} className="rounded-md px-2 py-1.5" style={fieldStyle}>
            {ZONE_KINDS.map((k) => (
              <option key={k} value={k} disabled={k === 'audio'}>
                {k === 'audio'
                  ? 'Board audio is configured below the Board card'
                  : humanize(k)}
              </option>
            ))}
          </select>
        </label>
        <label className="grid gap-1 text-xs">
          <span style={{ color: 'var(--cc-ink-3)' }}>Content source</span>
          <select aria-label="Content source" value={contentSource} onChange={(e) => setContentSource(e.target.value as ZoneInput['content_source'])} className="rounded-md px-2 py-1.5" style={fieldStyle}>
            {CONTENT_SOURCES.map((s) => (
              <option key={s} value={s}>
                {humanize(s)}
              </option>
            ))}
          </select>
        </label>
        {contentSource === 'live_video' && (
          <label className="grid gap-1 text-xs sm:col-span-2">
            <span>Live video in a zone</span>
            <select aria-label="Live video source" value={liveSourceId} onChange={(event) => setLiveSourceId(event.target.value)} className="rounded-md px-2 py-1.5" style={fieldStyle}>
              <option value="">Select a configured live source…</option>
              {liveSourceId && !liveSources.some((source) => source.live_source_id === liveSourceId) && <option value={liveSourceId}>Unavailable source: {liveSourceId}</option>}
              {liveSources.map((source) => <option key={source.live_source_id} value={source.live_source_id}>{source.name}</option>)}
            </select>
          </label>
        )}
        {needsFeed && (
          <label className="grid gap-1 text-xs">
            <span style={{ color: 'var(--cc-ink-3)' }}>Feed source</span>
            <select aria-label="Feed source" value={feedSourceId} onChange={(e) => setFeedSourceId(e.target.value)} className="rounded-md px-2 py-1.5" style={fieldStyle}>
              <option value="">Select a feed…</option>
              {feeds.map((f) => (
                <option key={f.feed_source_id} value={f.feed_source_id}>
                  {f.label}
                </option>
              ))}
            </select>
          </label>
        )}
      </div>
      {contentSource === 'manual' && (
        <label className="grid gap-1 text-xs">
          <span style={{ color: 'var(--cc-ink-3)' }}>Manual text</span>
          <textarea value={manualText} onChange={(e) => setManualText(e.target.value)} rows={2} className="rounded-md px-2 py-1.5 text-sm" style={fieldStyle} />
        </label>
      )}
      {contentSource === 'image' && (
        <label className="grid gap-1 text-xs">
          <span style={{ color: 'var(--cc-ink-3)' }}>Image</span>
          <select
            aria-label="Zone image"
            value={imageAssetRef}
            onChange={(e) => setImageAssetRef(e.target.value)}
            className="rounded-md px-2 py-1.5"
            style={fieldStyle}
          >
            <option value="">Choose an image…</option>
            <option value="station-logo">Station logo</option>
          </select>
          {imageAssetRef === 'station-logo' && !stationLogoPresent && (
            <span style={{ color: 'var(--cc-err)' }}>
              No station logo has been uploaded yet. Upload one on the Channel Ops screen first; until
              then this zone shows nothing.
            </span>
          )}
        </label>
      )}
      <label className="flex items-center gap-2 text-xs">
        <input type="checkbox" checked={approvalRequired} onChange={(e) => setApprovalRequired(e.target.checked)} />
        <span>Require operator approval of feed items before they show</span>
      </label>
      <label className="grid gap-1 text-xs">
        <span style={{ color: 'var(--cc-ink-3)' }}>Allowed tags (comma-separated; empty = show all)</span>
        <input
          aria-label="Allowed tags"
          type="text"
          placeholder="events, alerts"
          value={allowedTags}
          onChange={(e) => setAllowedTags(e.target.value)}
          className="rounded-md px-2 py-1.5 text-sm"
          style={fieldStyle}
        />
      </label>
      {feedMissing && (
        <span className="text-xs" style={{ color: 'var(--cc-err)' }}>
          A feed-sourced zone must name a feed.
        </span>
      )}
      {liveSourceMissing && <span className="text-xs" style={{ color: 'var(--cc-err)' }}>Select a live source for this zone.</span>}
      <div>
        <PrimaryButton
          label={submitting ? 'Saving…' : initial ? 'Save changes' : 'Add zone'}
          disabled={disabled}
          onClick={() =>
            onSubmit({
              region,
              zone_kind: zoneKind,
              content_source: contentSource,
              feed_source_id: needsFeed ? feedSourceId : null,
              live_source_id: contentSource === 'live_video' ? liveSourceId : null,
              approval_required: approvalRequired,
              manual_text: contentSource === 'manual' ? manualText.trim() || null : null,
              image_asset_ref:
                contentSource === 'image' ? imageAssetRef.trim() || null : null,
              allowed_tags: parseTags(allowedTags),
            })
          }
        />
      </div>
    </div>
  )
}

// --- Feed form --------------------------------------------------------------

export function FeedForm({
  submitting,
  onSubmit,
  initial,
}: {
  submitting: boolean
  onSubmit: (payload: FeedInput) => void
  initial?: CgFeedSource
}) {
  const [kind, setKind] = useState<FeedInput['kind']>(initial?.kind ?? 'rss')
  const [label, setLabel] = useState(initial?.label ?? '')
  const [sourceUrl, setSourceUrl] = useState(initial?.source_url ?? '')
  const [trustTier, setTrustTier] = useState<FeedInput['trust_tier']>(initial?.trust_tier ?? 'operator_curated')
  const [refreshMinutes, setRefreshMinutes] = useState(String(Math.round((initial?.refresh_seconds ?? 900) / 60)))
  const [enabled, setEnabled] = useState(initial?.enabled ?? true)
  const [tags, setTags] = useState<string>(formatTags(initial?.tags))

  const refreshSeconds = Math.round(Number(refreshMinutes) * 60)
  const refreshValid = Number.isFinite(refreshSeconds) && refreshSeconds > 0 && refreshSeconds <= 86400
  const weatherPublic = kind === 'weather' && trustTier === 'public_permitted'
  const disabled = submitting || !label.trim() || !sourceUrl.trim() || !refreshValid || weatherPublic

  return (
    <div className="grid gap-2 rounded-md p-3" style={insetStyle}>
      <div className="grid gap-2 sm:grid-cols-2">
        <label className="grid gap-1 text-xs">
          <span style={{ color: 'var(--cc-ink-3)' }}>Feed kind</span>
          <select aria-label="Feed kind" value={kind} onChange={(e) => setKind(e.target.value as FeedInput['kind'])} disabled={initial !== undefined} className="rounded-md px-2 py-1.5" style={fieldStyle}>
            {FEED_KINDS.map((k) => (
              <option key={k} value={k}>
                {humanize(k)}
              </option>
            ))}
          </select>
        </label>
        <label className="grid gap-1 text-xs">
          <span style={{ color: 'var(--cc-ink-3)' }}>Trust tier</span>
          <select aria-label="Trust tier" value={trustTier} onChange={(e) => setTrustTier(e.target.value as FeedInput['trust_tier'])} className="rounded-md px-2 py-1.5" style={fieldStyle}>
            {TRUST_TIERS.map((t) => (
              <option key={t} value={t}>
                {humanize(t)}
              </option>
            ))}
          </select>
        </label>
      </div>
      <label className="grid gap-1 text-xs">
        <span style={{ color: 'var(--cc-ink-3)' }}>Label</span>
        <input type="text" aria-label="Feed label" placeholder="Community news" value={label} onChange={(e) => setLabel(e.target.value)} className="rounded-md px-2 py-1.5 text-sm" style={fieldStyle} />
      </label>
      <label className="grid gap-1 text-xs">
        <span style={{ color: 'var(--cc-ink-3)' }}>Source URL</span>
        <input type="text" aria-label="Source URL" placeholder="https://example.gov/news.rss" value={sourceUrl} onChange={(e) => setSourceUrl(e.target.value)} className="rounded-md px-2 py-1.5 text-sm" style={fieldStyle} />
      </label>
      <div className="grid gap-2 sm:grid-cols-2">
        <label className="grid gap-1 text-xs">
          <span style={{ color: 'var(--cc-ink-3)' }}>Refresh (minutes)</span>
          <input aria-label="Refresh (minutes)" value={refreshMinutes} inputMode="numeric" onChange={(e) => setRefreshMinutes(e.target.value)} className="rounded-md px-2 py-1.5" style={fieldStyle} />
        </label>
        <label className="flex items-center gap-2 text-xs">
          <input type="checkbox" checked={enabled} onChange={(e) => setEnabled(e.target.checked)} />
          <span>Enabled</span>
        </label>
      </div>
      <label className="grid gap-1 text-xs">
        <span style={{ color: 'var(--cc-ink-3)' }}>Tags (comma-separated; stamped onto this feed's items)</span>
        <input
          aria-label="Feed tags"
          type="text"
          placeholder="events, community"
          value={tags}
          onChange={(e) => setTags(e.target.value)}
          className="rounded-md px-2 py-1.5 text-sm"
          style={fieldStyle}
        />
      </label>
      {weatherPublic && (
        <span className="text-xs" style={{ color: 'var(--cc-err)' }}>
          Weather feeds must be operator or partner curated (not public).
        </span>
      )}
      <div>
        <PrimaryButton
          label={submitting ? 'Saving…' : initial ? 'Save changes' : 'Register feed'}
          disabled={disabled}
          onClick={() =>
            onSubmit({
              kind,
              label: label.trim(),
              source_url: sourceUrl.trim(),
              trust_tier: trustTier,
              refresh_seconds: refreshSeconds,
              enabled,
              tags: parseTags(tags),
            })
          }
        />
      </div>
    </div>
  )
}

// --- Preview ----------------------------------------------------------------

export function BoardPreviewPanel({ resolved }: { resolved: ResolvedBoard }) {
  const degraded = new Set(resolved.degraded_zone_ids ?? [])
  const backfilled = resolved.backfilled_kinds ?? []
  return (
    <section className="rounded-md p-4" style={panelStyle} aria-label="Live preview">
      <h2 className="m-0 text-base font-semibold">Live preview</h2>
      {backfilled.length > 0 && (
        <div className="mt-2 rounded-md p-2 text-xs" style={{ background: 'var(--cc-warn-soft)', color: 'var(--cc-warn)' }}>
          Using defaults for unconfigured zones: {backfilled.map(humanize).join(', ')}.
        </div>
      )}
      <div className="mt-3 grid gap-2">
        {resolved.snapshot.zones.map((zone) => (
          <div key={zone.zone_id} className="rounded-md p-2 text-sm" style={insetStyle}>
            <div className="flex items-center justify-between gap-2">
              <span className="font-semibold">{zone.title ?? humanize(zone.kind)}</span>
              <span className="cc-mono rounded-full px-1.5 py-0.5 text-[10px]" style={{ background: 'var(--cc-surface-3)', color: 'var(--cc-ink-2)' }}>
                {zone.kind} / {zone.source}
              </span>
            </div>
            {degraded.has(zone.zone_id) && (
              <div className="mt-1 text-[11px]" style={{ color: 'var(--cc-warn)' }}>
                Feed unavailable — this zone is empty until its feed is restored.
              </div>
            )}
          </div>
        ))}
      </div>
    </section>
  )
}

// --- Container --------------------------------------------------------------

function useNoneOn404<T>(key: unknown[], fn: () => Promise<T>, enabled = true) {
  return useQuery({
    queryKey: key,
    queryFn: async () => {
      try {
        return await fn()
      } catch (err) {
        if (err instanceof ApiError && err.status === 404) return null
        throw err
      }
    },
    enabled,
    refetchInterval: POLL_MS,
    retry: false,
  })
}

export function CgBoardDesignerScreen() {
  const queryClient = useQueryClient()
  const [channelId, setChannelId] = useState('public')
  const [showAddZone, setShowAddZone] = useState(false)
  const [editingZoneId, setEditingZoneId] = useState<string | null>(null)
  const [showAddFeed, setShowAddFeed] = useState(false)
  const [editingFeedId, setEditingFeedId] = useState<string | null>(null)
  const [reviewingFeedId, setReviewingFeedId] = useState<string | null>(null)
  const [actionError, setActionError] = useState<string | null>(null)

  const appConfigQuery = useQuery({ queryKey: ['app-platform-config'], queryFn: getAppPlatformConfig, refetchInterval: POLL_MS })
  const identityQuery = useQuery({ queryKey: ['staff-identity'], queryFn: getStaffIdentity, retry: false })
  const canWrite =
    identityQuery.isSuccess &&
    (hasOperatorRole(identityQuery.data, 'publish_operator') || hasOperatorRole(identityQuery.data, 'setup_admin'))
  const canRead = canWrite || (identityQuery.isSuccess && hasOperatorRole(identityQuery.data, 'support_admin'))

  const boardQuery = useNoneOn404<BoardView | null>(['cg-board', channelId], () => getCgBoard(channelId), canRead)
  const board = boardQuery.data ?? null
  const audioQuery = useQuery({ queryKey: ['cg-board-audio', channelId], queryFn: () => getCgBoardAudio(channelId), enabled: canRead && board != null, retry: false })
  const assetsQuery = useQuery({ queryKey: ['staff-assets'], queryFn: listStaffAssets, enabled: canWrite && board != null, retry: false })
  const liveSourcesQuery = useQuery({ queryKey: ['live-sources'], queryFn: listLiveSources, enabled: canRead && board != null, retry: false })
  // Issue #348: a logo zone can show the channel's uploaded station logo. The
  // designer only needs to know whether one is uploaded, so the operator is
  // told before they pick it.
  const stationLogoQuery = useQuery({
    queryKey: ['graphics-overlay', channelId],
    queryFn: () => getGraphicsOverlay(channelId),
    enabled: canRead && board != null,
    retry: false,
  })
  const stationLogoPresent = stationLogoQuery.data?.graphics_overlay_logo_present ?? false
  const previewQuery = useNoneOn404<ResolvedBoard | null>(['cg-board-preview', channelId], () => previewCgBoard(channelId), canRead && board != null)
  const auditQuery = useQuery({
    queryKey: ['cg-board-audit', channelId],
    queryFn: () => listCgBoardAudit(channelId, { limit: 20 }),
    enabled: canRead && board != null,
    refetchInterval: POLL_MS,
    retry: false,
  })

  const refresh = () => {
    void queryClient.invalidateQueries({ queryKey: ['cg-board', channelId] })
    void queryClient.invalidateQueries({ queryKey: ['cg-board-preview', channelId] })
    void queryClient.invalidateQueries({ queryKey: ['cg-board-audit', channelId] })
    void queryClient.invalidateQueries({ queryKey: ['cg-board-audio', channelId] })
  }
  const onError = (fallback: string) => (err: unknown) => setActionError(apiMessage(err, fallback))
  const onOk = (after?: () => void) => () => {
    setActionError(null)
    after?.()
    refresh()
  }

  const createBoard = useMutation({ mutationFn: (p: { template_id: string }) => createCgBoard(channelId, p), onSuccess: onOk(), onError: onError('Could not create the board.') })
  const updateBoard = useMutation({ mutationFn: (p: { template_id?: string; active?: boolean }) => updateCgBoard(channelId, p), onSuccess: onOk(), onError: onError('Could not update the board.') })
  const addZone = useMutation({ mutationFn: (p: ZoneInput) => addCgZone(channelId, p), onSuccess: onOk(() => setShowAddZone(false)), onError: onError('Could not add the zone.') })
  const updateZone = useMutation({ mutationFn: ({ id, p }: { id: string; p: ZoneInput }) => updateCgZone(channelId, id, p), onSuccess: onOk(() => setEditingZoneId(null)), onError: onError('Could not update the zone.') })
  const deleteZone = useMutation({ mutationFn: (id: string) => deleteCgZone(channelId, id), onSuccess: onOk(), onError: onError('Could not delete the zone.') })
  const addFeed = useMutation({ mutationFn: (p: FeedInput) => addCgFeed(channelId, p), onSuccess: onOk(() => setShowAddFeed(false)), onError: onError('Could not register the feed.') })
  const updateFeed = useMutation({ mutationFn: ({ id, p }: { id: string; p: FeedUpdateInput }) => updateCgFeed(channelId, id, p), onSuccess: onOk(() => setEditingFeedId(null)), onError: onError('Could not update the feed.') })
  const deleteFeed = useMutation({ mutationFn: (id: string) => deleteCgFeed(channelId, id), onSuccess: onOk(), onError: onError('Could not delete the feed.') })
  const setAudio = useMutation({ mutationFn: (payload: { asset_ref: string; gain_db: number }) => setCgBoardAudio(channelId, payload), onSuccess: onOk(), onError: onError('Could not save the board audio.') })
  const deleteAudio = useMutation({ mutationFn: () => deleteCgBoardAudio(channelId), onSuccess: onOk(), onError: onError('Could not remove the board audio.') })

  const channels = appConfigQuery.data?.channels ?? []
  const feeds = board?.feeds ?? []
  const liveSources = (liveSourcesQuery.data ?? []).filter(
    (source) => source.channel_id === channelId && source.source_type !== 'ndi',
  )
  const zones = board?.zones ?? []
  const busy =
    createBoard.isPending || updateBoard.isPending || addZone.isPending || updateZone.isPending ||
    deleteZone.isPending || addFeed.isPending || updateFeed.isPending || deleteFeed.isPending ||
    setAudio.isPending || deleteAudio.isPending

  return (
    <div className="grid min-w-0 gap-5 overflow-x-hidden px-4 py-5 sm:px-6">
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="m-0 text-2xl font-semibold tracking-tight">CG Board Designer</h1>
          <p className="m-0 mt-1 max-w-3xl text-sm" style={{ color: 'var(--cc-ink-2)' }}>
            Configure the durable bulletin board: template, zones, feed sources, and a live preview. The
            engine composites this board into the channel.
          </p>
        </div>
        <label className="grid gap-1 text-sm" htmlFor="cg-designer-channel">
          <span className="font-semibold">Channel</span>
          <select
            id="cg-designer-channel"
            value={channelId}
            onChange={(e) => {
              setChannelId(e.target.value)
              setEditingZoneId(null)
              setEditingFeedId(null)
            }}
            className="rounded-md px-3 py-2 text-sm"
            style={fieldStyle}
          >
            <option value="public">Public board</option>
            {channels.map((c) => (
              <option key={c.channel_id} value={c.channel_id}>
                {c.branding.display_name}
              </option>
            ))}
          </select>
        </label>
      </header>

      {!identityQuery.isSuccess && !identityQuery.isError && (
        <div className="text-xs" style={{ color: 'var(--cc-ink-3)' }}>Loading…</div>
      )}
      {identityQuery.isError && (
        <div role="alert" className="rounded-md p-3 text-sm" style={{ background: 'var(--cc-err-soft)', color: 'var(--cc-err)' }}>
          Could not verify your access — {apiMessage(identityQuery.error, 'identity check failed')}.
        </div>
      )}
      {!canRead && identityQuery.isSuccess && <AccessNote what="the CG board designer" />}
      {boardQuery.isError && (
        <div role="alert" className="rounded-md p-3 text-sm" style={{ background: 'var(--cc-err-soft)', color: 'var(--cc-err)' }}>
          {apiMessage(boardQuery.error, 'Could not load the board.')}
        </div>
      )}
      {actionError && (
        <div role="alert" className="rounded-md p-3 text-sm" style={{ background: 'var(--cc-err-soft)', color: 'var(--cc-err)' }}>
          {actionError}
        </div>
      )}

      {canRead && (
        <div className="grid min-w-0 gap-4 xl:grid-cols-[1fr_0.8fr]">
          <div className="grid min-w-0 content-start gap-4">
            {/* Board */}
            <section className="rounded-md p-4" style={panelStyle} aria-label="Board">
              <h2 className="m-0 text-base font-semibold">Board</h2>
              {boardQuery.isLoading && <div className="mt-2 text-xs" style={{ color: 'var(--cc-ink-3)' }}>Loading…</div>}
              {boardQuery.isSuccess && board == null && (
                canWrite ? (
                  <div className="mt-3">
                    <BoardCreateForm submitting={createBoard.isPending} onSubmit={(p) => createBoard.mutate(p)} />
                  </div>
                ) : (
                  <EmptyState
                    headline="No board on this channel yet."
                    body="The board designer controls what the community board looks like on air — its zones, feeds, and styling. A station admin creates the board for this channel; once created, it appears here."
                  />
                )
              )}
              {board != null && (
                <div className="mt-3 grid gap-2 text-sm">
                  <div className="cc-mono text-[11px]" style={{ color: 'var(--cc-ink-3)' }}>{board.board.board_id}</div>
                  {canWrite ? (
                    <label className="grid gap-1 text-xs">
                      <span style={{ color: 'var(--cc-ink-3)' }}>Template</span>
                      <select
                        aria-label="Board template"
                        value={board.board.template_id}
                        onChange={(e) => updateBoard.mutate({ template_id: e.target.value })}
                        className="rounded-md px-2 py-1.5"
                        style={fieldStyle}
                      >
                        {TEMPLATE_OPTIONS.map((t) => (
                          <option key={t.id} value={t.id}>
                            {t.label}
                          </option>
                        ))}
                      </select>
                    </label>
                  ) : (
                    <div>Template: {board.board.template_id}</div>
                  )}
                </div>
              )}
            </section>

            {board != null && (
              <section className="rounded-md p-4" style={panelStyle} aria-label="Board audio">
                <h2 className="m-0 text-base font-semibold">Board background audio</h2>
                <p className="mt-1 text-xs" style={{ color: 'var(--cc-ink-2)' }}>
                  Choose an uploaded track to play when this board fills a gap between programs. Audio is normalized and the level can be reduced below that target.
                </p>
                {audioQuery.isLoading && <div className="text-xs">Loading board audio…</div>}
                {audioQuery.isError && <div role="alert" className="text-xs">{apiMessage(audioQuery.error, 'Could not load board audio.')}</div>}
                {canWrite && assetsQuery.isError && <div role="alert" className="text-xs">{apiMessage(assetsQuery.error, 'Could not load audio tracks.')}</div>}
                {canWrite && audioQuery.isSuccess && assetsQuery.isSuccess && (
                  <BoardAudioForm
                    key={`${channelId}:${audioQuery.data?.asset_ref ?? ''}:${audioQuery.data?.gain_db ?? ''}`}
                    initial={audioQuery.data}
                    assets={assetsQuery.data}
                    submitting={setAudio.isPending}
                    onSubmit={(payload) => setAudio.mutate(payload)}
                  />
                )}
                {!canWrite && audioQuery.isSuccess && <div className="text-sm">{audioQuery.data ? `Track: ${audioQuery.data.asset_ref}, level ${audioQuery.data.gain_db} dB` : 'No background audio selected.'}</div>}
                {canWrite && audioQuery.data && <div className="mt-3"><ConfirmDeleteButton onDelete={() => deleteAudio.mutate()} /></div>}
              </section>
            )}

            {/* Zones */}
            {board != null && (
              <section className="rounded-md p-4" style={panelStyle} aria-label="Zones">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <h2 className="m-0 text-base font-semibold">Zones</h2>
                  {canWrite && (
                    <button type="button" onClick={() => setShowAddZone((v) => !v)} className="rounded-md px-2.5 py-1 text-xs font-semibold" style={{ background: 'var(--cc-brand)', color: 'var(--cc-brand-ink)' }}>
                      {showAddZone ? 'Close' : 'Add zone'}
                    </button>
                  )}
                </div>
                {showAddZone && canWrite && (
                  <div className="mt-3">
                    <ZoneForm feeds={feeds} liveSources={liveSources} stationLogoPresent={stationLogoPresent} submitting={addZone.isPending} onSubmit={(p) => addZone.mutate(p)} />
                  </div>
                )}
                {liveSourcesQuery.isError && (
                  <div role="alert" className="mt-2 text-xs" style={{ color: 'var(--cc-err)' }}>
                    {apiMessage(liveSourcesQuery.error, 'Could not load configured live video sources.')}
                  </div>
                )}
                <div className="mt-3 grid gap-2">
                  {zones.length === 0 && <div className="text-xs" style={{ color: 'var(--cc-ink-3)' }}>No zones yet.</div>}
                  {zones.map((zone) => (
                    <div key={zone.zone_id} className="rounded-md p-3 text-sm" style={insetStyle}>
                      <div className="flex flex-wrap items-center justify-between gap-2">
                        <span>{zoneSummary(zone)}</span>
                        {canWrite && (
                          <div className="flex items-center gap-2">
                            <button type="button" onClick={() => setEditingZoneId((id) => (id === zone.zone_id ? null : zone.zone_id))} className="rounded-md px-2.5 py-1 text-[11px] font-medium" style={{ border: '1px solid var(--cc-line)', color: 'var(--cc-ink-2)', background: 'var(--cc-surface)' }}>
                              {editingZoneId === zone.zone_id ? 'Close' : 'Edit'}
                            </button>
                            <ConfirmDeleteButton onDelete={() => deleteZone.mutate(zone.zone_id)} />
                          </div>
                        )}
                      </div>
                      {editingZoneId === zone.zone_id && canWrite && (
                        <div className="mt-2">
                          <ZoneForm feeds={feeds} liveSources={liveSources} stationLogoPresent={stationLogoPresent} submitting={updateZone.isPending} initial={zone} onSubmit={(p) => updateZone.mutate({ id: zone.zone_id, p })} />
                        </div>
                      )}
                    </div>
                  ))}
                </div>
              </section>
            )}

            {/* Feeds */}
            {board != null && (
              <section className="rounded-md p-4" style={panelStyle} aria-label="Feed sources">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <h2 className="m-0 text-base font-semibold">Feed sources</h2>
                  {canWrite && (
                    <button type="button" onClick={() => setShowAddFeed((v) => !v)} className="rounded-md px-2.5 py-1 text-xs font-semibold" style={{ background: 'var(--cc-brand)', color: 'var(--cc-brand-ink)' }}>
                      {showAddFeed ? 'Close' : 'Add feed'}
                    </button>
                  )}
                </div>
                {showAddFeed && canWrite && (
                  <div className="mt-3">
                    <FeedForm submitting={addFeed.isPending} onSubmit={(p) => addFeed.mutate(p)} />
                  </div>
                )}
                <div className="mt-3 grid gap-2">
                  {feeds.length === 0 && <div className="text-xs" style={{ color: 'var(--cc-ink-3)' }}>No feeds registered.</div>}
                  {feeds.map((feed) => (
                    <div key={feed.feed_source_id} className="rounded-md p-3 text-sm" style={insetStyle}>
                      <div className="flex flex-wrap items-center justify-between gap-2">
                        <span className="font-semibold">{feed.label}</span>
                        <span className="cc-mono rounded-full px-1.5 py-0.5 text-[10px]" style={{ background: 'var(--cc-surface-3)', color: 'var(--cc-ink-2)' }}>
                          {feed.kind} / {humanize(feed.trust_tier)}
                        </span>
                      </div>
                      <div className="mt-1 text-[11px]" style={{ color: feed.last_fetch_error ? 'var(--cc-err)' : 'var(--cc-ink-3)' }}>
                        {feedFetchStatus(feed)}
                      </div>
                      {canWrite && (
                        <div className="mt-2 flex items-center gap-2">
                          <button type="button" onClick={() => setEditingFeedId((id) => (id === feed.feed_source_id ? null : feed.feed_source_id))} className="rounded-md px-2.5 py-1 text-[11px] font-medium" style={{ border: '1px solid var(--cc-line)', color: 'var(--cc-ink-2)', background: 'var(--cc-surface)' }}>
                            {editingFeedId === feed.feed_source_id ? 'Close' : 'Edit'}
                          </button>
                          <ConfirmDeleteButton onDelete={() => deleteFeed.mutate(feed.feed_source_id)} />
                        </div>
                      )}
                      {editingFeedId === feed.feed_source_id && canWrite && (
                        <div className="mt-2">
                          <FeedForm
                            submitting={updateFeed.isPending}
                            initial={feed}
                            onSubmit={(p) => updateFeed.mutate({
                              id: feed.feed_source_id,
                              p: {
                                label: p.label,
                                source_url: p.source_url,
                                trust_tier: p.trust_tier,
                                refresh_seconds: p.refresh_seconds,
                                enabled: p.enabled,
                                tags: p.tags,
                              },
                            })}
                          />
                        </div>
                      )}
                      {canRead && (
                        <div className="mt-2">
                          <button type="button" onClick={() => setReviewingFeedId((id) => (id === feed.feed_source_id ? null : feed.feed_source_id))} className="rounded-md px-2.5 py-1 text-[11px] font-medium" style={{ border: '1px solid var(--cc-line)', color: 'var(--cc-ink-2)', background: 'var(--cc-surface)' }}>
                            {reviewingFeedId === feed.feed_source_id ? 'Hide items' : 'Review items'}
                          </button>
                          {reviewingFeedId === feed.feed_source_id && (
                            <div className="mt-2">
                              <FeedApprovalQueue channelId={channelId} feedSourceId={feed.feed_source_id} canApprove={canWrite} />
                            </div>
                          )}
                        </div>
                      )}
                    </div>
                  ))}
                </div>
              </section>
            )}
          </div>

          {/* Right column: preview + audit */}
          <div className="grid min-w-0 content-start gap-4">
            {board != null && previewQuery.data != null && <BoardPreviewPanel resolved={previewQuery.data} />}
            {board != null && (
              <section className="rounded-md p-4" style={panelStyle} aria-label="Board history">
                <h2 className="m-0 text-base font-semibold">Board history</h2>
                <div className="mt-3 grid gap-1">
                  {(auditQuery.data ?? []).length === 0 && <div className="text-xs" style={{ color: 'var(--cc-ink-3)' }}>No history yet.</div>}
                  {(auditQuery.data ?? []).map((event) => (
                    <div key={event.audit_id} className="text-xs" style={{ color: 'var(--cc-ink-2)' }}>
                      {auditSummary(event)}
                    </div>
                  ))}
                </div>
              </section>
            )}
          </div>
        </div>
      )}

      {busy && <div className="text-xs" style={{ color: 'var(--cc-ink-3)' }} aria-live="polite">Saving…</div>}
    </div>
  )
}
