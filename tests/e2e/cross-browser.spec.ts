/**
 * P4-26d -- Cross-browser testing (Chrome, Firefox, Safari).
 *
 * Verifies core workflows render and behave consistently across all three
 * supported browsers.  Each test runs once per Playwright project defined
 * in playwright.config.ts (chromium, firefox, webkit).
 *
 * Coverage:
 *   - Page rendering (no JS errors, correct headings)
 *   - Client-side navigation between pages
 *   - Dark mode rendering
 *   - Form / interactive element behaviour
 *   - Layout consistency (viewport screenshots)
 *   - axe-core WCAG 2.1 AA compliance per browser
 */

import { test, expect, type Page } from '@playwright/test'
import AxeBuilder from '@axe-core/playwright'
import { bypassSetup, waitForReactHydration } from './helpers'

test.beforeEach(async ({ page }) => {
  await bypassSetup(page)
  await mockPassiveUpdateEndpoints(page)
})

// ── Core pages representing every major workflow area ───────────────────
const CORE_PAGES = [
  { path: '/', title: 'Dashboard' },
  { path: '/variants', title: 'Variant Explorer' },
  { path: '/pharmacogenomics', title: 'Pharmacogenomics' },
  { path: '/nutrigenomics', title: 'Nutrigenomics' },
  { path: '/cancer', title: 'Cancer' },
  { path: '/cardiovascular', title: 'Cardiovascular' },
  { path: '/fh', title: 'Familial Hypercholesterolemia' },
  { path: '/ancestry', title: 'Ancestry' },
  { path: '/carrier-status', title: 'Carrier Status' },
  { path: '/fitness', title: 'Gene Fitness' },
  { path: '/sleep', title: 'Gene Sleep' },
  { path: '/skin', title: 'Gene Skin' },
  { path: '/methylation', title: 'MTHFR & Methylation' },
  { path: '/allergy', title: 'Gene Allergy & Immune Sensitivities' },
  { path: '/traits', title: 'Traits & Personality' },
  { path: '/gene-health', title: 'Gene Health' },
  { path: '/findings', title: 'All Findings' },
  { path: '/rare-variants', title: 'Rare Variants' },
  { path: '/genome-browser', title: 'Genome Browser' },
  { path: '/query-builder', title: 'Query Builder' },
  { path: '/reports', title: 'Reports' },
  { path: '/settings', title: 'Settings' },
] as const

// Standalone (full-screen) pages
const STANDALONE_PAGES = [
  { path: '/setup', title: 'Setup Wizard' },
  { path: '/login', title: 'Login' },
] as const

// Third-party selectors excluded from axe scans (render their own DOM)
const THIRD_PARTY_EXCLUDES = [
  '.igv-container',
  '[data-testid="igv-container"]',
  '.igv-root-div',
  'nightingale-manager',
  '.monaco-editor',
]

// Pages where third-party components cause known color-contrast violations
const CONTRAST_EXCLUDED_PAGES = new Set(['/genome-browser'])

// Console message patterns that are safe to ignore across all browsers
const IGNORED_CONSOLE_PATTERNS = [
  '[vite]',
  'Download the React DevTools',
  'React does not recognize',
  '[HMR]',
  'was preloaded using link preload',
  'DevTools',
]

function isIgnoredConsoleMessage(text: string): boolean {
  return IGNORED_CONSOLE_PATTERNS.some((p) => text.includes(p))
}

const VISUAL_SCREENSHOT_SAMPLE = {
  id: 1,
  name: 'genome_Eduardo_Campos_v5_Full_20260227101528.txt',
  db_path: '/tmp/.yeliztli/samples/sample_1.db',
  file_format: '23andme_v5',
  file_hash: 'visual-screenshot-fixture',
  notes: null,
  date_collected: null,
  source: null,
  extra: null,
  created_at: '2026-06-17T12:00:00Z',
  updated_at: '2026-06-17T12:00:00Z',
}

