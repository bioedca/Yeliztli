import type { Page } from '@playwright/test'

/**
 * Stub `/api/setup/status` so AuthGuard treats the install as fully set up.
 *
 * The dashboard gate is health-based: `needs_setup` stays true until every
 * required, downloadable reference DB is integrity-`ready`. E2E specs that drive
 * real module pages don't seed real multi-GB databases, so they intercept the
 * status poll to report a ready install — the same `page.route` pattern the
 * setup-wizard specs already use, factored out here so these specs no longer
 * depend on the (now health-gated) on-disk bypass that `global-setup.ts` wrote.
 *
 * Call once per test via a file-level `test.beforeEach`. Register it before the
 * first `page.goto` so the very first AuthGuard poll is intercepted.
 */
export async function bypassSetup(page: Page): Promise<void> {
  await page.route('**/api/setup/status', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        needs_setup: false,
        disclaimer_accepted: true,
        has_databases: true,
        required_dbs_ready: true,
        db_readiness: [],
        has_samples: true,
        data_dir: '/tmp/.yeliztli',
      }),
    })
  })
}

/**
 * Wait until React has hydrated AppLayout.
 *
 * `networkidle` is unreliable for this purpose because the dev server can
 * return an empty `<div id="root">` shell and no further requests follow, so
 * the load state resolves before mount. Once the page-level `<h1>` is visible,
 * AppLayout + the page component have rendered and DOM-inspection assertions
 * are safe to run.
 *
 * Assumes the route under test renders an `<h1>` (every page in this app
 * does). Subject to Playwright's default action timeout, so it will throw if
 * no `<h1>` mounts. If a future page omits its h1, prefer changing the page
 * to render one over loosening this gate — that keeps the readiness signal
 * tied to a real hydration milestone.
 */
export async function waitForReactHydration(page: Page): Promise<void> {
  await page.locator('h1').first().waitFor({ state: 'visible' })
}
