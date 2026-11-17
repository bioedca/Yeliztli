import AxeBuilder from '@axe-core/playwright'
import { expect, test, type Page, type Route } from '@playwright/test'
import { bypassSetup } from './helpers'

// Synthetic search metadata only; no uploaded sample or genotype is needed.
const variants = [424242, 424243].map((id) => ({
  rsid: `rs${id}`, gene_symbol: 'LDLR', clinvar_significance: null,
}))

async function openPalette(page: Page, query: string) {
  await page.goto('/settings/general?sample_id=7')
  await page.getByTestId('command-palette-trigger').click()
  const input = page.getByTestId('command-palette-input')
  await expect(input).toBeFocused()
  await input.fill(query)
  return input
}

async function holdSearch(page: Page, query: string, sample = '7', results = variants) {
  let capture!: (route: Route) => void
  const request = new Promise<Route>((resolve) => { capture = resolve })
  let release!: () => void
  const gate = new Promise<void>((resolve) => { release = resolve })
  await page.route('**/api/variants/search**', async (route) => {
    const url = new URL(route.request().url())
    if (url.searchParams.get('q') !== query || url.searchParams.get('sample_id') !== sample) {
      await route.fallback()
      return
    }
    capture(route)
    await gate
    await route.fulfill({ json: results })
  })
  return { request, release }
}

test.beforeEach(async ({ page }) => {
  await bypassSetup(page)
  await page.route('**/api/samples', (route) => route.fulfill({ json: [7, 8].map((id) => ({
    id, name: `Palette sample ${id}`, file_format: '23andme_v5', created_at: null,
  })) }))
  await page.route('**/api/individuals', (route) => route.fulfill({ json: [] }))
  await page.route('**/api/variants/search**', (route) => route.fulfill({ json: [] }))
})

test('late gene results select the first ranked variant and Enter opens it', async ({ page }) => {
  const pending = await holdSearch(page, 'ldlr')
  try {
    const input = await openPalette(page, 'ldlr')
    await pending.request
    await expect(page.getByRole('option', { name: 'Query Builder', exact: true }))
      .toHaveAttribute('aria-selected', 'true')
    pending.release()
    const first = page.getByRole('option').first()
    await expect(first).toContainText('rs424242')
    await expect(first).toHaveAttribute('aria-selected', 'true')
    await expect(input).toHaveAttribute('aria-activedescendant', await first.getAttribute('id') ?? '')
    const audit = await new AxeBuilder({ page }).include('[cmdk-dialog]')
      .withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa']).analyze()
    expect(audit.passes.length).toBeGreaterThan(0)
    expect(audit.violations).toEqual([])
    await input.press('Enter')
    await expect(page).toHaveURL(/\/variants\/rs424242\?sample_id=7$/)
  } finally { pending.release() }
})

test('explicit keyboard selection while results are pending is preserved', async ({ page }) => {
  const pending = await holdSearch(page, 'ldlr')
  try {
    const input = await openPalette(page, 'ldlr')
    await pending.request
    await input.press('End')
    const chosen = page.getByRole('option', { name: 'Query Builder', exact: true })
    await expect(chosen).toHaveAttribute('aria-selected', 'true')
    pending.release()
    await expect(page.getByTestId('command-palette-variant-item')).toHaveCount(2)
    await expect(chosen).toHaveAttribute('aria-selected', 'true')
    await input.press('Enter')
    await expect(page).toHaveURL(/\/query-builder\?sample_id=7$/)
  } finally { pending.release() }
})

test('pointer selection is preserved and typing a new query restores automatic selection', async ({ page }) => {
  const pending = await holdSearch(page, 'ldlr')
  try {
    const input = await openPalette(page, 'ldlr')
    await pending.request
    const chosen = page.getByRole('option', { name: 'Query Builder', exact: true })
    await chosen.hover()
    pending.release()
    await expect(page.getByTestId('command-palette-variant-item')).toHaveCount(2)
    await expect(chosen).toHaveAttribute('aria-selected', 'true')
    await input.fill('ldlr ')
    const first = page.getByRole('option').first()
    await expect(first).toContainText('rs424242')
    await expect(first).toHaveAttribute('aria-selected', 'true')
    await input.press('ArrowDown')
    await expect(page.getByTestId('command-palette-variant-item').nth(1))
      .toHaveAttribute('aria-selected', 'true')
    await input.press('Enter')
    await expect(page).toHaveURL(/\/variants\/rs424243\?sample_id=7$/)
  } finally { pending.release() }
})