const VISUAL_SCREENSHOT_BACKUP_ESTIMATE = {
  sample_bytes: 544_944_947,
  config_bytes: 0,
  reference_bytes: 18_683_107_738,
  total_without_ref_bytes: 544_944_947,
  total_with_ref_bytes: 19_228_052_685,
  total_without_ref_mb: 519.7,
  total_with_ref_mb: 18_337.3,
  sample_count: 1,
  reference_db_count: 5,
}

function jsonRoute(body: unknown, status = 200) {
  return {
    status,
    contentType: 'application/json',
    body: JSON.stringify(body),
  }
}

async function mockVisualScreenshotData(page: Page) {
  await page.route('**/api/samples', (route) =>
    route.fulfill(jsonRoute([VISUAL_SCREENSHOT_SAMPLE])),
  )
  await page.route(`**/api/samples/${VISUAL_SCREENSHOT_SAMPLE.id}`, (route) =>
    route.fulfill(jsonRoute(VISUAL_SCREENSHOT_SAMPLE)),
  )
  await page.route('**/api/individuals', (route) => route.fulfill(jsonRoute([])))
  await page.route('**/api/backup/estimate', (route) =>
    route.fulfill(jsonRoute(VISUAL_SCREENSHOT_BACKUP_ESTIMATE)),
  )
}

async function mockPassiveUpdateEndpoints(page: Page) {
  await page.route('**/api/preferences/update-check-interval', (route) =>
    route.fulfill(jsonRoute({ update_check_interval: 'off' })),
  )
  await page.route('**/api/updates/status', (route) => route.fulfill(jsonRoute([])))
  await page.route('**/api/updates/check', (route) =>
    route.fulfill(jsonRoute({ available: [], up_to_date: [], errors: [], checked_at: null })),
  )
  await page.route('**/api/updates/app-update', (route) =>
    route.fulfill(
      jsonRoute({
        update_available: false,
        current_version: '0.2.0',
        latest_version: null,
        release_url: null,
        release_notes: null,
        error: null,
      }),
    ),
  )
  await page.route('**/api/updates/history**', (route) => route.fulfill(jsonRoute([])))
  await page.route('**/api/updates/prompts**', (route) => route.fulfill(jsonRoute([])))
}

async function expectAppChromeSettled(page: Page) {
  await expect(page.getByRole('button', { name: 'Switch sample' })).toBeVisible()
}

// ── 1. Page rendering: no JS errors, correct h1 heading ────────────────
test.describe('P4-26d: Cross-browser — page rendering', () => {
  for (const pg of CORE_PAGES) {
    test(`${pg.title} (${pg.path}) renders without JS errors`, async ({ page }) => {
      const errors: string[] = []
      page.on('pageerror', (err) => errors.push(err.message))

      await page.goto(pg.path)
      await waitForReactHydration(page)

      // Verify h1 heading is present
      const h1 = page.getByRole('heading', { level: 1 })
      await expect(h1).toBeVisible()

      expect(errors, `JS errors on ${pg.path}:\n${errors.join('\n')}`).toEqual([])
    })
  }

  for (const pg of STANDALONE_PAGES) {
    test(`${pg.title} (${pg.path}) renders without JS errors`, async ({ page }) => {
      const errors: string[] = []
      page.on('pageerror', (err) => errors.push(err.message))

      await page.goto(pg.path)
      await waitForReactHydration(page)

      expect(errors, `JS errors on ${pg.path}:\n${errors.join('\n')}`).toEqual([])
    })
  }
})

