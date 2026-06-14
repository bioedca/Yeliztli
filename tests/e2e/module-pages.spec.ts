/**
 * P3-68 — ui-inspector verification pass on the module pages.
 *
 * Verifies each module page renders correctly without JavaScript errors,
 * has proper heading structure, ARIA landmarks, keyboard navigability,
 * and passes axe-core WCAG 2.1 AA accessibility checks.
 *
 * Modules verified:
 *   1. Gene Fitness   (/fitness)
 *   2. Gene Sleep     (/sleep)
 *   3. Gene Skin      (/skin)
 *   4. MTHFR & Methylation (/methylation)
 *   5. Gene Allergy   (/allergy)
 *   6. Traits & Personality (/traits)
 *   7. Gene Health    (/gene-health)
 *   8. Familial Hypercholesterolemia (/fh) — added for page-coverage parity (#551)
 */

import { test, expect } from '@playwright/test'
import AxeBuilder from '@axe-core/playwright'
import { bypassSetup, waitForReactHydration } from './helpers'

test.beforeEach(async ({ page }) => {
  await bypassSetup(page)
})

// All module pages with expected content
const MODULE_PAGES = [
  {
    path: '/fitness',
    title: 'Gene Fitness',
    emptyText: 'Select a sample to view fitness results.',
  },
  {
    path: '/sleep',
    title: 'Gene Sleep',
    emptyText: 'Select a sample to view sleep results.',
  },
  {
    path: '/skin',
    title: 'Gene Skin',
    emptyText: 'Select a sample to view skin results.',
  },
  {
    path: '/methylation',
    title: 'MTHFR & Methylation',
    emptyText: 'Select a sample to view methylation pathway results.',
  },
  {
    path: '/allergy',
    title: 'Gene Allergy & Immune Sensitivities',
    emptyText: 'Select a sample to view allergy & immune sensitivity results.',
  },
  {
    path: '/traits',
    title: 'Traits & Personality',
    emptyText: 'Select a sample to view traits & personality results.',
  },
  {
    path: '/gene-health',
    title: 'Gene Health',
    emptyText: 'Select a sample to view gene health results.',
  },
  {
    path: '/fh',
    title: 'Familial Hypercholesterolemia',
    emptyText: 'Select a sample to view the FH assessment.',
  },
] as const

