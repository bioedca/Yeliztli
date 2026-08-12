/**
 * #2027: the Watching sidebar's sort toggle set an `aria-label` naming the mode a
 * click would switch *to*, while its visible text named the mode currently in
 * effect. Both branched on the same condition and returned opposite modes, so the
 * accessible name and the visible text were disjoint in BOTH states.
 *
 * That fails WCAG 2.5.3 Label in Name: a voice-control user saying "click Sort:
 * Date watched" could not reach the control, because none of the words on screen
 * appeared in its accessible name.
 *
 * These cases run in a real browser, so they check the browser's own accessible-name
 * computation rather than a jsdom approximation of it. `getByRole(..., { name })`
 * resolves by accessible name (substring, case-insensitive) while `toHaveText`
 * asserts the visible text, so the pair together assert containment in that
 * direction — which is exactly what 2.5.3 requires.
 */

import { test, expect } from '@playwright/test'
import { bypassSetup, waitForReactHydration } from './helpers'

const jsonRoute = (payload: unknown, status = 200) => ({
  status,
  contentType: 'application/json',
  body: JSON.stringify(payload),
})

/** Two watches, one of them reclassified, so both sort modes have something to order. */
const watchedVariants = [
  {
    rsid: 'rs2027',
    watched_at: '2026-03-02T12:00:00',
    clinvar_significance_at_watch: 'Uncertain significance',
    clinvar_significance_current: 'Uncertain significance',
    notes: '',
  },
  {
    rsid: 'rs2028',
    watched_at: '2026-03-01T12:00:00',
    clinvar_significance_at_watch: 'Uncertain significance',
    clinvar_significance_current: 'Pathogenic',
    notes: '',
  },
]

const variantPage = {
  items: [],
  next_cursor_chrom: null,
  next_cursor_pos: null,
  has_more: false,
  limit: 100,
}

test.beforeEach(async ({ page }) => {
  await bypassSetup(page)
  await page.route(/\/api\/column-presets(\?|\/|$)/, (route) =>
    route.fulfill(jsonRoute({ presets: [] })),
  )
  await page.route(/\/api\/tags(\?|$)/, (route) => route.fulfill(jsonRoute([])))
  await page.route(/\/api\/variants\/chromosomes(\?|$)/, (route) => route.fulfill(jsonRoute([])))
  await page.route(/\/api\/variants\/count(\?|$)/, (route) =>
    route.fulfill(jsonRoute({ total: 0, filtered: false })),
  )
  await page.route(/\/api\/variants(\?[^/]*)?$/, (route) => route.fulfill(jsonRoute(variantPage)))
  await page.route(/\/api\/samples\/\d+\/merge-provenance$/, (route) =>
    route.fulfill(jsonRoute({ detail: 'Sample is not a merged sample' }, 404)),
  )
  await page.route(/\/api\/watches(\?|$)/, (route) => route.fulfill(jsonRoute(watchedVariants)))
})

test('watch-list sort toggle is reachable by its visible label in both states (#2027)', async ({
  page,
}) => {
  await page.goto('/variants?sample_id=1')
  await waitForReactHydration(page)

  // Default state: sorted by date watched. The accessible name must contain the
  // words the user can see, or voice control cannot address the button.
  const dateWatched = page.getByRole('button', { name: 'Sort: Date watched' })
  await expect(dateWatched).toBeVisible()
  await expect(dateWatched).toHaveText('Sort: Date watched')

  await dateWatched.click()

  // Second state was broken the same way, so it needs its own assertion rather
  // than being assumed to follow from the first.
  const reclassifiedFirst = page.getByRole('button', { name: 'Sort: Reclassified first' })
  await expect(reclassifiedFirst).toBeVisible()
  await expect(reclassifiedFirst).toHaveText('Sort: Reclassified first')
})