// ── 2. Client-side navigation ──────────────────────────────────────────
test.describe('P4-26d: Cross-browser — client-side navigation', () => {
  test('navigate between multiple pages via sidebar links', async ({ page }) => {
    await page.goto('/')
    await waitForReactHydration(page)

    // Navigate to Variant Explorer using sidebar NavLink (title attribute is always present)
    const variantsLink = page.locator('nav[aria-label="Main navigation"] a[title="Variant Explorer"]')
    await variantsLink.click()
    await page.waitForURL(/\/variants/)
    await expect(page.getByRole('heading', { level: 1 })).toBeVisible()

    // Navigate to Settings
    const settingsLink = page.locator('nav[aria-label="Main navigation"] a[title="Settings"]')
    await settingsLink.click()
    await page.waitForURL(/\/settings/)
    await expect(page.getByRole('heading', { level: 1 })).toBeVisible()

    // Navigate back to Dashboard
    const dashLink = page.locator('nav[aria-label="Main navigation"] a[title="Dashboard"]')
    await dashLink.click()
    await page.waitForURL('/')
  })

  test('browser back/forward navigation works', async ({ page }) => {
    await page.goto('/')
    await waitForReactHydration(page)

    // Navigate via click (not page.goto) to build history stack
    const settingsLink = page.locator('nav[aria-label="Main navigation"] a[title="Settings"]')
    await settingsLink.click()
    await page.waitForURL(/\/settings/)

    await page.goBack()
    await page.waitForURL('/')

    await page.goForward()
    await page.waitForURL(/\/settings/)
  })

  test('direct URL navigation works for all core routes', async ({ page }) => {
    // Verify a representative subset loads directly (not via SPA navigation)
    const subset = ['/variants', '/pharmacogenomics', '/settings', '/findings']
    for (const path of subset) {
      const response = await page.goto(path)
      expect(response, `Navigation to ${path} failed`).not.toBeNull()
      expect(response!.status(), `${path} returned ${response!.status()}`).toBeLessThan(400)
      await waitForReactHydration(page)
      await expect(page.getByRole('heading', { level: 1 })).toBeVisible()
    }
  })
})

// ── 3. Dark mode rendering ─────────────────────────────────────────────
test.describe('P4-26d: Cross-browser — dark mode', () => {
  const darkModePages = [
    { path: '/', title: 'Dashboard' },
    { path: '/variants', title: 'Variant Explorer' },
    { path: '/pharmacogenomics', title: 'Pharmacogenomics' },
    { path: '/settings', title: 'Settings' },
    { path: '/fitness', title: 'Gene Fitness' },
    { path: '/ancestry', title: 'Ancestry' },
  ]

  for (const pg of darkModePages) {
    test(`${pg.title} renders in dark mode without errors`, async ({ page }) => {
      await page.emulateMedia({ colorScheme: 'dark' })

      const errors: string[] = []
      page.on('pageerror', (err) => errors.push(err.message))

      await page.goto(pg.path)
      await waitForReactHydration(page)

      await expect(page.getByRole('heading', { level: 1 })).toBeVisible()
      expect(errors).toEqual([])

      // Verify dark class is applied to the document
      const hasDarkClass = await page.evaluate(() =>
        document.documentElement.classList.contains('dark'),
      )
      // System-preference dark should trigger the dark class
      expect(hasDarkClass).toBe(true)
    })
  }
})

