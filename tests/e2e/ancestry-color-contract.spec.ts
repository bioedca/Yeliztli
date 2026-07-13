/**
 * Issue #1764 — every ancestry visualization must use one backend-delivered
 * population color instead of mixing that color with a frontend-only palette.
 *
 * The sentinel below belongs to neither historical palette, so hard-coding
 * either one cannot pass. This drives real Plotly/SVG output in the browser.
 */

import { expect, test, type Page, type Route } from '@playwright/test'
import { bypassSetup, waitForReactHydration } from './helpers'

const DELIVERED_COLOR = '#123456'
const DELIVERED_COLOR_RGB = 'rgb(18, 52, 86)'

const FINDING = {
  top_population: 'EAS',
  pc_scores: [0.1, -0.2],
  population_distances: { EAS: 0.01 },
  admixture_fractions: { EAS: 1 },
  population_ranking: [{ population: 'EAS', distance: 0.01 }],
  snps_used: 5000,
  snps_total: 5000,
  coverage_fraction: 1,
  projection_time_ms: 12,
  is_sufficient: true,
  classification_status: 'confident',
  quality_flags: [],
  evidence_level: 3,
  finding_text: 'Inferred ancestry: EAS 100%',
  confidence: 0.99,
  missing_aim_rate: 0,
  admixture_method: 'nnls',
  n_pcs_used: 2,
  nnls_fractions: { EAS: 1 },
  knn_fractions: { EAS: 1 },
  nnls_ci_low: { EAS: 0.98 },
  nnls_ci_high: { EAS: 1 },
}

const PCA = {
  user: [],
  reference_samples: { EAS: [[0.1, -0.2]] },
  centroids: { EAS: [0.1, -0.2] },
  population_labels: { EAS: 'East Asian' },
  n_components: 2,
  pc_labels: ['PC1', 'PC2'],
  top_population: 'EAS',
}

const LAI_RESULTS = {
  global_ancestry: {
    EAS: {
      fraction: 1,
      percentage: 100,
      display_name: 'East Asian',
      color: DELIVERED_COLOR,
      confidence: 0.99,
    },
  },
  chromosome_painting: {
    chr1: [
      {
        start: 0,
        end: 248_956_422,
        n_snps: 1000,
        hap0: 'EAS',
        hap1: 'EAS',
        hap0_color: DELIVERED_COLOR,
        hap1_color: DELIVERED_COLOR,
      },
    ],
  },
  metadata: { windows: 1, source: 'color-contract-e2e' },
  created_at: '2026-07-13T00:00:00Z',
  coverage_telemetry: null,
}

function json(body: unknown) {
  return {
    status: 200,
    contentType: 'application/json',
    body: JSON.stringify(body),
  }
}

async function fulfillJson(route: Route, body: unknown) {
  await route.fulfill(json(body))
}

async function mockAncestry(page: Page) {
  await page.route('**/api/analysis/ancestry/findings**', (route) =>
    fulfillJson(route, FINDING),
  )
  await page.route('**/api/analysis/ancestry/pca-coordinates**', (route) =>
    fulfillJson(route, PCA),
  )
  await page.route('**/api/analysis/ancestry/haplogroups**', (route) =>
    fulfillJson(route, { assignments: [] }),
  )
  await page.route('**/api/analysis/ancestry/lai/status**', (route) =>
    fulfillJson(route, {
      bundle_downloaded: true,
      java_available: true,
      lai_available: true,
      message: 'Ready',
    }),
  )
  await page.route('**/api/analysis/ancestry/lai/*/results**', (route) =>
    fulfillJson(route, LAI_RESULTS),
  )
  await page.route('**/api/analysis/ancestry/lai/*/progress**', (route) =>
    fulfillJson(route, null),
  )
}

test.beforeEach(async ({ page }) => {
  await bypassSetup(page)
  await mockAncestry(page)
})

test.describe('Ancestry population color contract (#1764)', () => {
  test('uses the delivered color in every ancestry visualization', async ({ page }) => {
    await page.goto('/ancestry?sample_id=1')
    await waitForReactHydration(page)
    await expect(page.getByText('Chromosome painting complete')).toBeVisible()

    const paintingSegments = page
      .getByTestId('painting-chr1')
      .locator('rect[data-population="EAS"]')
    await expect(paintingSegments).toHaveCount(2)
    for (let index = 0; index < 2; index += 1) {
      await expect(paintingSegments.nth(index)).toHaveAttribute('fill', DELIVERED_COLOR)
    }

    const legendSwatch = page
      .getByTestId('painting-legend')
      .locator('[data-population="EAS"] span')
    await expect(legendSwatch).toHaveCSS('background-color', DELIVERED_COLOR_RGB)

    const rankingDot = page
      .getByTestId('ancestry-result-card')
      .locator('[data-population="EAS"]')
    await expect(rankingDot).toHaveCSS('background-color', DELIVERED_COLOR_RGB)

    const tierOneBar = page
      .getByTestId('tier-comparison')
      .locator('[data-population="EAS"]')
    await expect(tierOneBar).toHaveCSS('background-color', DELIVERED_COLOR_RGB)

    const admixturePath = page
      .getByTestId('admixture-bar')
      .locator('.barlayer g.trace.bars g.point path')
    await expect(admixturePath).toHaveCount(1)
    await expect(admixturePath).toHaveCSS('fill', DELIVERED_COLOR_RGB)

    const pcaPaths = page
      .getByTestId('pca-scatter')
      .locator('.scatterlayer g.trace.scatter path.point')
    await expect(pcaPaths).toHaveCount(2)
    for (let index = 0; index < 2; index += 1) {
      await expect(pcaPaths.nth(index)).toHaveCSS('fill', DELIVERED_COLOR_RGB)
    }

    const piePath = page
      .getByTestId('ancestry-pie-chart')
      .locator('.pielayer g.trace g.slice path.surface')
    await expect(piePath).toHaveCount(1)
    await expect(piePath).toHaveCSS('fill', DELIVERED_COLOR_RGB)
  })
})
