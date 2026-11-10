/** The BED boundary correction must reach users with previously saved results. */
import AxeBuilder from '@axe-core/playwright'
import { existsSync } from 'node:fs'
import path from 'node:path'
import { expect, test, type Page } from '@playwright/test'
import { bypassSetup, mockFreshSampleState } from './helpers'

const unexpectedRequests = new WeakMap<Page, string[]>()
const overlay = {
  id: 1, name: 'Synthetic BED boundary', description: 'Synthetic UI fixture',
  file_type: 'bed', column_names: ['label'], region_count: 1, created_at: null,
}

test.beforeEach(async ({ page, baseURL }) => {
  const dist = path.resolve(__dirname, '../../frontend/dist')
  expect(existsSync(path.join(dist, 'index.html')), 'Build the frontend before E2E').toBe(true)
  if (!baseURL) throw new Error('A baseURL is required')
  const origin = new URL(baseURL).origin
  await page.route(url => url.origin === origin && url.pathname === '/overlays',
    route => route.fulfill({ path: path.join(dist, 'index.html') }))
  await page.route(url => url.origin === origin && url.pathname.startsWith('/assets/'), route => {
    const asset = new URL(route.request().url()).pathname.slice('/assets/'.length)
    expect(path.basename(asset)).toBe(asset)
    return route.fulfill({ path: path.join(dist, 'assets', asset) })
  })
  const unexpected: string[] = []
  unexpectedRequests.set(page, unexpected)
  await page.route(/^https?:\/\/[^/]+\/api\//, async route => {
    unexpected.push(new URL(route.request().url()).pathname)
    await route.abort()
  })
  await bypassSetup(page)
  await mockFreshSampleState(page)
  await page.route(/\/api\/samples$/, route => route.fulfill({ json: [{
    id: 1, name: 'Synthetic sample', db_path: 'synthetic-fixture', file_format: null,
    file_hash: null, notes: null, date_collected: null, source: null, extra: null,
    created_at: null, updated_at: null,
  }] }))
  await page.route(/\/api\/individuals$/, route => route.fulfill({ json: [] }))
  await page.route(/\/api\/annotation\/active\/\d+$/, route =>
    route.fulfill({ status: 404, json: { detail: 'No active job' } }))
})

test.afterEach(async ({ page }) => {
  expect(unexpectedRequests.get(page)).toEqual([])
  await expect(page.locator('link[rel="stylesheet"][href^="/assets/"]')).toHaveCount(1)
  await expect(page.locator('script[src*="@vite/client"]')).toHaveCount(0)
})

for (const theme of ['light', 'dark'] as const) {
  test(`BED reapply guidance and refreshed results work in ${theme} theme`, async ({ page }) => {
    await page.addInitScript(value => localStorage.setItem('gi-theme', value), theme)
    await page.route(/\/api\/overlays$/, route =>
      route.fulfill({ json: { items: [overlay], total: 1 } }))
    let applied = false
    let applyRequests = 0
    await page.route(/\/api\/overlays\/1\/apply\?/, route => {
      expect(route.request().method()).toBe('POST')
      expect(new URL(route.request().url()).searchParams.get('sample_id')).toBe('1')
      applied = true
      applyRequests += 1
      return route.fulfill({ json: {
        overlay_id: 1, overlay_name: overlay.name, variants_matched: 2, records_checked: 1,
      } })
    })
    await page.route(/\/api\/overlays\/1\/results\?/, route => {
      expect(new URL(route.request().url()).searchParams.get('sample_id')).toBe('1')
      return route.fulfill({ json: {
        overlay_id: 1, overlay_name: overlay.name, total: 2,
        results: (applied ? [101, 200] : [100, 101]).map(pos => ({
          rsid: `synthetic_pos_${pos}`, overlay_id: 1, label: applied ? 'corrected' : 'old',
        })),
      } })
    })

    await page.goto('/overlays?sample_id=1')
    const notice = page.getByRole('note', { name: 'BED overlay update' })
    await expect(notice).toBeVisible()
    await expect(notice).toContainText('each sample')
    await expect(notice).toContainText('Apply')
    await expect(notice).toContainText('Saved results do not update automatically')
    const audit = await new AxeBuilder({ page })
      .include('[role="note"][aria-label="BED overlay update"]')
      .withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa']).analyze()
    expect(audit.violations).toEqual([])
    expect(audit.passes.find(rule => rule.id === 'color-contrast')?.nodes.length).toBeGreaterThan(0)

    await page.getByRole('heading', { name: overlay.name }).click()
    await expect(page.getByText('synthetic_pos_100', { exact: true })).toBeVisible()
    await expect(page.getByText('synthetic_pos_101', { exact: true })).toBeVisible()
    await expect(page.getByText('synthetic_pos_200', { exact: true })).toHaveCount(0)
    await page.getByRole('button', { name: 'Apply', exact: true }).click()
    await expect(page.getByText('synthetic_pos_200', { exact: true })).toBeVisible()
    await expect(page.getByText('synthetic_pos_101', { exact: true })).toBeVisible()
    await expect(page.getByText('synthetic_pos_100', { exact: true })).toHaveCount(0)
    await expect(page.getByText('synthetic_pos_201', { exact: true })).toHaveCount(0)
    await expect(page.getByRole('cell', { name: 'corrected', exact: true })).toHaveCount(2)
    expect(applyRequests).toBe(1)
  })
}

test('VCF-only overlays do not show the BED reapply notice', async ({ page }) => {
  await page.route(/\/api\/overlays$/, route => route.fulfill({ json: {
    items: [{ ...overlay, name: 'Synthetic VCF', file_type: 'vcf' }], total: 1,
  } }))
  await page.goto('/overlays?sample_id=1')
  await expect(page.getByRole('heading', { name: 'Synthetic VCF' })).toBeVisible()
  await expect(page.getByRole('note', { name: 'BED overlay update' })).toHaveCount(0)
})