// ── 4. Interactive elements ────────────────────────────────────────────
test.describe('P4-26d: Cross-browser — interactive elements', () => {
  test('interactive elements exist and are tabbable', async ({ page }) => {
    await page.goto('/')
    await waitForReactHydration(page)

    // Verify interactive elements exist in the DOM with correct attributes
    // (Tab key behavior varies across browsers in headless mode)
    const links = page.locator('nav[aria-label="Main navigation"] a')
    await expect(links.first()).toBeAttached()

    // Verify skip-nav link exists for keyboard users
    const skipNav = page.locator('a[href="#main-content"]')
    await expect(skipNav).toBeAttached()

    // Verify main content is focusable (scrollable region)
    const main = page.locator('#main-content')
    await expect(main).toBeAttached()
    const tabIndex = await main.getAttribute('tabindex')
    expect(tabIndex).toBe('0')
  })

  test('command palette opens, searches, navigates, and closes (P4-26e)', async ({ page }) => {
    await page.goto('/')
    await waitForReactHydration(page)

    // Use click trigger directly (Ctrl+K behavior varies across browsers)
    const trigger = page.getByTestId('command-palette-trigger')
    await trigger.click()

    const input = page.getByTestId('command-palette-input')
    await expect(input).toBeVisible({ timeout: 3000 })

    // Verify page navigation items are visible
    await expect(page.getByRole('option', { name: /Dashboard/i })).toBeVisible()
    await expect(page.getByRole('option', { name: /Gene Fitness/i })).toBeVisible()
    await expect(page.getByRole('option', { name: /Query Builder/i })).toBeVisible()

    // Type a page name and verify filtering
    await input.fill('Pharma')
    await expect(page.getByRole('option', { name: /Pharmacogenomics/i })).toBeVisible()

    // Clear and verify no destructive actions are exposed
    await input.fill('')
    const allOptions = await page.getByRole('option').allTextContents()
    const destructiveTerms = ['delete', 'remove', 'wipe', 'reset', 'destroy']
    for (const text of allOptions) {
      for (const term of destructiveTerms) {
        expect(text.toLowerCase()).not.toContain(term)
      }
    }

    // Close with Escape
    await page.keyboard.press('Escape')
    await expect(input).not.toBeVisible({ timeout: 3000 })
  })

  test('sidebar collapse/expand works', async ({ page }) => {
    await page.goto('/')
    await waitForReactHydration(page)

    // Sidebar toggle uses aria-label "Collapse sidebar" or "Expand sidebar"
    const collapseBtn = page.locator('[aria-label="Collapse sidebar"]')
    if (await collapseBtn.isVisible()) {
      await collapseBtn.click()
      // Wait for sidebar to collapse
      const expandBtn = page.locator('[aria-label="Expand sidebar"]')
      await expect(expandBtn).toBeVisible({ timeout: 2000 })

      await expandBtn.click()
      await expect(collapseBtn).toBeVisible({ timeout: 2000 })

      // Page should still be functional
      await expect(page.getByRole('heading', { level: 1 })).toBeVisible()
    }
  })
})

// ── 5. Console error monitoring ────────────────────────────────────────
test.describe('P4-26d: Cross-browser — console errors', () => {
  const sampledPages = [
    { path: '/', title: 'Dashboard' },
    { path: '/variants', title: 'Variant Explorer' },
    { path: '/pharmacogenomics', title: 'Pharmacogenomics' },
    { path: '/settings', title: 'Settings' },
    { path: '/setup', title: 'Setup Wizard' },
  ]

  for (const pg of sampledPages) {
    test(`${pg.title} (${pg.path}) has no unexpected console errors`, async ({ page }) => {
      const consoleErrors: string[] = []
      page.on('console', (msg) => {
        if (msg.type() === 'error') {
          const text = msg.text()
          if (!isIgnoredConsoleMessage(text)) {
            consoleErrors.push(text)
          }
        }
      })

      await page.goto(pg.path)
      await waitForReactHydration(page)

      expect(
        consoleErrors,
        `Unexpected console errors on ${pg.path}:\n${consoleErrors.join('\n')}`,
      ).toEqual([])
    })
  }
})

// ── 6. Resource loading ────────────────────────────────────────────────
test.describe('P4-26d: Cross-browser — resource loading', () => {
  test('no broken static resources on Dashboard', async ({ page }) => {
    const failedRequests: string[] = []
    page.on('response', (response) => {
      // Only flag non-API static resource failures
      if (response.status() >= 400 && !response.url().includes('/api/')) {
        failedRequests.push(`${response.status()} ${response.url()}`)
      }
    })

    await page.goto('/')
    await waitForReactHydration(page)

    expect(
      failedRequests,
      `Broken resources:\n${failedRequests.join('\n')}`,
    ).toEqual([])
  })

  test('CSS and JS bundles load across pages', async ({ page }) => {
    for (const path of ['/', '/variants', '/settings']) {
      const failedAssets: string[] = []
      page.on('response', (response) => {
        const url = response.url()
        if (
          (url.endsWith('.js') || url.endsWith('.css') || url.includes('.js?') || url.includes('.css?')) &&
          response.status() >= 400
        ) {
          failedAssets.push(`${response.status()} ${url}`)
        }
      })

      await page.goto(path)
      await waitForReactHydration(page)

      expect(failedAssets, `Failed assets on ${path}`).toEqual([])
    }
  })
})

