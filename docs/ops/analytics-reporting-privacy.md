# Analytics Reporting Privacy Model

CivicCast analytics are aggregate-only by default. The reporting layer is built for station operations, franchise reporting, and grant reporting without creating a resident tracking system.

## Retained Event Fields

The analytics store keeps only these fields:

- `event_id`
- `event_name`
- `occurred_at`
- `app_target`
- `channel_id`
- `content_id`
- selected aggregate-safe `properties`

The store does not retain anonymous session IDs, hashed viewer IDs, IP addresses, email addresses, phone numbers, names, tokens, or direct resident identifiers.

## Allowed Aggregate Properties

Only a closed allowlist of analytics properties is retained:

- `audio_track`
- `caption_language`
- `concurrent_viewers`
- `country`
- `device`
- `device_type`
- `download_count`
- `duration_seconds`
- `platform`
- `podcast_download`
- `position_seconds`
- `region`
- `state`
- `subscription_action`
- `view_seconds`

Unknown properties are dropped. Properties that look like direct viewer identifiers are rejected by the public analytics event contract before they reach storage.

## Reports

The staff analytics report aggregates retained events into:

- per-asset view time series
- live concurrent viewer trends
- country/state-level geography
- device and platform breakdowns
- caption and audio usage
- subscription growth counts
- podcast download counts

These reports do not require per-viewer sessions, per-IP tracking, cross-session identity, tracking pixels, or resident profiles.

## Optional GA4

GA4 integration is optional. CivicCast rejects GA4 configuration unless station analytics are enabled and a station privacy notice URL is configured. Stations using GA4 are responsible for ensuring their notice matches their deployment, retention settings, and local policy.

## EPG Export

The JSON EPG export and the TV Guide X-List style export are generated from the same schedule feed. That keeps app, CG, and guide exports aligned with the public schedule data already exposed by CivicCast.

## Privacy Boundary

The report API returns the privacy boundary string:

`aggregate-only-no-session-ip-or-viewer-identity`

Treat any proposed analytics expansion that would require session identity, per-IP storage, cross-session resident identity, or third-party tracker sharing as a new privacy review item before implementation.
