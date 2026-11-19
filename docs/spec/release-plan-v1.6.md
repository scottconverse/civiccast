Status: SUPERSEDED -- historical plan from the pre-reset "v1.6" numbering. The current release is v1.0.0-rc.6 (see docs/releases/v1.0.0-rc.6-verification.md); v1.0.0-rc18 belongs to the retired WSL2 line and its record is in the archived repository.

## Goal

Build the software pieces required for industry-standard channel operation and
connected TV reach, while leaving physical headend, SDI, DeckLink, Comcast, and
streaming-TV platform publication proof to partner validation.

## Required Outcomes

1. Linear channel profiles.
   - Support Public, Education, Government, and future community channel
     profiles.
   - Store or expose channel identity, branding, programming rules, output
     settings, and fallback behavior.

2. Schedule-to-playout workflow.
   - Represent live sources, file playback, slates, bulletin boards, reruns,
     and fallback blocks.
   - Expose now/next status per channel.
   - Make gaps, underruns, and source failure visible to the operator.

3. Channel proof logs.
   - Record what was scheduled, what actually played, and what failed over.
   - Export operator-readable and machine-readable proof logs.
   - Attach caption and sidecar references where available.

4. Software outputs.
   - Support feasible software outputs such as HLS, RTMP, SRT, and NDI command
     planning where licensing permits.
   - Avoid hardware compatibility claims until partner proof exists.

5. Reference CTV support.
   - Provide a stable public feed/API for live channels and VOD.
   - Build a shippable prototype that can inform later platform-specific apps.
   - Support live HLS, VOD playback, captions, station branding, stable content
     IDs, and browse/search by meeting, series, date, body, or topic.

## First Slice

The first v1.6 slice adds the software channel and CTV contract foundation:
default PEG-style channel profiles, public and staff channel lists, public and
staff now/next projections, operator proof logs, and a stable public CTV feed.
This slice does not claim live headend delivery or streaming-TV platform
publication.

## Exit Criteria

- At least one end-to-end channel schedule can play through live/file/slate or
  equivalent software states.
- Channel now/next and proof logs are visible to operators.
- Failure/fallback behavior is tested.
- Public feed/API can drive the reference CTV surface.
- Reference CTV app can browse and play at least live channel and VOD content
  from CivicCast test data.
- Documentation states exactly what is software-proven and what requires partner
  station hardware validation.
