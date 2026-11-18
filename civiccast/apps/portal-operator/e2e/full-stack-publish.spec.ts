import { expect, test, type Page } from '@playwright/test'
import { spawn, type ChildProcessWithoutNullStreams } from 'node:child_process'
import net from 'node:net'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const STAFF_TOKEN = 'Bearer operator-token-a'
const E2E_DIR = path.dirname(fileURLToPath(import.meta.url))
const REPO_ROOT = path.resolve(E2E_DIR, '../../../..')
const PYTHON =
  process.env.PYTHON ??
  path.join(
    REPO_ROOT,
    '.venv',
    process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python',
  )

let backendProcess: ChildProcessWithoutNullStreams | undefined
let backendUrl = ''
let backendOutput = ''

function backendLogTail() {
  return backendOutput.slice(-8_000)
}

async function findFreePort(): Promise<number> {
  return new Promise((resolve, reject) => {
    const server = net.createServer()
    server.once('error', reject)
    server.listen(0, '127.0.0.1', () => {
      const address = server.address()
      server.close(() => {
        if (typeof address === 'object' && address != null) {
          resolve(address.port)
        } else {
          reject(new Error('Could not allocate a loopback port for the fixture API.'))
        }
      })
    })
  })
}

async function waitForBackendReady() {
  const deadline = Date.now() + 30_000
  let lastError = ''
  while (Date.now() < deadline) {
    if (backendProcess?.exitCode != null) {
      throw new Error(
        `Fixture API exited with ${backendProcess.exitCode}.\n${backendLogTail()}`,
      )
    }
    try {
      const response = await fetch(`${backendUrl}/health`)
      if (response.ok) return
      lastError = `HTTP ${response.status}`
    } catch (error) {
      lastError = String(error)
    }
    await new Promise((resolve) => setTimeout(resolve, 250))
  }
  throw new Error(`Fixture API did not become ready: ${lastError}\n${backendLogTail()}`)
}

async function proxyStaffApiToFixture(page: Page) {
  await page.route('**/api/**', async (route) => {
    const request = route.request()
    const source = new URL(request.url())
    const headers = {
      ...request.headers(),
      authorization: STAFF_TOKEN,
    }
    delete headers.host

    const response = await route.fetch({
      url: `${backendUrl}${source.pathname}${source.search}`,
      headers,
    })
    await route.fulfill({ response })
  })
}

test.describe.configure({ mode: 'serial' })

test.beforeAll(async () => {
  const port = await findFreePort()
  backendUrl = `http://127.0.0.1:${port}`
  backendOutput = ''
  backendProcess = spawn(
    PYTHON,
    [
      '-m',
      'uvicorn',
      'tests.integration.operator_fullstack_fixture_app:app',
      '--host',
      '127.0.0.1',
      '--port',
      String(port),
    ],
    {
      cwd: REPO_ROOT,
      env: {
        ...process.env,
        CIVICCAST_ACTIVITYPUB_MODE: 'disabled',
        CIVICCAST_ALLOW_DETERMINISTIC_STAFF_TOKEN: '1',
        CIVICCAST_ALLOW_EPHEMERAL_STORES: '1',
        DATABASE_URL: '',
        PYTHONPATH: REPO_ROOT,
      },
      windowsHide: true,
    },
  )
  backendProcess.stdout.on('data', (chunk: Buffer) => {
    backendOutput += chunk.toString()
  })
  backendProcess.stderr.on('data', (chunk: Buffer) => {
    backendOutput += chunk.toString()
  })
  await waitForBackendReady()
})

test.afterAll(async () => {
  if (backendProcess && backendProcess.exitCode == null) {
    backendProcess.kill()
  }
})

test.beforeEach(async ({ page }) => {
  await proxyStaffApiToFixture(page)
})

