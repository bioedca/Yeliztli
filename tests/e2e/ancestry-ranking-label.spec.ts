/**
 * Issue #532 — the Ancestry "Population Ranking" block rendered each population
 * next to a bare, unlabeled distance-to-centroid number. The list is sorted
 * best-first, so the numbers increase down the list, and with nothing naming the
 * metric or its direction the natural reading ("bigger = stronger match") is
 * exactly backwards. Ancestry is identity-laden, so a misread ranking is a real
 * harm, not just polish.
 *
 * The findings endpoint is stubbed with a realistic admixed (AMR-top) sample —
 * the view reads `sample_id` from the URL and gates the result card only on the
 * findings query — so the card renders without genomic data. We assert the
 * caption that names the metric and its direction, the ordinal rank that makes
 * best-first ordering explicit, and the per-value accessible label.
 */

import { test, expect } from '@playwright/test'
import { bypassSetup, waitForReactHydration } from './helpers'

test.beforeEach(async ({ page }) => {
  await bypassSetup(page)
})

// Realistic admixed sample: closest to the AMR centroid by a wide margin, then a
// cluster of mid-range continental distances, OCE far away — mirrors the #532
// repro (best match = smallest number, worst = largest).
const ANCESTRY_FINDING = {
  top_population: 'AMR',
  pc_scores: [35.2, -6.1, 42.9, 31.8, 2.3, 0.4, 0.9, 0.7],
  population_distances: {
    AMR: 12.3456,
    EUR: 38.7012,
    CSA: 39.1234,
    EAS: 40.0021,
    AFR: 41.2345,
    MID: 44.5678,
    OCE: 88.0102,
  },
  // Intentionally empty: this spec targets the Population Ranking *list*, not the
  // admixture chart. An empty map makes <AdmixtureBar> render its "No admixture
  // data" fallback instead of a Plotly chart, keeping the page render
  // deterministic (no chart-library load) and focused on the ranking. The
  // ranking is a separate field (population_ranking) that always renders.
  admixture_fractions: {},
  population_ranking: [
    { population: 'AMR', distance: 12.3456 },
    { population: 'EUR', distance: 38.7012 },
    { population: 'CSA', distance: 39.1234 },
    { population: 'EAS', distance: 40.0021 },
    { population: 'AFR', distance: 41.2345 },
    { population: 'MID', distance: 44.5678 },
    { population: 'OCE', distance: 88.0102 },
  ],
  snps_used: 4901,
  snps_total: 5000,
  coverage_fraction: 0.98,
  projection_time_ms: 42.0,
  is_sufficient: true,
  evidence_level: 3,
  finding_text: 'Top inferred population: Admixed American (confident).',
  confidence: 0.74,
  missing_aim_rate: 0.02,
  admixture_method: 'nnls',
  n_pcs_used: 8,
  nnls_fractions: null,
  knn_fractions: null,
  nnls_ci_low: null,
  nnls_ci_high: null,
}

test.describe('Ancestry Population Ranking labels its metric and direction (#532)', () => {
  test('caption, ordinal rank, and accessible labels make lower-is-closer clear', async ({
    page,
  }) => {
    await page.route('**/api/analysis/ancestry/findings**', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(ANCESTRY_FINDING),
      })
    })
    // Keep the PCA section out of the Plotly path too: a 404 makes the view show
    // "Failed to load PCA coordinates" instead of rendering <PCAScatter>, so the
    // whole page stays chart-free and the ranking assertions are deterministic.
    await page.route('**/api/analysis/ancestry/pca-coordinates**', async (route) => {
      await route.fulfill({ status: 404, contentType: 'application/json', body: '{}' })
    })

    await page.goto('/ancestry?sample_id=1')
    await waitForReactHydration(page)

    const card = page.getByTestId('ancestry-result-card')
    await expect(card).toBeVisible()

    // The ranking block exists.
    await expect(card.getByText('Population Ranking')).toBeVisible()

    // A caption names the metric AND states which direction is "better", so the
    // increasing-down-the-list numbers cannot read as "bigger = stronger match".
    await expect(card.getByText(/distance to population centroid/i)).toBeVisible()
    await expect(card.getByText(/lower is closer/i)).toBeVisible()

    // An ordinal rank makes the best-first ordering explicit: #1 = best match.
    await expect(card.getByText('#1')).toBeVisible()
    await expect(card.getByText('#7')).toBeVisible()

    // The best match (AMR) shows the smallest distance and is ranked #1, with an
    // accessible label spelling out the direction for screen readers.
    await expect(
      card.getByLabel(
        /Admixed American: rank 1, distance 12\.3456 \(lower is closer\)/,
      ),
    ).toBeVisible()

    // The worst match (MID) shows the largest of the continental distances and a
    // higher rank — confirming numbers grow as the match weakens.
    await expect(
      card.getByLabel(/Middle Eastern: rank 6, distance 44\.5678/),
    ).toBeVisible()
  })
})
