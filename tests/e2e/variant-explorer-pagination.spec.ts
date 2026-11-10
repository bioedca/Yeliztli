/** Synthetic same-position page boundaries and chromosome seeks (#2449). */
import AxeBuilder from '@axe-core/playwright'
import { test, expect, type Page } from '@playwright/test'
import { bypassSetup, waitForReactHydration } from './helpers'

const rows = ['1', 'X'].flatMap((chrom) =>
  Array.from({ length: 101 }, (_, index) => ({
    rsid: `iYELIZTLI_${chrom}_${String(index).padStart(3, '0')}`,
    chrom,
    pos: chrom === '1' ? 20 : 5,
    genotype: '--',
    annotation_coverage: 0,
    tags: [],
    source: '',
    concordance: '',
    alt_rsid: '',
  })),
)

const json = (body: unknown, status = 200) => ({
  status, contentType: 'application/json', body: JSON.stringify(body),
})

async function stubExplorer(page: Page) {
  const requests: URLSearchParams[] = []
  await bypassSetup(page)
  await page.route(/\/api\/column-presets(\?|\/|$)/, (route) => route.fulfill(json({ presets: [] })))
  await page.route(/\/api\/(tags|watches)(\?|$)/, (route) => route.fulfill(json([])))
  await page.route(/\/api\/samples\/\d+\/merge-provenance$/, (route) => route.fulfill(json(null)))
  await page.route(/\/api\/variants\/chromosomes(\?|$)/, (route) =>
    route.fulfill(json([{ chrom: '1', count: 101 }, { chrom: 'X', count: 101 }])),
  )
  await page.route(/\/api\/variants\/count(\?|$)/, (route) => {
    const filter = new URL(route.request().url()).searchParams.get('filter')
    return route.fulfill(json({ total: filter === 'annotation_coverage:null' ? 0 : rows.length }))
  })
  await page.route(/\/api\/variants(\?[^/]*)?$/, (route) => {
    const params = new URL(route.request().url()).searchParams
    requests.push(params)
    let start = 0
    if (params.has('cursor_rsid')) {
      const index = rows.findIndex((row) => row.rsid === params.get('cursor_rsid'))
      if (index < 0 || rows[index].chrom !== params.get('cursor_chrom') ||
          String(rows[index].pos) !== params.get('cursor_pos')) {
        return route.fulfill(json({ detail: 'Invalid complete cursor' }, 422))
      }
      start = index + 1
    } else if (params.has('cursor_chrom')) {
      // Only the explicit chromosome jump may omit the primary-key cursor.
      if (params.get('cursor_pos') !== '0' || params.get('cursor_chrom') !== 'X') {
        return route.fulfill(json({ detail: 'Missing continuation rsID' }, 422))
      }
      start = 101
    }
    const items = rows.slice(start, start + 100)
    const hasMore = start + items.length < rows.length
    const last = hasMore ? items.at(-1) : null
    return route.fulfill(json({
      items,
      has_more: hasMore,
      next_cursor_chrom: last?.chrom ?? null,
      next_cursor_pos: last?.pos ?? null,
      next_cursor_rsid: last?.rsid ?? null,
      limit: 100,
    }))
  })
  return requests
}

test('scrolling and chromosome jumps retain same-position variants exactly once', async ({ page }) => {
  const requests = await stubExplorer(page)
  await page.emulateMedia({ reducedMotion: 'reduce' })
  await page.goto('/variants?sample_id=1')
  await waitForReactHydration(page)
  const table = page.getByRole('region', { name: 'Variant table', exact: true })
  const scrollToEnd = () => table.evaluate((element) => {
    element.scrollTop = element.scrollHeight
  })
  const renderedIds = () => table.getByText(/^iYELIZTLI_[1X]_\d{3}$/).allTextContents()
  await expect(table.getByText(rows[0].rsid, { exact: true })).toBeVisible()
  await scrollToEnd()
  await expect(table.getByText(rows[100].rsid, { exact: true })).toHaveCount(1)
  await scrollToEnd()
  await expect(table.getByText(rows.at(-1)!.rsid, { exact: true })).toHaveCount(1)
  await scrollToEnd()
  await expect(table.getByText(rows.at(-1)!.rsid, { exact: true })).toBeVisible()
  await expect(table.locator('tbody tr')).toHaveCount(rows.length)
  expect(await renderedIds()).toEqual(rows.map((row) => row.rsid))
  expect(requests.map((params) => params.get('cursor_rsid'))).toEqual([
    null, rows[99].rsid, rows[199].rsid,
  ])

  const jump = page.getByRole('button', { name: 'Jump to chromosome X, 101 variants', exact: true })
  await jump.focus()
  await page.keyboard.press('Enter')
  await expect(jump).toHaveAttribute('aria-current', 'location')
  await expect(table.getByText(rows[101].rsid, { exact: true })).toBeVisible()
  await expect(table.getByText(rows[0].rsid, { exact: true })).toHaveCount(0)
  await scrollToEnd()
  await expect(table.getByText(rows.at(-1)!.rsid, { exact: true })).toHaveCount(1)
  await scrollToEnd()
  await expect(table.getByText(rows.at(-1)!.rsid, { exact: true })).toBeVisible()
  expect(Object.fromEntries(requests[3])).toMatchObject({ cursor_chrom: 'X', cursor_pos: '0' })
  expect(requests[3].has('cursor_rsid')).toBe(false)
  expect(Object.fromEntries(requests[4])).toMatchObject({
    cursor_chrom: 'X', cursor_pos: '5', cursor_rsid: rows[200].rsid,
  })
  expect(requests).toHaveLength(5)
  await expect(table.locator('tbody tr')).toHaveCount(101)
  expect(await renderedIds()).toEqual(rows.slice(101).map((row) => row.rsid))
  const accessibility = await new AxeBuilder({ page })
    .include('[aria-label="Chromosome navigation"]')
    .include('[aria-label="Variant table"]')
    .analyze()
  expect(accessibility.violations).toEqual([])
})