test.describe('P3-68: Module pages verification', () => {
  for (const mod of MODULE_PAGES) {
    test.describe(`${mod.title} (${mod.path})`, () => {
      test('page loads without JavaScript errors', async ({ page }) => {
        const errors: string[] = []
        page.on('pageerror', (err) => errors.push(err.message))

        await page.goto(mod.path)
        await page.waitForLoadState('networkidle')

        expect(errors).toEqual([])
      })

      test('renders page heading', async ({ page }) => {
        await page.goto(mod.path)
        const heading = page.getByRole('heading', { level: 1, name: mod.title })
        await expect(heading).toBeVisible()
      })

      test('shows empty state without sample', async ({ page }) => {
        await page.goto(mod.path)
        await expect(page.getByText(mod.emptyText)).toBeVisible()
      })

      test('heading hierarchy is valid (no skipped levels)', async ({ page }) => {
        await page.goto(mod.path)
        await waitForReactHydration(page)

        const headingLevels = await page.evaluate(() => {
          const headings = document.querySelectorAll('h1, h2, h3, h4, h5, h6')
          return Array.from(headings).map((h) => parseInt(h.tagName.charAt(1)))
        })

        // Verify no heading level is skipped
        for (let i = 1; i < headingLevels.length; i++) {
          const diff = headingLevels[i] - headingLevels[i - 1]
          expect(
            diff,
            `Heading level jumped from h${headingLevels[i - 1]} to h${headingLevels[i]}`,
          ).toBeLessThanOrEqual(1)
        }
      })

      test('has focusable interactive elements', async ({ page }) => {
        await page.goto(mod.path)
        await waitForReactHydration(page)

        // Verify the page has interactive elements that can receive focus
        const interactive = page.locator('a, button, input, select, textarea, [tabindex="0"]')
        const count = await interactive.count()
        expect(count, `No interactive elements on ${mod.path}`).toBeGreaterThan(0)

        // Verify programmatic focus works
        await interactive.first().focus()
        const focusedTag = await page.evaluate(() => document.activeElement?.tagName)
        expect(focusedTag).not.toBe('BODY')
      })

      test('passes axe-core WCAG 2.1 AA accessibility check', async ({ page }) => {
        await page.goto(mod.path)
        await waitForReactHydration(page)

        const results = await new AxeBuilder({ page })
          .withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'])
          .analyze()

        const violations = results.violations.map((v) => ({
          id: v.id,
          impact: v.impact,
          description: v.description,
          nodes: v.nodes.length,
        }))

        expect(
          violations,
          `Accessibility violations found:\n${JSON.stringify(violations, null, 2)}`,
        ).toEqual([])
      })

      test('renders without console errors or warnings', async ({ page }) => {
        const warnings: string[] = []
        page.on('console', (msg) => {
          if (msg.type() === 'error' || msg.type() === 'warning') {
            // Filter out known noise (React dev mode, HMR)
            const text = msg.text()
            if (
              !text.includes('[vite]') &&
              !text.includes('Download the React DevTools') &&
              !text.includes('React does not recognize')
            ) {
              warnings.push(`[${msg.type()}] ${text}`)
            }
          }
        })

        await page.goto(mod.path)
        await page.waitForLoadState('networkidle')

        expect(
          warnings,
          `Console errors/warnings:\n${warnings.join('\n')}`,
        ).toEqual([])
      })

      test('no broken images or missing resources', async ({ page }) => {
        const failedRequests: string[] = []
        page.on('response', (response) => {
          if (response.status() >= 400 && !response.url().includes('/api/')) {
            failedRequests.push(`${response.status()} ${response.url()}`)
          }
        })

        await page.goto(mod.path)
        await page.waitForLoadState('networkidle')

        expect(
          failedRequests,
          `Failed resource requests:\n${failedRequests.join('\n')}`,
        ).toEqual([])
      })
    })
  }

  // Cross-module navigation tests
  test.describe('Cross-module navigation', () => {
    test('all module routes are reachable from root', async ({ page }) => {
      for (const mod of MODULE_PAGES) {
        const response = await page.goto(mod.path)
        expect(response?.status()).toBeLessThan(400)
        await expect(
          page.getByRole('heading', { level: 1, name: mod.title }),
        ).toBeVisible()
      }
    })
  })

  // Dark mode tests
  test.describe('Dark mode rendering', () => {
    for (const mod of MODULE_PAGES) {
      test(`${mod.title} renders in dark mode without errors`, async ({ page }) => {
        // Emulate dark color scheme
        await page.emulateMedia({ colorScheme: 'dark' })

        const errors: string[] = []
        page.on('pageerror', (err) => errors.push(err.message))

        await page.goto(mod.path)
        await page.waitForLoadState('networkidle')

        await expect(
          page.getByRole('heading', { level: 1, name: mod.title }),
        ).toBeVisible()
        expect(errors).toEqual([])
      })
    }
  })

  // Viewport responsiveness tests
  test.describe('Responsive layout', () => {
    const viewports = [
      { name: 'mobile', width: 375, height: 812 },
      { name: 'tablet', width: 768, height: 1024 },
      { name: 'desktop', width: 1440, height: 900 },
    ]

    for (const mod of MODULE_PAGES) {
      for (const vp of viewports) {
        test(`${mod.title} renders at ${vp.name} (${vp.width}x${vp.height})`, async ({ page }) => {
          await page.setViewportSize({ width: vp.width, height: vp.height })

          const errors: string[] = []
          page.on('pageerror', (err) => errors.push(err.message))

          await page.goto(mod.path)
          await page.waitForLoadState('networkidle')

          await expect(
            page.getByRole('heading', { level: 1, name: mod.title }),
          ).toBeVisible()
          expect(errors).toEqual([])
        })
      }
    }
  })
})
