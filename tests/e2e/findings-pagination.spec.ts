/** Real scroll, mounted-row and recovery controls for All Findings (#2485). */
import { expect, test, type Page } from '@playwright/test'
import AxeBuilder from '@axe-core/playwright'
import { bypassSetup, mockFreshSampleState } from './helpers'

const json = (body: unknown, status = 200) => ({
  status, contentType: 'application/json', body: JSON.stringify(body),
})

const findings = (count: number) => Array.from({ length: count }, (_, index) => ({
  id: index + 1, module: 'rare_variants', evidence_level: 1,
  finding_text: `Synthetic finding ${index + 1}`, category: 'synthetic', detail: null,
}))

async function setup(page: Page) {
  await bypassSetup(page)
  await mockFreshSampleState(page)
  await page.route('**/api/samples', route => route.fulfill(json([])))
  await page.route('**/api/individuals', route => route.fulfill(json([])))
  await page.route('**/api/analysis/findings/summary*', route => route.fulfill(json({
    total_findings: 450, modules: [], high_confidence_findings: [],
  })))
}

for (const theme of ['light', 'dark'] as const) {
  test(`Load more preserves mounted rows and scroll in ${theme} mode (#2485)`, async ({ page }) => {
    // A populated 200-row axe scan takes ~24 s in WebKit; retain CI load headroom.
    test.setTimeout(60_000)
    await page.emulateMedia({ colorScheme: theme })
    await setup(page)
    let release!: () => void
    const held = new Promise<void>(resolve => { release = resolve })
    const limits: number[] = []
    await page.route('**/api/analysis/findings?*', async route => {
      const limit = Number(new URL(route.request().url()).searchParams.get('limit'))
      limits.push(limit)
      if (limit > 200) await held
      await route.fulfill(json(findings(Math.min(limit, 450))))
    })

    await page.goto('/findings?sample_id=1')
    const more = page.getByRole('button', { name: 'Load more findings', exact: true })
    await more.scrollIntoViewIfNeeded()
    const main = page.getByRole('main')
    const before = await main.evaluate(element => element.scrollTop)
    expect(before).toBeGreaterThan(1000)
    const first = await page.getByText('Synthetic finding 1', { exact: true }).elementHandle()
    expect(first).not.toBeNull()
    await more.focus()
    await page.keyboard.press('Enter')
    try {
      await expect.poll(() => limits).toEqual([200, 400])
      await expect(page.getByRole('button', { name: 'Loading…', exact: true })).toBeDisabled()
      await expect(page.getByText('Loading findings...', { exact: true })).toHaveCount(0)
      await expect(page.getByText(/^Synthetic finding \d+$/)).toHaveCount(200)
      expect(await first!.evaluate(element => element.isConnected)).toBe(true)
      expect(Math.abs(await main.evaluate(element => element.scrollTop) - before)).toBeLessThanOrEqual(1)
      const accessibility = await new AxeBuilder({ page }).include('main')
        .withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa']).analyze()
      expect(accessibility.violations).toEqual([])
    } finally {
      release()
    }
    // Count/attachment assertions do not scroll to newly appended rows.
    await expect(page.getByText(/^Synthetic finding \d+$/)).toHaveCount(400)
    expect(await first!.evaluate(element => element.isConnected)).toBe(true)
    expect(Math.abs(await main.evaluate(element => element.scrollTop) - before)).toBeLessThanOrEqual(1)
    await expect(more).toBeEnabled()
    await more.click()
    await expect(page.getByText(/^Synthetic finding \d+$/)).toHaveCount(450)
    await expect(more).toHaveCount(0)
    expect(limits).toEqual([200, 400, 600])
  })

}

test('a transient next-window error retains rows and retries the same window (#2485)', async ({ page }) => {
  await setup(page)
  let fail = true
  const limits: number[] = []
  await page.route('**/api/analysis/findings?*', route => {
    const limit = Number(new URL(route.request().url()).searchParams.get('limit'))
    limits.push(limit)
    return route.fulfill(limit > 200 && fail
      ? json({ detail: 'Synthetic private diagnostic' }, 503)
      : json(findings(Math.min(limit, 250))))
  })
  await page.goto('/findings?sample_id=1')
  const more = page.getByRole('button', { name: 'Load more findings', exact: true })
  await more.scrollIntoViewIfNeeded()
  const before = await page.getByRole('main').evaluate(element => element.scrollTop)
  const first = await page.getByText('Synthetic finding 1', { exact: true }).elementHandle()
  expect(first).not.toBeNull()
  await more.click()
  await expect(page.getByRole('alert')).toContainText('Could not load more findings', { timeout: 15_000 })
  await expect(page.getByText(/^Synthetic finding \d+$/)).toHaveCount(200)
  await expect(page.getByText('Synthetic private diagnostic')).toHaveCount(0)
  expect(await first!.evaluate(element => element.isConnected)).toBe(true)
  expect(Math.abs(await page.getByRole('main').evaluate(element => element.scrollTop) - before)).toBeLessThanOrEqual(1)
  fail = false
  await page.getByRole('button', { name: 'Retry loading more findings', exact: true }).click()
  await expect(page.getByText(/^Synthetic finding \d+$/)).toHaveCount(250)
  expect(await first!.evaluate(element => element.isConnected)).toBe(true)
  await expect(page.getByRole('alert')).toHaveCount(0)
  expect(limits.length).toBeGreaterThanOrEqual(3)
  expect(limits[0]).toBe(200)
  expect(limits.slice(1).every(limit => limit === 400)).toBe(true)
})
