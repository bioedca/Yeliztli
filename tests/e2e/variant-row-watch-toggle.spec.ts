/**
 * Issue #2123 — the variant table had no per-row watch affordance. Watching was
 * reachable only by clicking a row, noticing the detail panel, and finding the
 * button inside it, so a user browsing the table had no in-place way to watch
 * what they were looking at. (#1995 fixed the sidebar's empty-state copy, which
 * had pointed at a nonexistent "eye icon on a variant"; this adds the control
 * that copy was describing.)
 *
 * Covers the three things the issue flags as traps, through the real browser:
 * the toggle must not also open the row's detail panel, the toggle and the
 * Watching sidebar must stay in sync, and the control must be keyboard
 * reachable.
 */

import { test, expect } from '@playwright/test'
import AxeBuilder from '@axe-core/playwright'
import { bypassSetup, waitForReactHydration } from './helpers'

const jsonRoute = (payload: unknown, status = 200) => ({
  status,
  contentType: 'application/json',
  body: JSON.stringify(payload),
})

const VARIANTS = [
  { rsid: 'rs1000', pos: 1000 },
  { rsid: 'rs2000', pos: 2000 },
]

function variantPage() {
  return {
    items: VARIANTS.map(({ rsid, pos }) => ({
      rsid,
      chrom: '1',
      pos,
      ref: 'A',
      alt: 'G',
      genotype: 'AG',
      zygosity: 'het',
      gene_symbol: 'GENE1',
      consequence: 'missense_variant',
      clinvar_significance: null,
      clinvar_review_stars: null,
      gnomad_af_global: 0.01,
      rare_flag: false,
      cadd_phred: 10,
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
    })),
    next_cursor_chrom: null,
    next_cursor_pos: null,
    has_more: false,
    // `limit` matters: without it the infinite query's next-page handling can
    // request another page and append these same rows a second time, which
    // shows up as duplicate per-row controls.
    limit: 100,
  }
}

test.beforeEach(async ({ page }) => {
  await bypassSetup(page)

  // Server-side watch list, mutated by the POST/DELETE the toggle issues, so the
  // round trip and the sidebar refresh are both exercised rather than stubbed.
  let watched: string[] = []

  // Anchored on the port so the glob cannot also match Vite's module request
  // for the app's own `/src/api/watches.ts` — `**` crosses `/`, so
  // `**/api/watches**` matches that too and serves JSON where the browser
  // expects JavaScript, blanking the page.
  await page.route(/:\d+\/api\/watches/, async (route) => {
    const request = route.request()
    const method = request.method()
    const url = request.url()

    if (method === 'POST') {
      const body = JSON.parse(request.postData() ?? '{}')
      if (!watched.includes(body.rsid)) watched.push(body.rsid)
      return route.fulfill(jsonRoute({ rsid: body.rsid }))
    }
    if (method === 'DELETE') {
      const rsid = decodeURIComponent(url.split('/api/watches/')[1].split('?')[0])
      watched = watched.filter((w) => w !== rsid)
      return route.fulfill(jsonRoute({}))
    }
    return route.fulfill(
      jsonRoute(watched.map((rsid) => ({ rsid, watched_at: '2026-10-15T00:00:00Z' }))),
    )
  })

  await page.route(/:\d+\/api\/variants\/count/, (route) =>
    route.fulfill(jsonRoute({ total: VARIANTS.length, filtered: false })),
  )
  await page.route(/:\d+\/api\/variants\/chromosomes/, (route) =>
    route.fulfill(jsonRoute([{ chrom: '1', count: VARIANTS.length }])),
  )
  await page.route(/:\d+\/api\/samples\/1\/merge-provenance/, (route) =>
    route.fulfill(jsonRoute(null)),
  )
  await page.route(/:\d+\/api\/tags/, (route) => route.fulfill(jsonRoute([])))
  await page.route(/:\d+\/api\/column-presets/, (route) =>
    route.fulfill(jsonRoute({ presets: [] })),
  )
  await page.route(/:\d+\/api\/variants\?/, (route) => route.fulfill(jsonRoute(variantPage())))
})