test('a previous sample response cannot replace the current sample results', async ({ page }) => {
  const old = await holdSearch(page, 'ldlr')
  const current = await holdSearch(page, 'ldlr', '8', [{
    rsid: 'rs818181', gene_symbol: 'LDLR', clinvar_significance: null,
  }])
  try {
    const input = await openPalette(page, 'ldlr')
    await old.request
    await input.press('End')
    // Exercise an external router sample change without closing the palette.
    await page.evaluate(() => {
      history.pushState(null, '', '/settings/general?sample_id=8')
      dispatchEvent(new PopStateEvent('popstate'))
    })
    await current.request
    current.release()
    const first = page.getByRole('option').first()
    await expect(first).toContainText('rs818181')
    await expect(first).toHaveAttribute('aria-selected', 'true')
    const staleResponse = page.waitForResponse((response) => {
      const url = new URL(response.url())
      return url.pathname === '/api/variants/search' && url.searchParams.get('sample_id') === '7'
    })
    old.release()
    await (await staleResponse).finished()
    await expect(page.getByTestId('command-palette-variant-item')).toHaveCount(1)
    await expect(first).toContainText('rs818181')
    await input.press('Enter')
    await expect(page).toHaveURL(/\/variants\/rs818181\?sample_id=8$/)
  } finally { old.release(); current.release() }
})

test('a stale query response cannot steal the current selection', async ({ page }) => {
  const pending = await holdSearch(page, 'ldlr')
  try {
    const input = await openPalette(page, 'ldlr')
    await pending.request
    await input.fill('Cancer')
    const cancer = page.getByRole('option', { name: 'Cancer', exact: true })
    await expect(cancer).toHaveAttribute('aria-selected', 'true')
    const response = page.waitForResponse((response) => new URL(response.url()).searchParams.get('q') === 'ldlr')
    pending.release()
    await (await response).finished()
    await expect(page.getByTestId('command-palette-variant-item')).toHaveCount(0)
    await expect(cancer).toHaveAttribute('aria-selected', 'true')
    await input.press('Enter')
    await expect(page).toHaveURL(/\/cancer\?sample_id=7$/)
  } finally { pending.release() }
})

test('late rsID results keep the first-ranked Genome Browser action selected', async ({ page }) => {
  const pending = await holdSearch(page, 'rs424242')
  try {
    const input = await openPalette(page, 'rs424242')
    await pending.request
    pending.release()
    const first = page.getByRole('option').first()
    await expect(page.getByTestId('command-palette-variant-item')).toHaveCount(2)
    await expect(first).toHaveAttribute('data-testid', 'command-palette-igv-item')
    await expect(first).toHaveAttribute('aria-selected', 'true')
    await input.press('Enter')
    await expect(page).toHaveURL(/\/genome-browser\?locus=rs424242&sample_id=7$/)
  } finally { pending.release() }
})

test('the Genome Browser action is selected when it ranks first', async ({ page }) => {
  const input = await openPalette(page, 'chr1:100-200')
  const first = page.getByRole('option').first()
  await expect(first).toHaveAttribute('data-testid', 'command-palette-igv-item')
  await expect(first).toHaveAttribute('aria-selected', 'true')
  await input.press('Enter')
  await expect(page).toHaveURL(/\/genome-browser\?locus=chr1%3A100-200&sample_id=7$/)
})

for (const status of [200, 503]) {
  test(`empty or failed search (${status}) retains page navigation`, async ({ page }) => {
    await page.route('**/api/variants/search**', (route) => route.fulfill({ status, json: [] }))
    const input = await openPalette(page, 'ldlr')
    const chosen = page.getByRole('option', { name: 'Query Builder', exact: true })
    await expect(chosen).toHaveAttribute('aria-selected', 'true')
    await expect(page.getByTestId('command-palette-variant-item')).toHaveCount(0)
    await input.press('Enter')
    await expect(page).toHaveURL(/\/query-builder\?sample_id=7$/)
  })
}
