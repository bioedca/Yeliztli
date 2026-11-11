/** Foreground errors must remain readable on their real surfaces (#2437). */
import AxeBuilder from '@axe-core/playwright'
import { existsSync } from 'node:fs'
import path from 'node:path'
import { expect, test, type Locator, type Page } from '@playwright/test'
import { bypassSetup, mockWizardChrome } from './helpers'

const unexpectedRequests = new WeakMap<Page, string[]>()
const overlay = {
  id: 1, name: 'Synthetic overlay', description: 'Synthetic UI fixture',
  file_type: 'bed', column_names: ['label'], region_count: 1, created_at: null,
}

test.beforeEach(async ({ page, baseURL }) => {
  // Exercise the shipped CSS, not Vite's development-only nested stylesheet.
  // CI builds this bundle before E2E; local runs must do the same.
  const dist = path.resolve(__dirname, '../../frontend/dist')
  expect(existsSync(path.join(dist, 'index.html')), 'Run npm --prefix frontend run build first').toBe(true)
  if (!baseURL) throw new Error('A baseURL is required for the production asset routes')
  const origin = new URL(baseURL).origin
  await page.route(url => url.origin === origin && ['/overlays', '/setup'].includes(url.pathname),
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
  await bypassSetup(page)
  await page.route(/\/api\/samples$/, route => route.fulfill({ json: [{
    id: 2, name: 'Synthetic sample', db_path: 'synthetic-fixture', file_format: null,
    file_hash: null, notes: null, date_collected: null, source: null, extra: null,
    created_at: null, updated_at: null,
  }] }))
  await page.route(/\/api\/individuals$/, route => route.fulfill({ json: [] }))
  await page.route(/\/api\/annotation\/active\/\d+$/, route => route.fulfill({ status: 404, json: { detail: 'No active job' } }))
  await page.route(/\/api\/variants\/count\?/, route => route.fulfill({ json: { total: 0, filtered: false } }))
})

test.afterEach(async ({ page }) => {
  expect(unexpectedRequests.get(page)).toEqual([])
  await expect(page.locator('link[rel="stylesheet"][href^="/assets/"]')).toHaveCount(1)
  await expect(page.locator('script[src*="@vite/client"]')).toHaveCount(0)
})

async function assertContrast(page: Page, selector: string) {
  await expect(page.locator(selector).first()).toBeVisible()
  const audit = await new AxeBuilder({ page }).include(selector)
    .withRules(['color-contrast']).analyze()
  expect(audit.violations).toEqual([])
  // An empty audit is not evidence that the populated error/control was checked.
  expect(audit.passes.find(rule => rule.id === 'color-contrast')?.nodes.length).toBeGreaterThan(0)
}

async function assertErrorColor(locator: Locator, alpha = 1) {
  const colors = await locator.evaluate((node, opacity) => {
    const style = getComputedStyle(node)
    const token = style.getPropertyValue('--color-destructive-text').trim()
    const context = document.createElement('canvas').getContext('2d')
    if (!context) throw new Error('Canvas color conversion is unavailable')
    const rgba = (color: string, alpha = 1) => {
      context.clearRect(0, 0, 1, 1)
      context.globalAlpha = alpha
      context.fillStyle = color
      context.fillRect(0, 0, 1, 1)
      return Array.from(context.getImageData(0, 0, 1, 1).data)
    }
    return { token, actual: rgba(style.color), expected: token ? rgba(token, opacity) : [] }
  }, alpha)
  // A missing generated utility can inherit readable neutral text and fool Axe.
  expect(colors.token).not.toBe('')
  expect(colors.actual).toEqual(colors.expected)
}

for (const colorScheme of ['light', 'dark'] as const) {
  test(`malformed upload error meets AA on a card in ${colorScheme} theme`, async ({ page }) => {
    await page.emulateMedia({ colorScheme })
    await page.route(/\/api\/overlays$/, route => route.fulfill({ json: { items: [], total: 0 } }))
    let parseRequests = 0
    await page.route(/\/api\/overlays\/parse$/, route => {
      parseRequests += 1
      return route.fulfill({ status: 422, json: { detail: 'Synthetic malformed BED' } })
    })
    await page.goto('/overlays?sample_id=2')
    await expect(page.getByText('Parse failed. Please try again.', { exact: true })).toHaveCount(0)
    await page.locator('input[type=file]').setInputFiles({
      name: 'synthetic.bed', mimeType: 'text/plain', buffer: Buffer.from('not a BED row\n'),
    })
    await expect(page.getByText('Parse failed. Please try again.', { exact: true })).toBeVisible()
    await assertErrorColor(page.getByText('Parse failed. Please try again.', { exact: true }))
    expect(parseRequests).toBe(1)
    await expect(page.getByRole('button', { name: 'Upload & Save' })).toBeDisabled()
    const audit = await new AxeBuilder({ page }).include('#main-content')
      .withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa']).analyze()
    expect(audit.passes.length).toBeGreaterThan(0)
    expect(audit.violations).toEqual([])
  })

  test(`destructive action stays readable and preserves its pending state in ${colorScheme} theme`, async ({ page, browserName }) => {
    await page.emulateMedia({ colorScheme })
    await page.route(/\/api\/overlays$/, route => route.fulfill({ json: { items: [overlay], total: 1 } }))
    let releaseDelete!: () => void
    const pending = new Promise<void>(resolve => { releaseDelete = resolve })
    let deletes = 0
    await page.route(/\/api\/overlays\/1$/, async route => {
      expect(route.request().method()).toBe('DELETE')
      deletes += 1
      await pending
      await route.fulfill({ status: 500, json: { detail: 'Synthetic deletion failure' } })
    })
    await page.goto('/overlays?sample_id=2')
    const button = page.getByRole('button', { name: 'Delete', exact: true })
    await expect(button).toBeEnabled()
    await assertErrorColor(button)
    const selector = '#main-content button[class*="hover:bg-destructive"]'
    await expect(page.locator(selector)).toHaveCount(1)
    await assertContrast(page, selector)
    const restingBackground = await button.evaluate(node => getComputedStyle(node).backgroundColor)
    const supportsHover = await page.evaluate(() => matchMedia('(hover: hover)').matches)
    // Keep a positive hover requirement; headless Firefox may report hover:none.
    if (browserName !== 'firefox') expect(supportsHover).toBe(true)
    await button.hover()
    if (supportsHover) {
      await expect.poll(() => button.evaluate(node => getComputedStyle(node).backgroundColor)).not.toBe(restingBackground)
    } else {
      await expect(button).toHaveCSS('background-color', restingBackground)
    }
    await assertErrorColor(button)
    await assertContrast(page, selector)
    await page.mouse.move(0, 0)
    await page.keyboard.press('Tab')
    await button.focus()
    await expect(button).toBeFocused()
    await expect(button).toHaveCSS('outline-style', 'solid')
    await expect(button).toHaveCSS('outline-width', '2px')
    await assertErrorColor(button)
    await assertContrast(page, selector)
    page.once('dialog', dialog => dialog.accept())
    try {
      await button.click()
      await expect(button).toBeDisabled()
      await expect(button).toHaveCSS('opacity', '0.5')
      expect(deletes).toBe(1)
    } finally {
      releaseDelete()
    }
    const deleteError = page.getByText('Delete failed. Please try again.', { exact: true })
    await expect(deleteError).toBeVisible()
    await assertErrorColor(deleteError)
    await expect(button).toBeEnabled()
    await expect(button).toHaveCSS('opacity', '1')
    expect(deletes).toBe(1)
    // Contrast analysis must inspect the error after it enters the viewport.
    await deleteError.scrollIntoViewIfNeeded()
    await expect(deleteError).toBeInViewport({ ratio: 1 })
    await assertContrast(page, '#main-content p[class*="text-destructive"]')
  })

  test(`tinted storage error and its subdued explanation meet AA in ${colorScheme} theme`, async ({ page }) => {
    await page.emulateMedia({ colorScheme })
    await mockWizardChrome(page)
    await page.route('**/api/setup/status', route => route.fulfill({ json: {
      needs_setup: true, disclaimer_accepted: true, has_databases: false,
      required_dbs_ready: false, db_readiness: [], has_samples: false, data_dir: 'synthetic-fixture',
    } }))
    await page.route('**/api/setup/storage-info', route => route.fulfill({ json: {
      data_dir: 'synthetic-fixture', free_space_bytes: 1_000_000_000, free_space_gb: 1,
      total_space_bytes: 100_000_000_000, total_space_gb: 100, status: 'blocked',
      message: 'Synthetic storage capacity is insufficient.', path_exists: true, path_writable: true,
    } }))
    await page.goto('/setup')
    await page.getByRole('button', { name: /Skip — Start Fresh/i }).click()
    await expect(page.getByText('Insufficient Disk Space', { exact: true })).toBeVisible()
    await expect(page.getByText('Synthetic storage capacity is insufficient.', { exact: true })).toBeVisible()
    await assertErrorColor(page.getByText('Insufficient Disk Space', { exact: true }))
    await assertErrorColor(page.getByText('Synthetic storage capacity is insufficient.', { exact: true }), 0.8)
    await expect(page.getByRole('button', { name: 'Continue', exact: true })).toBeDisabled()
    await assertContrast(page, '[class~="bg-destructive/5"]')
  })
}
