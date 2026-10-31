// SPDX-License-Identifier: Apache-2.0
// Copyright (c) The CivicCast Authors
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { getStaffIdentity, getAudioDescription, runAudioDescription, prepareAudioDescriptionPreview } from '../api/client'
import { hasRole } from './contribution-format'

export function AudioDescriptionPanel({ assetId }: { assetId: string }) {
  const cache = useQueryClient()
  const key = ['audio-description', assetId]
  const identity = useQuery({ queryKey: ['staff-identity'], queryFn: getStaffIdentity })
  const canWrite = hasRole(identity.data, ['setup_admin', 'meeting_operator', 'records_clerk', 'support_admin'])
  const job = useQuery({ queryKey: key, queryFn: () => getAudioDescription(assetId), retry: false,
    refetchInterval: (query) => ['queued', 'running'].includes(query.state.data?.state ?? '') ? 2500 : false })
  const action = useMutation({ mutationFn: (name: 'generate' | 'resume' | 'cancel') => runAudioDescription(assetId, name),
    onSuccess: () => cache.invalidateQueries({ queryKey: key }) })
  const preview = useMutation({ mutationFn: () => prepareAudioDescriptionPreview(assetId) })
  const active = ['queued', 'running'].includes(job.data?.state ?? '')
  const resumable = ['interrupted', 'failed', 'cancelled'].includes(job.data?.state ?? '')
  const error = action.error ?? job.error ?? preview.error
  const coverage = job.data?.coverage
  return <section aria-label="Audio description" className="grid gap-3 rounded-md border p-4">
    <h2 className="text-base font-semibold">Audio description</h2>
    <p className="text-sm">Describe information visible on slides and in the video that is missing from the dialogue. Processing and narration stay on this station. The original is preserved.</p>
    <p className="text-sm">Descriptions are generated automatically, with no description-approval queue. Published recordings offer original or described playback and optional pause-to-describe when dialogue leaves no room.</p>
    <p role="status">{job.isPending ? 'Loading description status…' : `Status: ${(job.data?.state ?? 'unavailable').replaceAll('_', ' ')}`}</p>
    {job.data?.message && <p role="status">{job.data.message}</p>}
    {coverage && <p>{coverage.in_gap} descriptions fit between dialogue; {coverage.requires_pause} need pause-to-describe; {coverage.uncertain_visuals} visual observations contain uncertainty.</p>}
    {error && <p role="alert">{error.message}</p>}
    <p className="text-xs">Long recordings can take substantial time. Background description inference waits at safe boundaries while a channel is on air. You can close this page while generation runs. English captions and the local narrator must be available. Automatic output is not a compliance certification.</p>
    {canWrite && <div className="flex flex-wrap gap-2">
      <button type="button" className="rounded border px-3 py-2" disabled={active || action.isPending || job.isPending || !!job.error}
        onClick={() => { preview.reset(); action.mutate('generate') }}>{job.data?.state === 'complete' ? 'Generate again' : 'Generate audio description'}</button>
      {resumable && <button type="button" className="rounded border px-3 py-2" disabled={action.isPending} onClick={() => action.mutate('resume')}>Resume generation</button>}
      {active && <button type="button" className="rounded border px-3 py-2" disabled={action.isPending} onClick={() => action.mutate('cancel')}>Cancel generation</button>}
    </div>}
    {job.data?.state === 'complete' && <button type="button" className="justify-self-start rounded border px-3 py-2" disabled={preview.isPending} onClick={() => preview.mutate()}>
      {preview.isPending ? 'Preparing preview…' : 'Show described recording preview'}
    </button>}
    {preview.data && <>
      <a href={preview.data.url} target="_blank" rel="noreferrer" className="underline">Open full-window description preview</a>
      <iframe title="Audio-described recording preview" src={preview.data.url} className="h-[75vh] w-full border" allow="autoplay" />
    </>}
  </section>
}