// `exact: true` throughout: Playwright matches an accessible name as a
// case-insensitive *substring*, so { name: 'Watch rs1000' } would also match
// the Watching sidebar's own 'Unwatch rs1000' control and resolve to two
// elements. (Testing Library's getByRole matches the whole string, which is
// why the component tests did not surface this.)
test.describe('Per-row watch toggle in the variant table (#2123)', () => {
  test('every row offers a watch control without opening the row', async ({ page }) => {
    await page.goto('/variants?sample_id=1')
    await waitForReactHydration(page)

    const firstToggle = page.getByRole('button', { name: 'Watch rs1000', exact: true })
    await expect(firstToggle).toBeVisible()
    await expect(page.getByRole('button', { name: 'Watch rs2000', exact: true })).toBeVisible()
    // The affordance exists before any row has been opened — the whole point.
    await expect(firstToggle).toHaveAttribute('aria-pressed', 'false')

    await firstToggle.click()

    // Pressed state reflects the watch, and the row's detail panel did NOT open:
    // without stopPropagation the row handler fires too and the user gets a
    // panel they did not ask for.
    await expect(firstToggle).toHaveAttribute('aria-pressed', 'true')
    await expect(page.getByRole('dialog', { name: /Variant detail for rs1000/ })).toHaveCount(0)
  })

  test('watching from a row populates the Watching sidebar', async ({ page }) => {
    await page.goto('/variants?sample_id=1')
    await waitForReactHydration(page)

    await page.getByRole('button', { name: 'Watch rs2000', exact: true }).click()
    await expect(page.getByRole('button', { name: 'Watch rs2000', exact: true })).toHaveAttribute(
      'aria-pressed',
      'true',
    )

    // The sidebar reads the same per-sample watch list, so the row toggle and
    // the sidebar stay in sync through the shared query cache.
    const watchedList = page.getByRole('list', { name: 'Watched variants' })
    await expect(watchedList).toContainText('rs2000')
  })

  test('unwatching from the row removes it from the sidebar', async ({ page }) => {
    await page.goto('/variants?sample_id=1')
    await waitForReactHydration(page)

    const toggle = page.getByRole('button', { name: 'Watch rs1000', exact: true })
    await toggle.click()
    await expect(toggle).toHaveAttribute('aria-pressed', 'true')
    await expect(page.getByRole('list', { name: 'Watched variants' })).toContainText('rs1000')

    await toggle.click()

    await expect(toggle).toHaveAttribute('aria-pressed', 'false')
    await expect(
      page.getByRole('list', { name: 'Watched variants' }).getByText('rs1000', { exact: true }),
    ).toHaveCount(0)
  })

  test('a click on a disabled toggle does not open the row', async ({ page }) => {
    // A disabled button never runs the component's stopPropagation handler, so
    // a pointer click landing on its nested icon could bubble to the row and
    // open the detail panel exactly while the control is unavailable. The icons
    // are pointer-events-none so the hit target stays the disabled button.
    //
    // Honest about what this proves: removing `pointer-events-none` does NOT
    // make this test fail in chromium or firefox, both of which already
    // suppress the bubble from a disabled control's children. It is kept as a
    // guard for the behaviour we want, and because WebKit — which CI runs and
    // which is not installed in the dev sandbox — was the browser the finding
    // named. Treat it as a regression guard, not as a demonstration of the
    // vulnerability.
    let releaseWatches: () => void = () => {}
    await page.unroute(/:\d+\/api\/watches/)
    await page.route(/:\d+\/api\/watches/, async (route) => {
      if (route.request().method() === 'GET') {
        await new Promise<void>((resolve) => {
          releaseWatches = resolve
        })
      }
      return route.fulfill(jsonRoute([]))
    })

    await page.goto('/variants?sample_id=1')
    await waitForReactHydration(page)

    const toggle = page.getByRole('button', { name: 'Watch rs1000', exact: true })
    await expect(toggle).toBeVisible()
    await expect(toggle).toBeDisabled()

    // force: true because Playwright refuses to click a disabled control; the
    // point is precisely what a real pointer event does here.
    await toggle.click({ force: true })

    await expect(page.getByRole('dialog', { name: /Variant detail for rs1000/ })).toHaveCount(0)
    releaseWatches()
  })

  test('the toggle column passes an axe scan in both states', async ({ page }) => {
    await page.goto('/variants?sample_id=1')
    await waitForReactHydration(page)

    const toggle = page.getByRole('button', { name: 'Watch rs1000', exact: true })
    await expect(toggle).toBeVisible()

    for (const state of ['unwatched', 'watched'] as const) {
      if (state === 'watched') {
        await toggle.click()
        await expect(toggle).toHaveAttribute('aria-pressed', 'true')
      }
      const accessibility = await new AxeBuilder({ page })
        .include('[data-testid="variant-table-region"], table')
        .analyze()
      expect(
        accessibility.violations.map(({ id, impact, nodes }) => ({
          id,
          impact,
          nodes: nodes.map(({ target, failureSummary }) => ({ target, failureSummary })),
        })),
        `${state} toggle accessibility violations`,
      ).toEqual([])
    }
  })

  test('the toggle is reachable and operable from the keyboard', async ({ page }) => {
    await page.goto('/variants?sample_id=1')
    await waitForReactHydration(page)

    const toggle = page.getByRole('button', { name: 'Watch rs1000', exact: true })
    await expect(toggle).toBeVisible()

    await toggle.focus()
    await expect(toggle).toBeFocused()
    await page.keyboard.press('Enter')

    await expect(toggle).toHaveAttribute('aria-pressed', 'true')
    // Keyboard activation must not open the row either.
    await expect(page.getByRole('dialog', { name: /Variant detail for rs1000/ })).toHaveCount(0)
  })
})
