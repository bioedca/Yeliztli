/**
 * Issue #601 — the variant table's loading indicators ("Loading variants…" on the
 * initial load) rendered as a plain spinner + text with no `role="status"` /
 * aria-live, so screen-reader users were never told variants were loading. The fix
 * wraps the indicator in a `role="status"` live region and hides the decorative
 * spinner from assistive tech.
 *
 * The loading state is transient, so this HOLDS the list response (`/api/variants?…`)
 * open while it asserts the live region is present, then releases it.
 */

import { test, expect } from '@playwright/test'
import { bypassSetup, waitForReactHydration } from './helpers'

test.beforeEach(async ({ page }) => {
  await bypassSetup(page)
})

const jsonRoute = (payload: unknown, status = 200) => ({
  status,
  contentType: 'application/json',
  body: JSON.stringify(payload),
})

test.describe('Variant table loading state is announced to screen readers (#601)', () => {
  test('the "Loading variants…" indicator is a role=status live region', async ({ page }) => {
    // Sibling queries — stub so they don't error (they do not gate the loading td,
    // which is driven by the list query's pending state, but keep the page clean).
    await page.route('**/api/variants/count**', (route) => route.fulfill(jsonRoute({ total: 5 })))
    await page.route('**/api/variants/chromosomes**', (route) => route.fulfill(jsonRoute([])))

    // Hold the list response so the pending/loading state stays on screen. The
    // list endpoint is `/api/variants?…` — disjoint from the `/count` & `/chromosomes`
    // sub-paths above (those have no literal "variants?" segment).
    let release: () => void = () => {}
    const held = new Promise<void>((resolve) => {
      release = resolve
    })
    await page.route(/\/api\/variants\?/, async (route) => {
      await held
      await route.fulfill(
        jsonRoute({ items: [], next_cursor_chrom: null, next_cursor_pos: null, has_more: false }),
      )
    })

    await page.goto('/variants?sample_id=1')
    await waitForReactHydration(page)

    // While the list request is pending, the loading indicator is a status region
    // whose accessible content names the loading state...
    const status = page.getByRole('status').filter({ hasText: /Loading variants/ })
    await expect(status).toBeVisible()
    await expect(status).toContainText('Loading variants')
    // ...and the decorative spinner is hidden from assistive tech.
    await expect(status.locator('svg')).toHaveAttribute('aria-hidden', 'true')

    release()
  })
})