// ── 7. axe-core WCAG 2.1 AA per browser ───────────────────────────────
test.describe('P4-26d: Cross-browser — WCAG 2.1 AA compliance', () => {
  // Run axe-core on a representative subset of pages per browser
  const axePages = [
    { path: '/', title: 'Dashboard' },
    { path: '/variants', title: 'Variant Explorer' },
    { path: '/pharmacogenomics', title: 'Pharmacogenomics' },
    { path: '/settings', title: 'Settings' },
    { path: '/findings', title: 'All Findings' },
    { path: '/fitness', title: 'Gene Fitness' },
  ]

  // Pages where Firefox/WebKit axe-core reports false-positive color-contrast
  // violations due to browser-specific font rendering (passes on Chromium)
  const BROWSER_CONTRAST_PAGES = new Set(['/settings', '/setup'])

  for (const pg of axePages) {
    test(`${pg.title} (${pg.path}) passes axe-core`, async ({ page, browserName }) => {
      await page.goto(pg.path)
      await waitForReactHydration(page)

      let builder = new AxeBuilder({ page })
        .withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'])
      for (const sel of THIRD_PARTY_EXCLUDES) {
        builder = builder.exclude(sel)
      }
      if (CONTRAST_EXCLUDED_PAGES.has(pg.path)) {
        builder = builder.disableRules(['color-contrast'])
      }
      if (BROWSER_CONTRAST_PAGES.has(pg.path) && browserName !== 'chromium') {
        builder = builder.disableRules(['color-contrast'])
      }

      const results = await builder.analyze()

      const violations = results.violations.map((v) => ({
        id: v.id,
        impact: v.impact,
        description: v.description,
        nodes: v.nodes.length,
      }))

      expect(
        violations,
        `axe-core violations on ${pg.path}:\n${JSON.stringify(violations, null, 2)}`,
      ).toEqual([])
    })
  }
})

// ── 8. Visual screenshot comparison ────────────────────────────────────
test.describe('P4-26d: Cross-browser — visual screenshots', () => {
  const screenshotPages = [
    { path: '/', name: 'dashboard' },
    { path: '/variants', name: 'variants' },
    { path: '/settings/general', name: 'settings' },
    { path: '/pharmacogenomics', name: 'pharmacogenomics' },
  ]

  for (const pg of screenshotPages) {
    test(`capture ${pg.name} screenshot`, async ({ page }) => {
      await mockVisualScreenshotData(page)
      await page.goto(pg.path)
      await waitForReactHydration(page)
      await expectAppChromeSettled(page)

      await expect(page.getByRole('heading', { level: 1 })).toBeVisible()
      await expect(page).toHaveScreenshot(`${pg.name}.png`, {
        fullPage: true,
      })
    })
  }

  // Dark mode screenshots
  for (const pg of screenshotPages) {
    test(`capture ${pg.name} dark mode screenshot`, async ({ page }) => {
      await mockVisualScreenshotData(page)
      await page.emulateMedia({ colorScheme: 'dark' })
      await page.goto(pg.path)
      await waitForReactHydration(page)
      await expectAppChromeSettled(page)

      await expect(page.getByRole('heading', { level: 1 })).toBeVisible()
      await expect(page).toHaveScreenshot(`${pg.name}-dark.png`, {
        fullPage: true,
      })
    })
  }
})

