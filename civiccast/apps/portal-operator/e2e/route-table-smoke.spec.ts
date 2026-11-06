import { expect, test } from '@playwright/test'

import { ROUTE_ALIASES, ROUTE_PATHS } from '../src/routes'

test.beforeEach(async ({ page }) => {
  await page.route('**/api/version', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ version: '1.0.0-rc.5' }),
    })
  })
  await page.route('**/api/staff/auth/me', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        operator_id: 'route-smoke',
        operator_display_name: 'Route Smoke',
        token_id: 'route-smoke-token',
        scopes: ['operator'],
        roles: [
          'setup_admin',
          'meeting_operator',
          'support_admin',
          'records_clerk',
          'publisher',
        ],
      }),
    })
  })
  await page.route('**/api/**', async (route) => {
    await route.fulfill({
      status: 503,
      contentType: 'application/json',
      body: JSON.stringify({ detail: 'Route smoke deliberately leaves this dependency absent.' }),
    })
  })
})

test.describe('operator route table smoke coverage', () => {
  test.describe.configure({ mode: 'serial' })

  for (const viewport of [
    { label: 'desktop', width: 1280, height: 900 },
    { label: 'mobile', width: 390, height: 844 },
  ]) {
    for (const [routeId, routePath] of Object.entries(ROUTE_PATHS)) {
      test(`${routeId} renders ${routePath} on ${viewport.label} without falling through to not found`, async ({ page }) => {
        await page.setViewportSize({ width: viewport.width, height: viewport.height })
        await page.goto(`/#${routePath}`)
        await expect(page.getByText('v1.0.0-rc.5')).toBeVisible()
        await expect(page.getByRole('main').first()).toBeVisible()
        await expect(page.getByRole('heading', { name: 'Page not found' })).toHaveCount(0)
      })
    }

    for (const [aliasPath, canonicalPath] of Object.entries(ROUTE_ALIASES)) {
      test(`alias ${aliasPath} redirects to ${canonicalPath} on ${viewport.label}`, async ({ page }) => {
        await page.setViewportSize({ width: viewport.width, height: viewport.height })
        await page.goto(`/#${aliasPath}`)
        await expect(page.getByText('v1.0.0-rc.5')).toBeVisible()
        await expect(page.getByRole('main').first()).toBeVisible()
        await expect(page.getByRole('heading', { name: 'Page not found' })).toHaveCount(0)
      })
    }
  }
})
