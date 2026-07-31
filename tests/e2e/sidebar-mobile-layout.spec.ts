import { test, expect, type Page } from '@playwright/test'
import { bypassSetup, waitForReactHydration } from './helpers'

const jsonRoute = (payload: unknown, status = 200) => ({
  status,
  contentType: 'application/json',
  body: JSON.stringify(payload),
})

const watchedVariants = Array.from({ length: 6 }, (_, index) => ({
  rsid: `rs90${index + 1}`,
  watched_at: `2026-07-${String(index + 1).padStart(2, '0')}T00:00:00Z`,
  clinvar_significance_at_watch: 'Uncertain significance',
  clinvar_significance_current: 'Uncertain significance',
  notes: null,
}))

async function stubVariantExplorer(page: Page) {
  await page.route(/\/api\/column-presets(\?|\/|$)/, (route) =>
    route.fulfill(jsonRoute({ presets: [] })),
  )
  await page.route(/\/api\/tags(\?|$)/, (route) => route.fulfill(jsonRoute([])))
  await page.route(/\/api\/variants\/count(\?|$)/, (route) =>
    route.fulfill(jsonRoute({ total: 1, filtered: false })),
  )
  await page.route(/\/api\/variants\/chromosomes(\?|$)/, (route) =>
    route.fulfill(jsonRoute([{ chrom: '1', count: 1 }])),
  )
  await page.route(/\/api\/variants(\?[^/]*)?$/, (route) =>
    route.fulfill(
      jsonRoute({
        items: [
          {
            rsid: 'rs2010',
            chrom: '1',
            pos: 2010,
            genotype: 'AG',
            ref: 'A',
            alt: 'G',
            zygosity: 'het',
            carriage_status: 'carried',
            gene_symbol: 'BRCA1',
            consequence: 'missense_variant',
            clinvar_significance: 'Uncertain significance',
            clinvar_review_stars: 1,
            gnomad_af_global: 0.01,
            rare_flag: false,
            cadd_phred: 12.5,
            sift_score: null,
            sift_pred: null,
            polyphen2_hsvar_score: null,
            polyphen2_hsvar_pred: null,
            revel: null,
            annotation_coverage: 0b111111,
            evidence_conflict: false,
            ensemble_pathogenic: false,
            chrom_grch38: null,
            pos_grch38: null,
            tags: [],
            source: '',
            concordance: '',
          },
        ],
        next_cursor_chrom: null,
        next_cursor_pos: null,
        has_more: false,
        limit: 100,
      }),
    ),
  )
  await page.route(/\/api\/samples\/\d+\/merge-provenance$/, (route) =>
    route.fulfill(jsonRoute({ detail: 'Sample is not a merged sample' }, 404)),
  )
  await page.route(/\/api\/watches(\?|$)/, (route) =>
    route.fulfill(jsonRoute(watchedVariants)),
  )
}

test.beforeEach(async ({ page }) => {
  await bypassSetup(page)
  await page.route('**/api/samples', (route) =>
    route.fulfill({ status: 200, contentType: 'application/json', body: '[]' }),
  )
  await page.route('**/api/individuals', (route) =>
    route.fulfill({ status: 200, contentType: 'application/json', body: '[]' }),
  )
})

test.describe('mobile app navigation layout', () => {
  test('keeps the main content full width on phone viewports (#1515)', async ({ page }) => {
    await page.setViewportSize({ width: 375, height: 800 })
    await page.goto('/not-a-real-route')
    await waitForReactHydration(page)

    const viewportWidth = page.viewportSize()?.width ?? 375
    const main = page.locator('#main-content')
    const nav = page.getByRole('navigation', { name: 'Main navigation' })
    const mainBox = await main.boundingBox()
    const navBox = await nav.boundingBox()
    const mainPrecedesNav = await nav.evaluate((navElement) => {
      const mainElement = document.querySelector('#main-content')

      if (!mainElement) {
        return false
      }

      return Boolean(mainElement.compareDocumentPosition(navElement) & Node.DOCUMENT_POSITION_FOLLOWING)
    })

    expect(mainBox).not.toBeNull()
    expect(navBox).not.toBeNull()
    expect(mainBox!.width).toBeGreaterThanOrEqual(viewportWidth - 2)
    expect(navBox!.width).toBeGreaterThanOrEqual(viewportWidth - 2)
    expect(navBox!.height).toBeLessThan(100)
    expect(mainBox!.y + mainBox!.height).toBeLessThanOrEqual(navBox!.y + 1)
    expect(mainPrecedesNav).toBe(true)
  })

  test('keeps the Variant Explorer table usable beside Watching on phones (#2010)', async ({
    page,
  }) => {
    await stubVariantExplorer(page)
    await page.setViewportSize({ width: 375, height: 800 })
    await page.goto('/variants?sample_id=1')
    await waitForReactHydration(page)

    await expect(page.getByText('rs2010')).toBeVisible()

    const sidebar = page.getByRole('complementary', { name: 'Variant table sidebar' })
    const table = page.getByRole('region', { name: 'Variant table' })
    const row = sidebar.locator('..')
    const [sidebarBox, tableBox, rowBox, flexDirection] = await Promise.all([
      sidebar.boundingBox(),
      table.boundingBox(),
      row.boundingBox(),
      row.evaluate((element) => getComputedStyle(element).flexDirection),
    ])

    expect(sidebarBox).not.toBeNull()
    expect(tableBox).not.toBeNull()
    expect(rowBox).not.toBeNull()
    expect(flexDirection).toBe('column')
    expect(sidebarBox!.width).toBeGreaterThanOrEqual(rowBox!.width - 2)
    expect(sidebarBox!.height).toBeLessThanOrEqual(160)
    expect(tableBox!.width).toBeGreaterThanOrEqual(rowBox!.width - 2)
    expect(sidebarBox!.y + sidebarBox!.height).toBeLessThanOrEqual(tableBox!.y + 1)
  })
})
