/**
 * Issue #631 — the Ancestry "Admixture Proportions" stacked bar forced an in-bar
 * `%` (and `±CI`) label onto EVERY segment via `textposition: "inside"`. For the
 * small-fraction populations that are normal in admixed individuals, the label
 * couldn't fit, so Plotly rotated it vertical and clipped it — illegible text
 * crammed into the sliver.
 *
 * The fix suppresses the in-bar label on narrow segments (hover + legend still
 * convey them) and uses `textposition: "auto"`, so a shown label is placed
 * inside only when it fits. We render the real Plotly chart (no mock) with a
 * realistic admixed profile and assert the wide-slice label is present while the
 * two narrow-slice labels are absent from the in-bar text.
 *
 * The findings endpoint is stubbed (the view reads `sample_id` from the URL and
 * gates the chart on the findings query), so the chart renders without genomic
 * data. PCA is 404'd to keep the rest of the page off the Plotly path.
 */

import { test, expect } from '@playwright/test'
import { bypassSetup, waitForReactHydration } from './helpers'

test.beforeEach(async ({ page }) => {
  await bypassSetup(page)
})

// Realistic admixed profile from the issue: four wide ~23.5% components plus two
// small ones — Middle Eastern 4.2% and Admixed American 1.8% — whose labels used
// to render as rotated/clipped vertical text.
const ANCESTRY_FINDING = {
  top_population: 'AFR',
  pc_scores: [1.2, -0.6, 4.9, 3.8, 0.3, 0.4, 0.9, 0.7],
  population_distances: {
    AFR: 20.1,
    CSA: 20.4,
    EAS: 20.6,
    EUR: 20.9,
    MID: 41.2,
    AMR: 44.5,
    OCE: 88.0,
  },
  admixture_fractions: {
    AFR: 0.235,
    CSA: 0.235,
    EAS: 0.235,
    EUR: 0.235,
    MID: 0.042,
    AMR: 0.018,
  },
  population_ranking: [
    { population: 'AFR', distance: 20.1 },
    { population: 'CSA', distance: 20.4 },
    { population: 'EAS', distance: 20.6 },
    { population: 'EUR', distance: 20.9 },
    { population: 'MID', distance: 41.2 },
    { population: 'AMR', distance: 44.5 },
  ],
  snps_used: 4901,
  snps_total: 5000,
  coverage_fraction: 0.98,
  projection_time_ms: 42.0,
  is_sufficient: true,
  evidence_level: 3,
  finding_text: 'Top inferred population: African (confident).',
  confidence: 0.71,
  missing_aim_rate: 0.02,
  admixture_method: 'nnls',
  n_pcs_used: 8,
  nnls_fractions: null,
  knn_fractions: null,
  nnls_ci_low: {
    AFR: 0.18,
    CSA: 0.18,
    EAS: 0.18,
    EUR: 0.18,
    MID: 0.025,
    AMR: 0.003,
  },
  nnls_ci_high: {
    AFR: 0.29,
    CSA: 0.29,
    EAS: 0.29,
    EUR: 0.29,
    MID: 0.077,
    AMR: 0.048,
  },
}

test.describe('Ancestry admixture bar suppresses illegible narrow-segment labels (#631)', () => {
  test('wide slices keep their % label; narrow slices show none', async ({ page }) => {
    await page.route('**/api/analysis/ancestry/findings**', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(ANCESTRY_FINDING),
      })
    })
    // 404 the PCA coordinates so only the admixture bar exercises Plotly.
    await page.route('**/api/analysis/ancestry/pca-coordinates**', async (route) => {
      await route.fulfill({ status: 404, contentType: 'application/json', body: '{}' })
    })

    await page.goto('/ancestry?sample_id=1')
    await waitForReactHydration(page)

    const bar = page.getByTestId('admixture-bar')
    await expect(bar).toBeVisible()

    // Wait for Plotly to render its in-bar text layer (label elements carry the
    // `bartext` class regardless of inside/outside placement).
    const barText = bar.locator('text.bartext')
    await expect(barText.first()).toBeVisible()

    const labels = (await barText.allTextContents()).map((s) => s.trim()).filter(Boolean)

    // A wide ~23.5% slice keeps its label (with the ±CI suffix).
    expect(labels.some((t) => t.includes('23.5%'))).toBe(true)

    // The two narrow slices (4.2%, 1.8%) must NOT appear as in-bar labels — that
    // is the rotated/clipped text the fix removes. Their values remain available
    // via hover and the legend.
    expect(labels.some((t) => t.includes('4.2%'))).toBe(false)
    expect(labels.some((t) => t.includes('1.8%'))).toBe(false)

    // No more in-bar labels than wide slices (4 wide of 6 populations).
    expect(labels.length).toBeLessThanOrEqual(4)
  })
})