// ── 9. Responsive layout across browsers ───────────────────────────────
test.describe('P4-26d: Cross-browser — responsive layout', () => {
  const viewports = [
    { name: 'mobile', width: 375, height: 812 },
    { name: 'tablet', width: 768, height: 1024 },
    { name: 'desktop', width: 1440, height: 900 },
  ]

  for (const vp of viewports) {
    test(`Dashboard renders at ${vp.name} (${vp.width}x${vp.height})`, async ({ page }) => {
      await page.setViewportSize({ width: vp.width, height: vp.height })

      const errors: string[] = []
      page.on('pageerror', (err) => errors.push(err.message))

      await page.goto('/')
      await waitForReactHydration(page)

      await expect(page.getByRole('heading', { level: 1 })).toBeVisible()
      expect(errors).toEqual([])
    })

    test(`Settings renders at ${vp.name} (${vp.width}x${vp.height})`, async ({ page }) => {
      await page.setViewportSize({ width: vp.width, height: vp.height })

      const errors: string[] = []
      page.on('pageerror', (err) => errors.push(err.message))

      await page.goto('/settings')
      await waitForReactHydration(page)

      await expect(page.getByRole('heading', { level: 1 })).toBeVisible()
      expect(errors).toEqual([])
    })
  }
})

// ── 7. Genome Browser reference-fetch disclosure (#1286) ───────────────
// On first open, the Genome Browser must show a one-time notice that the GRCh37
// reference + RefSeq track are fetched from the IGV.js project's third-party
// servers, and must NOT initialize IGV (the fetch) until the user acknowledges.
const IGV_MODULE_URL =
  /(?:\/node_modules\/\.vite\/deps\/igv[^/?]*\.js|\/node_modules\/igv\/dist\/igv[^/?]*\.js|\/@id\/igv)(?:\?.*)?$/
const IGV_MODULE_STUB = `
  export default {
    async createBrowser(div) {
      const root = document.createElement('div')
      root.className = 'igv-root-div'
      root.dataset.mockIgv = 'true'
      div.appendChild(root)
      return { search() {}, on() {} }
    },
    removeBrowser() {}
  }
`

async function mockIgvModule(page: Page) {
  await page.route(IGV_MODULE_URL, async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/javascript',
      body: IGV_MODULE_STUB,
    })
  })
}

test.describe('P4-26d: Genome Browser reference-fetch disclosure (#1286)', () => {
  test('shows the one-time third-party fetch disclosure before loading IGV', async ({
    page,
  }) => {
    // Fresh context → clean localStorage → the one-time notice is shown.
    await page.goto('/genome-browser')
    await waitForReactHydration(page)

    const notice = page.getByRole('region', { name: /reference-data notice/i })
    await expect(notice).toBeVisible()
    await expect(notice).toContainText('IGV.js project')
    const continueBtn = page.getByRole('button', {
      name: /continue to the genome browser/i,
    })
    await expect(continueBtn).toBeVisible()
    // IGV has not initialized yet — its root element must be absent.
    await expect(page.locator('.igv-root-div')).toHaveCount(0)

    // Acknowledging dismisses the notice and records the consent (one-time).
    await continueBtn.click()
    await expect(notice).toBeHidden()
    const ack = await page.evaluate(() =>
      window.localStorage.getItem('yeliztli.genome-browser-reference-disclosure'),
    )
    expect(ack).toBe('acknowledged')
  })

  test('continues past the disclosure and loads the bundled IGV module', async ({
    page,
  }) => {
    await mockIgvModule(page)

    const pageErrors: string[] = []
    page.on('pageerror', (err) => pageErrors.push(err.message))

    await page.goto('/genome-browser')
    await waitForReactHydration(page)

    await page
      .getByRole('button', { name: /continue to the genome browser/i })
      .click()

    await expect(page.locator('.igv-root-div')).toHaveAttribute(
      'data-mock-igv',
      'true',
    )
    await expect(page.getByRole('alert')).toHaveCount(0)
    expect(
      pageErrors.filter((message) =>
        message.includes("Failed to resolve module specifier 'igv'"),
      ),
    ).toEqual([])
  })
})
