/**
 * Issue #589 — the shared PRSGaugeCard footer was rendered unconditionally as
 * `Source: {study} ({ancestry}, n={sample_size})`. Legacy responses without
 * development provenance reached the gauge with `source_ancestry=""` and
 * `sample_size=0`, so the footer rendered the
 * broken, misleading `Source: … (, n=0)` — a stray comma plus a false "0 samples".
 *
 * This drives the real eBMD view (run mutation + prs query stubbed; the view reads
 * `sample_id` from the URL) and asserts the footer now omits the empty parenthetical.
 */

import { test, expect } from '@playwright/test'
import AxeBuilder from '@axe-core/playwright'
import { bypassSetup, mockFreshSampleState, waitForReactHydration } from './helpers'

test.beforeEach(async ({ page }) => {
  await bypassSetup(page)
  await mockFreshSampleState(page)
})

const jsonRoute = (payload: unknown, status = 200) => ({
  status,
  contentType: 'application/json',
  body: JSON.stringify(payload),
})

// Deliberately model a legacy response without development-provenance fields.
const EBMD_PRS = {
  name: 'Heel eBMD',
  calibrated: true,
  percentile: 55,
  snps_used: 1100,
  snps_total: 1200,
  coverage_fraction: 0.92,
  is_sufficient: true,
  source_study: 'Graham et al. 2021',
  source_pmid: '33462484',
  pgs_id: 'PGS000001',
  pgs_license: 'CC-BY',
  development_method: 'C+T',
  ancestry_mismatch: false,
  ancestry_warning_text: null,
  evidence_level: 2,
}

const EBMD_RESPONSE = {
  available: true,
  recommended_pgs_id: 'PGS000001',
  prs: EBMD_PRS,
  context: {},
  research_use_only: true,
}

test.describe('eBMD stored development provenance (#2423)', () => {
  test('renders the stored provenance, preserves its link, and remains accessible', async ({ page }) => {
    await page.route('**/api/analysis/ebmd/prs**', (route) => route.fulfill(jsonRoute({
      ...EBMD_RESPONSE,
      scoring_state: 'current',
      prs: {
        ...EBMD_PRS,
        source_study: 'Synthetic provenance study',
        source_pmid: '',
        source_ancestry: 'EAS',
        sample_size: 12345,
        genome_build: 'GRCh37',
        variants_number: 987654,
        source_url: 'https://example.org/synthetic-ebmd-score',
        pgs_license: 'Synthetic fixture license',
      },
    })))
    await page.goto('/ebmd?sample_id=1')
    await waitForReactHydration(page)

    const card = page.getByTestId('ebmd-prs').getByTestId('cancer-prs-card')
    await expect(card).toBeVisible()
    await expect(card.getByText('Source: Synthetic provenance study (EAS, n=12,345)')).toBeVisible()
    await expect(card.getByText('987,654 variants')).toBeVisible()
    await expect(card.getByText(/^(1,100|1,200) variants$/)).toHaveCount(0)
    const source = card.getByRole('link', { name: 'PGS000001', exact: true })
    await expect(source).toHaveAttribute('href', 'https://example.org/synthetic-ebmd-score')
    await source.focus()
    await expect(source).toBeFocused()
    await expect(source).toHaveAttribute('rel', 'noopener noreferrer')
    await expect(card.getByTestId('research-use-badge')).toHaveText('Research Use Only')
    await expect(card.getByText('Synthetic fixture license')).toBeVisible()

    const accessibility = await new AxeBuilder({ page })
      .include('[data-testid="ebmd-prs"]')
      .withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'])
      .analyze()
    expect(accessibility.passes.length).toBeGreaterThan(0)
    expect(accessibility.violations).toEqual([])
  })

  test('keeps unknown ancestry without inventing counts and uses the catalog fallback', async ({ page }) => {
    await page.route('**/api/analysis/ebmd/prs**', (route) => route.fulfill(jsonRoute({
      ...EBMD_RESPONSE,
      scoring_state: 'current',
      prs: {
        ...EBMD_PRS,
        source_study: 'Synthetic unknown study',
        source_pmid: '',
        source_ancestry: 'unknown',
        sample_size: 0,
        genome_build: null,
        variants_number: null,
        source_url: null,
      },
    })))
    await page.goto('/ebmd?sample_id=1')
    await waitForReactHydration(page)

    const card = page.getByTestId('ebmd-prs').getByTestId('cancer-prs-card')
    await expect(card).toBeVisible()
    await expect(card.getByText('Source: Synthetic unknown study (unknown)')).toBeVisible()
    await expect(card.getByText(/n=0|\(,|\d+ variants/)).toHaveCount(0)
    await expect(card.getByRole('link', { name: 'PGS000001', exact: true })).toHaveAttribute(
      'href', 'https://www.pgscatalog.org/score/PGS000001/',
    )
  })
})

test.describe('PRS gauge source footer drops the empty "(, n=0)" placeholder (#589)', () => {
  test('eBMD gauge footer shows the study with no stray comma or false n=0', async ({ page }) => {
    // The view fires the run mutation on mount; an unstubbed POST flips the page
    // to a full-page error, so stub both the run and the prs query.
    await page.route('**/api/analysis/ebmd/run**', async (route) => {
      await route.fulfill(jsonRoute({ status: 'complete' }))
    })
    await page.route('**/api/analysis/ebmd/prs**', async (route) => {
      await route.fulfill(jsonRoute(EBMD_RESPONSE))
    })

    await page.goto('/ebmd?sample_id=1')
    await waitForReactHydration(page)

    const card = page.getByTestId('ebmd-prs').getByTestId('cancer-prs-card')
    await expect(card).toBeVisible()

    // The footer names the source study...
    await expect(card.getByText(/Source:\s*Graham et al\. 2021/)).toBeVisible()
    // ...with NO broken "(, n=0)" parenthetical (the bug): no stray comma, no n=0.
    await expect(card.getByText(/\(,/)).toHaveCount(0)
    await expect(card.getByText(/n=0/)).toHaveCount(0)
  })
})
