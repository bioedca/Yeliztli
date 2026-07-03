import { expect, type Page } from '@playwright/test'

const WIZARD_DATA_DIR = '/tmp/.yeliztli'

/**
 * Mock the *static* setup-wizard chrome endpoints (auth, disclaimer, detect,
 * storage, credentials) so a spec can drive the wizard to the Databases step
 * without a real backend. The dynamic endpoints — `/api/setup/status`,
 * `/api/databases`, `/api/databases/download`, `/api/databases/progress/**`,
 * `/api/databases/health` — stay the caller's responsibility because each spec
 * stages them differently. `disclaimer_accepted` is reported true so the wizard
 * starts on the Import step.
 */
export async function mockWizardChrome(page: Page): Promise<void> {
  const json = (body: unknown) => ({
    status: 200,
    contentType: 'application/json',
    body: JSON.stringify(body),
  })
  await page.route('**/api/auth/status', (route) =>
    route.fulfill(json({ auth_enabled: false, has_password: false, authenticated: true })),
  )
  await page.route('**/api/setup/disclaimer', (route) =>
    route.fulfill(
      json({
        title: 'Disclaimer',
        text: 'For research / educational use only.',
        accept_label: 'I Understand and Accept',
      }),
    ),
  )
  await page.route('**/api/setup/detect-existing', (route) =>
    route.fulfill(
      json({
        existing_found: false,
        has_config: false,
        has_samples: false,
        has_databases: false,
        data_dir: WIZARD_DATA_DIR,
      }),
    ),
  )
  await page.route('**/api/setup/storage-info', (route) =>
    route.fulfill(
      json({
        data_dir: WIZARD_DATA_DIR,
        free_space_bytes: 100_000_000_000,
        free_space_gb: 100,
        total_space_bytes: 500_000_000_000,
        total_space_gb: 500,
        status: 'ok',
        message: 'Storage looks good.',
        path_exists: true,
        path_writable: true,
      }),
    ),
  )
  await page.route('**/api/setup/set-storage-path', (route) =>
    route.fulfill(
      json({
        success: true,
        data_dir: WIZARD_DATA_DIR,
        free_space_gb: 100,
        status: 'ok',
        message: 'Storage path saved.',
      }),
    ),
  )
  await page.route('**/api/setup/credentials', (route) =>
    route.request().method() === 'POST'
      ? route.fulfill(json({ success: true, message: 'Saved.' }))
      : route.fulfill(json({ pubmed_email: '', ncbi_api_key: '', omim_api_key: '' })),
  )
}

/** Drive the wizard from /setup through to the Databases step. */
export async function walkWizardToDatabases(page: Page): Promise<void> {
  await page.goto('/setup')
  await page.waitForLoadState('domcontentloaded')
  await page.getByRole('button', { name: /Skip — Start Fresh/i }).click()
  await expect(page.getByRole('heading', { name: /Storage Location/i })).toBeVisible()
  await page.getByRole('button', { name: 'Continue' }).click()
  await expect(page.getByRole('heading', { name: 'External Services' })).toBeVisible()
  await page.getByLabel(/PubMed email address/i).fill('e2e@example.com')
  await page.getByRole('button', { name: 'Continue' }).click()
  await expect(page.getByRole('heading', { name: /Reference Databases/i })).toBeVisible()
}

/**
 * Stub AuthGuard endpoints so protected app routes render without depending on
 * live backend auth/setup state.
 *
 * The dashboard gate is health-based: `needs_setup` stays true until every
 * setup-gated required reference DB is integrity-`ready`. E2E specs that drive
 * real module pages don't seed real multi-GB databases, so they intercept the
 * status poll to report a ready install — the same `page.route` pattern the
 * setup-wizard specs already use, factored out here so these specs no longer
 * depend on the (now health-gated) on-disk bypass that `global-setup.ts` wrote.
 *
 * Call once per test via a file-level `test.beforeEach`. Register it before the
 * first `page.goto` so the very first AuthGuard poll is intercepted.
 */
export async function bypassSetup(page: Page): Promise<void> {
  await page.route('**/api/auth/status', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        auth_enabled: false,
        has_password: false,
        authenticated: true,
      }),
    })
  })
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
