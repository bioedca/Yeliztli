/** Missing resources, transient failures, and withheld results remain distinct (#2433). */
import AxeBuilder from '@axe-core/playwright'
import { expect, test, type Page } from '@playwright/test'
import { bypassSetup } from './helpers'

const PRIVATE_BODY = 'server-only diagnostic must stay hidden'
const NOT_FOUND = "The requested data wasn't found. Check the link or select another sample."
const unexpectedRequests = new WeakMap<Page, string[]>()

test.beforeEach(async ({ page }) => {
  const unexpected: string[] = []
  unexpectedRequests.set(page, unexpected)
  // Keep these synthetic tests independent of any server or user's sample data.
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
  await page.route(/\/api\/analysis\/pharma\/drugs$/, route => route.fulfill({ json: { items: [], total: 0 } }))
  await page.route(/\/api\/analysis\/pharma\/report\?/, route => route.fulfill({ json: { genes_assessed: 0 } }))
})

test.afterEach(async ({ page }) => {
  expect(unexpectedRequests.get(page)).toEqual([])
})

for (const colorScheme of ['light', 'dark'] as const) {
  test(`a missing sample has no Retry and keyboard sample selection recovers in ${colorScheme} theme`, async ({ page }) => {
    await page.emulateMedia({ colorScheme })
    await page.route(/\/api\/analysis\/pharma\/genes\?/, route => {
      const missing = new URL(route.request().url()).searchParams.get('sample_id') === '99999'
      return route.fulfill(missing
        ? { status: 404, json: { detail: PRIVATE_BODY } }
        : { json: { items: [], total: 0 } })
    })
    // The freshness gate must let a genuinely missing sample reach its page error.
    await page.route('**/api/variants/count?sample_id=99999', route => route.fulfill({ status: 404, json: { detail: PRIVATE_BODY } }))
    await page.goto('/pharmacogenomics?sample_id=99999')
    await expect(page.getByRole('alert')).toContainText('Data not found')
    await expect(page.getByRole('alert')).toContainText(NOT_FOUND)
    await expect(page.getByRole('button', { name: 'Retry', exact: true })).toHaveCount(0)
    await expect(page.getByText(/Please try again|server-only diagnostic/)).toHaveCount(0)
    const audit = await new AxeBuilder({ page }).include('#main-content')
      .withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa']).analyze()
    expect(audit.passes.length).toBeGreaterThan(0)
    expect(audit.violations).toEqual([])

    const selector = page.getByRole('button', { name: 'Select sample, switch sample' })
    await selector.focus()
    await page.keyboard.press('Enter')
    const sample = page.getByRole('treeitem', { name: /Synthetic sample/ })
    await expect(sample).toBeFocused()
    await page.keyboard.press('Enter')
    await expect(page).toHaveURL(/\/pharmacogenomics\?sample_id=2$/)
    await expect(page.getByText('No pharmacogenomics results yet.', { exact: true })).toBeVisible()
    await expect(page.getByRole('alert')).toHaveCount(0)
    await expect(page.getByRole('button', { name: 'Synthetic sample, switch sample' })).toBeFocused()
  })
}

test('a missing variant retains its working back link without Retry', async ({ page }) => {
  await page.route('**/api/variants/rs00000000?sample_id=2', route => route.fulfill({ status: 404, json: { detail: PRIVATE_BODY } }))
  await page.route(/\/api\/column-presets(?:\?|$)/, route => route.fulfill({ json: { presets: [] } }))
  for (const endpoint of ['tags', 'watches', 'variants/chromosomes']) {
    await page.route(new RegExp(`/api/${endpoint}(?:\\?|$)`), route => route.fulfill({ json: [] }))
  }
  await page.route('**/api/samples/2/merge-provenance', route => route.fulfill({ json: null }))
  await page.route(/\/api\/variants\?/, route => route.fulfill({ json: {
    items: [], next_cursor_chrom: null, next_cursor_pos: null, has_more: false, limit: 100,
  } }))
  await page.goto('/variants/rs00000000?sample_id=2')
  await expect(page.getByRole('alert')).toContainText(NOT_FOUND)
  await expect(page.getByRole('button', { name: 'Retry', exact: true })).toHaveCount(0)
  await expect(page.getByText(PRIVATE_BODY)).toHaveCount(0)
  const back = page.getByRole('link', { name: /Back to Variant Explorer/ })
  await expect(back).toHaveAttribute('href', '/variants?sample_id=2')
  await back.focus()
  await page.keyboard.press('Enter')
  await expect(page).toHaveURL(/\/variants\?sample_id=2$/)
  await expect(page.getByRole('heading', { name: 'Variant Explorer', exact: true })).toBeVisible()
  await expect(page.getByRole('alert')).toHaveCount(0)
})

