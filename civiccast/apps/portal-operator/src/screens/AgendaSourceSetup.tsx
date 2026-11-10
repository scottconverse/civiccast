// SPDX-License-Identifier: Apache-2.0
// Copyright (c) The CivicCast Authors
import { useId, useState, type CSSProperties } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ApiError } from '../api/client'
import {
  agendaSettingsKey, getAgendaSourceSettings, saveAgendaSourceSettings,
  type AgendaSourceSettings,
} from '../api/agenda-settings'

const labels: Record<AgendaSourceSettings['source'], string> = {
  off: 'Disabled', legistar: 'Legistar', primegov: 'PrimeGov', civicclerk: 'CivicClerk',
  js_portal: 'JS-rendered portal (CivicPlus, Granicus, other)',
  townlight: 'Townlight shared importer',
}
const fieldStyle: CSSProperties = {
  background: 'var(--cc-surface)', border: '1px solid var(--cc-line)', color: 'var(--cc-ink)',
}
const hints = [
  ['generic', 'Generic / unknown'], ['civicplus', 'CivicPlus'], ['granicus', 'Granicus'],
  ['legistar_js', 'Legistar public page'], ['primegov_js', 'PrimeGov public page'],
]
function message(error: unknown): string {
  return error instanceof ApiError ? error.detail || error.message :
    error instanceof Error ? error.message : 'Check your connection and try again.'
}

export function AgendaSourceSetup() {
  const query = useQuery({ queryKey: agendaSettingsKey, queryFn: getAgendaSourceSettings, retry: false })
  return <section aria-label="Agenda source setup" className="space-y-3 rounded-md p-4" style={fieldStyle}>
    <h2 className="font-semibold">Agenda source setup</h2>
    <p className="text-sm">Choose the station default and save to enable discovery immediately. PrimeGov imports require choosing a meeting and confirming its preview.</p>
    {query.isLoading && <p role="status">Loading agenda settings…</p>}
    {query.isError && <p role="alert">Could not load agenda settings. {message(query.error)} <button onClick={() => void query.refetch()}>Retry</button></p>}
    {query.data && <SettingsForm settings={query.data} />}
  </section>
}

function SettingsForm({ settings }: { settings: AgendaSourceSettings }) {
  const id = useId()
  const [draft, setDraft] = useState(settings)
  const queryClient = useQueryClient()
  const mutation = useMutation({ mutationFn: saveAgendaSourceSettings,
    onSuccess: (saved) => { setDraft(saved); queryClient.setQueryData(agendaSettingsKey, saved) },
  })
  const update = (changes: Partial<AgendaSourceSettings>) => {
    setDraft({ ...draft, ...changes }); mutation.reset()
  }
  return <form className="grid max-w-xl gap-3 text-sm" onSubmit={(event) => {
    event.preventDefault()
    mutation.mutate({ ...draft, client_code: draft.client_code?.trim() || null,
      portal_url: draft.portal_url?.trim() || null })
  }}>
    <label htmlFor={`${id}-source`}>Default agenda provider</label>
    <select className="rounded-md px-2 py-1.5" style={fieldStyle} id={`${id}-source`} value={draft.source}
      onChange={(event) => update({ source: event.target.value as AgendaSourceSettings['source'] })}>
      {Object.entries(labels).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
    </select>
    <button className="justify-self-start rounded-md px-3 py-1.5 font-semibold" style={fieldStyle} type="button" onClick={() => update({ source: 'primegov', client_code: 'longmont', portal_url: null, portal_vendor_hint: 'generic' })}>Use Longmont / PrimeGov</button>
    <label htmlFor={`${id}-tenant`}>Tenant / site code</label>
    <input className="rounded-md px-2 py-1.5" style={fieldStyle} id={`${id}-tenant`} value={draft.client_code ?? ''} placeholder="longmont"
      required={draft.source !== 'off'} pattern="[A-Za-z0-9_-]{1,64}"
      onChange={(event) => update({ client_code: event.target.value })} />
    <p>For Longmont, choose PrimeGov and enter longmont. Public PrimeGov agendas need no credentials.</p>
    {draft.source === 'townlight' && <p>Set the importer, connector, format and URL in the station environment. Use the tenant field to label this source.</p>}
    {draft.source === 'js_portal' && <>
      <label htmlFor={`${id}-portal`}>Public portal URL</label>
      <input className="rounded-md px-2 py-1.5" style={fieldStyle} id={`${id}-portal`} type="url" required value={draft.portal_url ?? ''}
        onChange={(event) => update({ portal_url: event.target.value })} />
      <label htmlFor={`${id}-hint`}>Portal vendor hint</label>
      <select className="rounded-md px-2 py-1.5" style={fieldStyle} id={`${id}-hint`} value={draft.portal_vendor_hint}
        onChange={(event) => update({ portal_vendor_hint: event.target.value as AgendaSourceSettings['portal_vendor_hint'] })}>
        {hints.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
      </select>
    </>}
    <button className="justify-self-start rounded-md px-3 py-1.5 font-semibold disabled:opacity-50" style={{ background: 'var(--cc-brand)', color: 'var(--cc-brand-ink)' }} type="submit" disabled={mutation.isPending}>{mutation.isPending ? 'Saving…' : 'Save agenda source'}</button>
    {mutation.isError && <p role="alert">Could not save agenda settings. {message(mutation.error)}</p>}
    {mutation.isSuccess && <p role="status">Agenda source saved. It applies now and after restart.</p>}
  </form>
}