test('@fullstack approves a public-record publish cycle against the live API', async ({ page }) => {
  const consoleErrors: string[] = []
  const pageErrors: string[] = []
  page.on('console', (message) => {
    if (message.type() === 'error') consoleErrors.push(message.text())
  })
  page.on('pageerror', (error) => pageErrors.push(error.message))

  await page.goto('/')
  await page.getByRole('button', { name: 'Publish' }).click()
  await expect(page.getByRole('heading', { name: 'Publish dashboard' })).toBeVisible()

  const councilPanel = page.locator('article').filter({ hasText: 'Council - May 8, 2026' })
  // PublishDashboardScreen now also renders a "Publish readiness"
  // (PreflightPanel) section per asset, alongside the surface-selection
  // checklist -- both legitimately name every surface (WP-03 plan item 8:
  // preflight and approval read the same registry, so they always agree),
  // so each surface name can appear twice in this panel once readiness has
  // loaded. .first() below just proves "the surface name is present" the
  // way it did before that panel existed; it doesn't weaken the later
  // structural assertions (toHaveCount(3) for the archive-simulated note,
  // toHaveCount(0) for a real archive.org URL), which stay exact.
  await expect(councilPanel.getByText('Internet Archive', { exact: true }).first()).toBeVisible()
  await expect(councilPanel.getByText('Local NAS rsync', { exact: true }).first()).toBeVisible()
  await expect(councilPanel.getByText('Local NAS ZFS', { exact: true }).first()).toBeVisible()
  await expect(councilPanel.getByText('YouTube Live', { exact: true }).first()).toBeVisible()

  // B3: only Portal is selected by default; archive surfaces are opt-in per
  // approval. A public record needs all three required archive surfaces, so
  // the operator ticks each one, exactly as a clerk does.
  await expect(councilPanel.getByTestId('required-surfaces-unselected')).toHaveText(
    /3 required archive surfaces not selected/,
  )
  for (const surfaceId of ['internet-archive', 'local-nas-rsync', 'local-nas-zfs']) {
    await councilPanel
      .locator(`[data-surface-id="${surfaceId}"]`)
      .getByRole('checkbox', { name: 'Approve this surface' })
      .check()
  }
  await expect(councilPanel.getByTestId('required-surfaces-unselected')).toHaveCount(0)

  await councilPanel.getByRole('button', { name: 'Approve and Publish selected' }).click()
  await page.getByRole('alertdialog').getByRole('button', { name: 'Approve and Publish' }).click()

  await expect(councilPanel.getByText('Portal public')).toBeVisible()
  await expect(councilPanel.getByText('IA and local NAS verified')).toBeVisible()

  // GauntletGate TW-1: the default Internet Archive client
  // (civiccast.archive.models.MockInternetArchiveClient) is intentionally a
  // mock until an admin sets CIVICCAST_PROVIDER_INTERNET_ARCHIVE=real. It
  // flags the proof `simulated: true` and emits a target on the RFC 2606
  // `.invalid` TLD, which can never be mistaken for a real permalink. The
  // dashboard must label it "Simulated target:" (PublishDashboardScreen.tsx)
  // rather than the real-looking "URL:" label used for genuine archive
  // writes -- that mislabeling is exactly the bug TW-1 fixed, where a mock
  // provider's proof looked identical to a real archival write and a clerk
  // could approve a meeting as legally archived when nothing was written.
  await expect(
    councilPanel.getByText(
      'Simulated target: https://internet-archive.simulated.invalid/details/council-2026-05-08',
    ),
  ).toBeVisible()
  // All three archive-tier surfaces (Internet Archive + local NAS rsync +
  // local NAS ZFS) run through the mock clients in this fixture, so the
  // warning must render on each of them, not just one.
  //
  // getByRole('note', ...) (not getByText) so this assertion actually guards
  // the role="note" on PublishDashboardScreen.tsx's warning element -- with
  // getByText alone, removing role="note" while leaving the text unchanged
  // would still pass, silently losing the accessibility semantics for this
  // legally-significant warning.
  await expect(
    councilPanel
      .getByRole('note')
      .filter({ hasText: 'Simulated — nothing was actually archived' }),
  ).toHaveCount(3)

  // Regression guard: a real-looking archive.org permalink must never appear
  // on this surface while it is simulated. If this starts failing, the
  // `simulated` flag stopped propagating from the mock client to the UI --
  // the exact unsafe state TW-1 fixed.
  await expect(councilPanel.getByText(/archive\.org\/details/)).toHaveCount(0)

  await expect(councilPanel.getByText(/Hash: sha256:/)).toHaveCount(3)
  expect(consoleErrors).toEqual([])
  expect(pageErrors).toEqual([])
})