test('a transient failure keeps Retry and recovers when the request succeeds', async ({ page }) => {
  let recover = false
  let requests = 0
  await page.route(/\/api\/analysis\/pharma\/genes\?/, route => {
    requests += 1
    return route.fulfill(recover
      ? { json: { items: [], total: 0 } }
      : { status: 500, json: { detail: PRIVATE_BODY } })
  })
  await page.goto('/pharmacogenomics?sample_id=2')
  await expect(page.getByRole('alert')).toContainText('Pharma genes failed. Please try again.')
  await expect(page.getByText(PRIVATE_BODY)).toHaveCount(0)
  const before = requests
  expect(before).toBeGreaterThan(0)
  recover = true
  await page.getByRole('button', { name: 'Retry', exact: true }).click()
  await expect(page.getByText('No pharmacogenomics results yet.', { exact: true })).toBeVisible()
  expect(requests).toBe(before + 1)
  await expect(page.getByRole('alert')).toHaveCount(0)
})

test('a guard-withheld 404 never becomes a not-found result or exposes its body', async ({ page }) => {
  await page.route(/\/api\/analysis\/pharma\/genes\?/, route => route.fulfill({
    status: 404, json: { detail: { code: 'patient_response_withheld', message: PRIVATE_BODY } },
  }))
  await page.goto('/pharmacogenomics?sample_id=2')
  await expect(page.getByText('These results are withheld for this sample.', { exact: true })).toBeVisible()
  await expect(page.getByText(/Data not found|Please try again|server-only diagnostic/)).toHaveCount(0)
  await expect(page.getByRole('button', { name: 'Retry', exact: true })).toHaveCount(0)
})

for (const withheld of [true, false]) {
  test(`a ${withheld ? 'withheld' : 'missing'} pathway detail keeps its distinct 404 message`, async ({ page }) => {
    await page.route('**/api/analysis/allergy/pathways?sample_id=2', route => route.fulfill({ json: {
      items: [{ pathway_id: 'synthetic', pathway_name: 'Synthetic pathway', level: 'Standard',
        evidence_level: 0, called_snps: 0, total_snps: 0, missing_snps: [], pmids: [], hla_proxy_lookup: null }],
      total: 1, celiac_combined: null, histamine_combined: null, cross_module: [],
    } }))
    await page.route('**/api/analysis/allergy/pathway/synthetic?sample_id=2', route => route.fulfill({
      status: 404, json: { detail: withheld ? { code: 'patient_response_withheld', message: PRIVATE_BODY } : PRIVATE_BODY },
    }))
    await page.goto('/allergy?sample_id=2')
    await page.getByRole('button', { name: /^Synthetic pathway/ }).click()
    const expected = withheld ? "This pathway's results are withheld for this sample." : NOT_FOUND
    await expect(page.getByText(expected, { exact: true })).toBeVisible()
    await expect(page.getByText(withheld ? NOT_FOUND : "This pathway's results are withheld for this sample.", { exact: true })).toHaveCount(0)
    await expect(page.getByText(/Please try again|server-only diagnostic/)).toHaveCount(0)
    await expect(page.getByRole('button', { name: 'Retry', exact: true })).toHaveCount(0)
    const audit = await new AxeBuilder({ page }).include('[role="dialog"]')
      .withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa']).analyze()
    expect(audit.passes.length).toBeGreaterThan(0)
    expect(audit.violations).toEqual([])
  })
}
