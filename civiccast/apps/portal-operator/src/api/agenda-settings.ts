// SPDX-License-Identifier: Apache-2.0
// Copyright (c) The CivicCast Authors
import { staffApiRequest } from './client'

export interface AgendaSourceSettings {
  source: 'off' | 'legistar' | 'primegov' | 'civicclerk' | 'js_portal' | 'townlight'
  client_code: string | null
  portal_url: string | null
  portal_vendor_hint: 'generic' | 'civicplus' | 'granicus' | 'legistar_js' | 'primegov_js'
  timeout_seconds: number
}
export const agendaSettingsKey = ['agenda-source-settings']
export const getAgendaSourceSettings = () =>
  staffApiRequest<AgendaSourceSettings>('/api/staff/agenda-sources/settings')
export const saveAgendaSourceSettings = (settings: AgendaSourceSettings) =>
  staffApiRequest<AgendaSourceSettings>('/api/staff/agenda-sources/settings', {
    method: 'PUT', body: JSON.stringify(settings),
  })
