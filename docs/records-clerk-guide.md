# CivicCast Records Clerk Guide

This guide is for the person who reviews captions, checks summaries, approves
records, and publishes the meeting replay.

## Your Job

You answer one question: **is the public record accurate enough to publish?**

Your main tasks are:

1. Review captions.
2. Review sourced summary claims.
3. Confirm signed-record metadata.
4. Approve archive and publish surfaces.
5. Keep notes when something is published with a limitation.

## Caption Review

CivicCast may produce **auto-generated captions** before a person has reviewed
them. Your station policy decides whether those captions can appear publicly
before review.

When reviewing captions:

- Compare the machine cue with the meeting audio.
- Edit words that change meaning, names, numbers, motions, votes, or public
  comment.
- Approve cues that are accurate enough.
- Reject or mark unclear sections when audio quality prevents a reliable edit.

If captions publish before review, the public surface should label them as
auto-generated.

### When captions actually appear

Approving publish makes the recording public straight away — residents and
the press can watch it before anyone has reviewed a single caption cue. That
is deliberate: a public record must not wait days on caption review.

**The recording is public immediately; captions attach after review according
to the station setting.** With **Wait for every selected language**, both
reviewed tracks appear together. With **Attach English after its review**,
reviewed English can attach while Spanish work continues. The caption queue is
therefore not a gate on publication, but it is a gate on *captions*.

CivicCast will not approve a publish it cannot queue a caption job for. If
approval fails with a message about the caption job, nothing was published —
fix what the message names and approve again.

### English and each selected translation are separate passes

A published recording carries an English caption track plus one track for each
selected translation language (Spanish, Chinese (Simplified), Tagalog /
Filipino, Vietnamese or Arabic). The review queue shows the language code on
every row and has a language filter above the list, because each track is
reviewed separately:

1. **English first.** Approve or edit the English cues as above. No captions are
   published yet.
2. **Translations next.** Once English is fully decided, CivicCast translates
   the cues you approved and files each translated language as its own set of rows, with
   the same approve / edit / reject choices. The Spanish text is machine
   output too, so it gets a real review — not a rubber stamp.

With **Wait for every selected language**, the recording's selected caption
tracks attach together; with **Attach English after its review**, reviewed
English can attach first. Two consequences worth knowing before you start:

- **Rejecting every Spanish cue does not complete the selected Spanish track.**
  If the station waits for every selected language, the caption job is held and
  says why; if English-first is selected, reviewed English can already be
  available while Spanish remains incomplete. Edit the Spanish rows with the
  correct wording instead — a rejected row can still be edited.
- **Rejecting every English cue does not finish the job either.** With no
  English there is nothing to translate and no track in either language, so
  the job is held the same way. Fix the English cues; if the audio is
  genuinely unusable, ask a technical admin to cancel the caption job rather
  than leaving it held.
- **A Spanish cue is never blocked on audio evidence.** It is a translation of
  text you already approved, not a guess about what someone said, so the
  low-confidence audio check that can gate an English cue does not apply.
- **Revising an English cue after the Spanish pass re-queues Spanish for that
  cue.** A fresh Spanish row appears for the corrected wording and waits for
  your approval; the old translation is left in the queue as a record but is
  never published. Only the cue you changed comes back — the rest of the
  Spanish pass stands. So a late English correction is always safe: the two
  languages cannot drift apart.

If Spanish rows never appear after you finish the English pass, the station's
translation model is missing or broken. The caption job records that on the
job, and a technical admin fixes it under **Settings → AI Models →
Translation** or with `civiccast doctor`.

## Summary Review

CivicCast summaries are sourced. A number, vote, dollar amount, date, or named
claim should link back to transcript evidence.

For each important claim:

1. Open the timestamp link.
2. Confirm the transcript supports the claim.
3. Edit the summary if the claim is unsupported or too strong.
4. Reject the claim if you cannot verify it.

If a number in the summary does not appear in the transcript, remove or rewrite
the claim before approval.

## Signed Records

A signed record is a CivicCast export with integrity metadata and approval
history. It is not automatically a jurisdiction-specific legal record; check
with your station's records officer or legal counsel for what your
jurisdiction requires beyond this export.

Before exporting:

- Confirm the meeting title, date, body, and agenda reference.
- Confirm approved captions and summary status match station policy.
- Confirm the signer or approver shown by CivicCast is correct.
- Keep the export with the station's retention records.

## Publish Approval

The publish dashboard may show many surfaces: resident portal, archive copies,
YouTube, podcast, subscriber notifications, cable packages, and optional
federation. Most records clerks should think in two levels:

- **Required for this asset:** the surfaces the station has configured and
  marked required for the asset. That can include the resident portal,
  Internet Archive, or a local archive destination. The required set depends
  on station configuration and the asset's public-record status; this guide
  does not determine legal retention or publication duties.
- **Optional:** additional reach surfaces such as YouTube, podcast,
  subscribers, or ActivityPub (fediverse sharing, e.g. Mastodon). Station
  policy decides which of these you use.

Do not infer that an unconfigured surface is optional or required. Follow your
station's records policy and ask an administrator which destinations are
required for the asset.

> **Before you treat an archive approval as the legal archive — check it is real.**
>
> On a new install, Internet Archive and local NAS run in a **simulated** mode
> until a technical admin turns on the real providers. Simulated means the
> surface will report success and show a target, but **nothing is uploaded and
> no file is written anywhere.**
>
> You can tell them apart on the publish dashboard. A simulated surface shows an
> amber **"Simulated — nothing was actually archived"** note, its message starts
> with **SIMULATED**, and its target reads
> `internet-archive.simulated.invalid/...` rather than a real `archive.org` link.
>
> A simulated result is not an upload or a file copy. If the station marks that
> surface required for this asset, its simulated result does not satisfy that
> requirement. Ask your admin to enable the real provider or storage path (Admin Guide → Provider Setup;
> they set `CIVICCAST_PROVIDER_INTERNET_ARCHIVE=real` and
> `CIVICCAST_PROVIDER_LOCAL_NAS=real` with the station's credentials), then
> publish again. Confirm the actual result at each destination that station
> policy requires.

**Meeting body tags.** Each published recording can carry a meeting-body
tag (set on the asset detail screen). The public portal's Browse filter and
meeting-body subscriptions are built from these values, so use one
canonical spelling per body and set the tag during the publish review. The
tag is a browse label only — it does not affect retention, approval, or the
signed record.

## When Something Is Wrong

| Situation | Records Action |
| --- | --- |
| Captions are incomplete | Follow station policy: publish video first, hold captions, or publish auto-generated captions with a label. |
| Summary claim is unsupported | Edit or reject the claim before approval. |
| Archive target is unavailable | Ask an admin whether the meeting can publish now or must wait for the required archive. |
| Subscriber notification fails | Publish the record if required surfaces are ready, then retry notification after the issue is fixed. |
| Wrong meeting metadata | Correct the metadata before signed-record export. |

**Logging a limitation.** When you publish with a limitation (auto-generated
captions not yet reviewed, an unresolved summary claim, a delayed archive
surface, and similar), keep a short note with the station's retention
records alongside the signed export: what was limited, why, and any
follow-up action taken or still pending.

## What Not To Include In Support Requests

Do not include bearer tokens, passwords, private keys, resident email
addresses, private meeting content, or raw provider credentials. Send a
redacted support report or the exact screen state instead.
