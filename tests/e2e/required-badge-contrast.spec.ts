/** Required labels must remain readable on the actual downloaded card (#2695). */
import AxeBuilder from '@axe-core/playwright'
import { existsSync } from 'node:fs'
import path from 'node:path'
import { expect, test, type Page } from '@playwright/test'
import { mockWizardChrome, walkWizardToDatabases } from './helpers'

const unexpectedRequests = new WeakMap<Page, string[]>()
const databases = {
  databases: [
    {
      name: 'clinvar', display_name: 'ClinVar', description: 'Synthetic required reference',
      filename: 'clinvar.db', expected_size_bytes: 250_000_000, required: true,
      phase: 1, downloaded: true, file_size_bytes: 250_000_000, build_mode: 'pipeline',
    },
    {
      name: 'encode_ccres', display_name: 'ENCODE cCREs', description: 'Synthetic optional reference',
      filename: 'encode_ccres.db', expected_size_bytes: 30_000_000, required: false,
      phase: 3, downloaded: false, file_size_bytes: null, build_mode: 'download',
    },
  ],
  total_size_bytes: 280_000_000, downloaded_count: 1, total_count: 2,
}
const progress = {
  session_id: 'synthetic-required-badge',
  databases: [{
    db_name: 'encode_ccres', job_id: 'synthetic-encode', status: 'running',
    progress_pct: 70, message: 'Synthetic download', error: null,
    total_bytes: 30_000_000, downloaded_bytes: 21_000_000,
    speed_bps: 8_000_000, eta_seconds: 90,
  }],
  aggregate: {
    total_bytes: 30_000_000, downloaded_bytes: 21_000_000, remaining_bytes: 9_000_000,
    overall_pct: 70, speed_bps: 8_000_000, eta_seconds: 90, size_unknown_count: 0,
  },
}

test.beforeEach(async ({ page, baseURL }) => {
  // Keep the existing production-asset convention used by error-text-contrast.
  const dist = path.resolve(__dirname, '../../frontend/dist')
  expect(existsSync(path.join(dist, 'index.html')), 'Run npm --prefix frontend run build first').toBe(true)
  if (!baseURL) throw new Error('A baseURL is required for the production asset routes')
  const origin = new URL(baseURL).origin
  await page.route(url => url.origin === origin && url.pathname === '/setup',
    route => route.fulfill({ path: path.join(dist, 'index.html') }))
  await page.route(url => url.origin === origin && url.pathname.startsWith('/assets/'), route => {
    const asset = new URL(route.request().url()).pathname.slice('/assets/'.length)
    expect(asset).toMatch(/^[A-Za-z0-9_.-]+$/)
    return route.fulfill({ path: path.join(dist, 'assets', asset) })
  })
  const unexpected: string[] = []
  unexpectedRequests.set(page, unexpected)
  await page.route(/^https?:\/\/[^/]+\/api\//, async route => {
    unexpected.push(new URL(route.request().url()).pathname)
    await route.abort()
  })
  await mockWizardChrome(page)
  await page.route('**/api/setup/status', route => route.fulfill({ json: {
    needs_setup: true, disclaimer_accepted: true, has_databases: true,
    required_dbs_ready: false, db_readiness: [], has_samples: false, data_dir: 'synthetic-fixture',
  } }))
  await page.route('**/api/databases', route => route.fulfill({ json: databases }))
  await page.route('**/api/databases/health', route => route.fulfill({ json: { databases: [] } }))
  await page.route('**/api/databases/download', route => route.fulfill({ status: 202, json: {
    session_id: progress.session_id,
    downloads: [{ db_name: 'encode_ccres', job_id: 'synthetic-encode' }],
  } }))
  await page.addInitScript(event => {
    class ProgressSource extends EventTarget {
      constructor() {
        super()
        setTimeout(() => this.dispatchEvent(new MessageEvent('progress', {
          data: JSON.stringify(event),
        })), 100)
      }
      close() {}
    }
    window.EventSource = ProgressSource as unknown as typeof EventSource
  }, progress)
})

test.afterEach(async ({ page }) => {
  expect(unexpectedRequests.get(page)).toEqual([])
  await expect(page.locator('link[rel="stylesheet"][href^="/assets/"]')).toHaveCount(1)
  await expect(page.locator('script[src*="@vite/client"]')).toHaveCount(0)
})

for (const colorScheme of ['light', 'dark'] as const) {
  test(`Required badge is readable during a download in ${colorScheme} theme`, async ({ page }) => {
    await page.emulateMedia({ colorScheme })
    await walkWizardToDatabases(page)
    await expect(page.getByTestId('db-checkbox-encode_ccres')).toBeChecked()
    const download = page.waitForResponse(response =>
      response.url().endsWith('/api/databases/download') && response.request().method() === 'POST')
    await page.getByRole('button', { name: /Download Selected/i }).click()
    const response = await download
    expect(response.status()).toBe(202)
    expect(response.request().postDataJSON()).toEqual({ databases: ['encode_ccres'] })
    await expect(page.getByTestId('aggregate-pct')).toHaveText('70%')
    await expect(page.getByTestId('db-rate-encode_ccres')).toContainText('8.0 MB/s')
    await expect(page.getByTestId('db-checkbox-encode_ccres')).toBeDisabled()
    await expect(page.getByRole('button', { name: 'Continue', exact: true })).toBeDisabled()

    const selector = '[data-testid="db-required-badge-clinvar"]'
    const badge = page.locator(selector)
    await expect(badge).toHaveCount(1)
    await expect(badge).toHaveText('Required')
    await badge.scrollIntoViewIfNeeded()
    await expect(badge).toBeInViewport({ ratio: 1 })
    const measurement = await badge.evaluate(node => {
      const style = getComputedStyle(node)
      return {
        foreground: style.color, background: style.backgroundColor,
        fontSize: style.fontSize, fontWeight: style.fontWeight,
        dark: document.documentElement.classList.contains('dark'),
        disabledAncestor: Boolean(node.closest(':disabled, [aria-disabled="true"], [inert]')),
      }
    })
    // The informational label remains eligible even while sibling controls wait.
    expect(measurement.disabledAncestor).toBe(false)
    expect(measurement.dark).toBe(colorScheme === 'dark')
    const audit = await new AxeBuilder({ page }).include(selector)
      .withRules(['color-contrast']).analyze()
    await test.info().attach('required-badge-contrast', {
      body: Buffer.from(JSON.stringify({ measurement, audit }, null, 2)),
      contentType: 'application/json',
    })
    expect(audit.violations).toEqual([])
    expect(audit.incomplete).toEqual([])
    // An empty, hidden or exempt target cannot establish readable badge text.
    expect(audit.passes.find(rule => rule.id === 'color-contrast')?.nodes.length).toBe(1)
  })
}
