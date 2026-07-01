/**
 * Issue #1334 - sparse ancestry uploads were analyzed as UNCERTAIN in the
 * backend, but no finding row was persisted. The page therefore rendered the
 * same empty state as a never-analyzed sample. This spec stubs the findings
 * endpoint with an explicit low-coverage result and verifies the analyzed-but-
 * uncertain state is visible.
 */

import { test, expect } from '@playwright/test'
import { bypassSetup, waitForReactHydration } from './helpers'

test.beforeEach(async ({ page }) => {
  await bypassSetup(page)
})

const UNCERTAIN_FINDING = {
  top_population: 'UNCERTAIN',
  pc_scores: [],
  population_distances: {},
  admixture_fractions: {},
  population_ranking: [],
  snps_used: 500,
  snps_total: 5000,
  coverage_fraction: 0.1,
  projection_time_ms: 12.5,
  is_sufficient: false,
  classification_status: 'uncertain',
  quality_flags: ['low_coverage'],
  evidence_level: 2,
  finding_text:
    'Ancestry: Uncertain (500/5000 markers, 10% coverage; below 55% coverage needed for a confident call)',
  confidence: 0,
  missing_aim_rate: 0.9,
  admixture_method: 'nnls',
  n_pcs_used: 8,
  nnls_fractions: null,
  knn_fractions: null,
  nnls_ci_low: null,
  nnls_ci_high: null,
}

test.describe('Ancestry low-coverage uncertain state (#1334)', () => {
  test('shows analyzed-but-uncertain instead of never-analyzed empty state', async ({
    page,
  }) => {
    await page.route('**/api/analysis/ancestry/findings**', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(UNCERTAIN_FINDING),
      })
    })
    await page.route('**/api/analysis/ancestry/pca-coordinates**', async (route) => {
      await route.fulfill({ status: 200, contentType: 'application/json', body: 'null' })
    })
    await page.route('**/api/analysis/ancestry/haplogroups**', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ assignments: [] }),
      })
    })
    await page.route('**/api/analysis/ancestry/lai/status', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          bundle_downloaded: false,
          java_available: false,
          lai_available: false,
          message: 'Unavailable in test',
          degraded_coverage: false,
        }),
      })
    })
    await page.route('**/api/analysis/ancestry/lai/*/results', async (route) => {
      await route.fulfill({ status: 200, contentType: 'application/json', body: 'null' })
    })

    await page.goto('/ancestry?sample_id=1')
    await waitForReactHydration(page)

    await expect(page.getByText('Ancestry: Uncertain')).toBeVisible()
    await expect(
      page.getByText(
        'This sample covers 10.0% of ancestry markers, below the 55% needed for a confident ancestry call.',
      ),
    ).toBeVisible()
    await expect(page.getByText('No ancestry results yet.')).toHaveCount(0)
    await expect(page.getByTestId('ancestry-result-card')).toHaveCount(0)
  })
})
