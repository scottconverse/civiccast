// SPDX-License-Identifier: Apache-2.0
// Copyright (c) The CivicCast Authors
//
// Playwright config for the operator-portal verify skill. It reuses the app's
// own config and changes only what the verify run needs:
// - the preview is started by verify-portal.ps1 (hidden, on a free port,
//   stopped by its PID), so there is no webServer here and nothing can
//   attach to a stray preview on 4174;
// - the browser is headless, and trace, screenshots and video are always
//   kept in the evidence folder.
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { defineConfig } from '@playwright/test'
import base from '../../../playwright.config'

const here = path.dirname(fileURLToPath(import.meta.url))
const appRoot = path.resolve(here, '../../..')
const baseURL = process.env.VERIFY_PORTAL_BASE_URL
const evidence = process.env.VERIFY_PORTAL_EVIDENCE_DIR
if (!baseURL || !evidence) {
  throw new Error('Run this through verify-portal.ps1 (VERIFY_PORTAL_BASE_URL and VERIFY_PORTAL_EVIDENCE_DIR are unset).')
}

export default defineConfig({
  ...base,
  testDir: path.join(appRoot, 'e2e'),
  outputDir: path.join(evidence, 'playwright-output'),
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: [
    ['list'],
    ['json', { outputFile: path.join(evidence, 'playwright-results.json') }],
    ['html', { open: 'never', outputFolder: path.join(evidence, 'playwright-report') }],
  ],
  use: {
    ...base.use,
    baseURL,
    headless: true,
    trace: 'on',
    screenshot: 'on',
    video: 'off',
  },
  webServer: undefined,
})
