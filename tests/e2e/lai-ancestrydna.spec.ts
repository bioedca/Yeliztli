/**
 * Step 25 — Playwright E2E for AncestryDNA LAI (LAI-00f; Plan §12.2).
 *
 * Exercises the two user-visible LAI surfaces against an AncestryDNA-sourced
 * sample, parameterised by the installed `lai_bundle.version`:
 *
 *   1. **Pre-v2.0.0 bundle** — `GET /api/analysis/ancestry/lai/status` returns
 *      `degraded_coverage: true`, the Dashboard renders the dismissible
 *      "LAI coverage degraded for AncestryDNA — update bundle to v2.0.0"
 *      banner (Plan §6.7, `<AppUpdateBanner>` Step 23).
 *   2. **v2.0.0 bundle** — the soft-gate flag is absent/false; the
 *      AncestryView LAI results carry the Step-24 `coverage_telemetry`
 *      payload so `<LAICoverageTelemetryPanel>` renders the single-source
 *      "X of Y AncestryDNA rsIDs mapped to bundle (Z% dropout)" summary.
 *   3. **No confirmed coverage policy** — chromosome painting renders a
 *      structured no-call while Tier 1 ancestry stays visible and no legacy
 *      painting or rerun control is shown.
 *
 * Following the pattern of `setup-wizard-lai.spec.ts`, every backend
 * endpoint is intercepted with `page.route()` so the spec stays
 * deterministic across Chromium / Firefox / WebKit without depending on
 * the real ingest pipeline, the live `lai_bundle`, or Java availability.
 * Phase 0 (the PR-0c surface we exercise here) does not expose AncestryDNA
 * upload to the user yet — that lands in Phase 1 — so the AncestryDNA
 * sample is mocked as pre-existing.
 */

import { expect, test } from '@playwright/test'

// ── Fixture data ────────────────────────────────────────────────────────

const SAMPLE_ID = 1
const ANCESTRYDNA_SAMPLE = {
  id: SAMPLE_ID,
  name: 'AncestryDNA E2E Sample',
  file_format: 'ancestrydna_v2.0',
  file_hash: 'a'.repeat(64),
  variant_count: 700_000,
  date_collected: '2026-05-15',
  notes: '',
  source: 'AncestryDNA',
  individual_id: null,
  status: 'complete',
  created_at: '2026-05-15T00:00:00Z',
  extra: {},
}

const ANCESTRY_FINDING = {
  top_population: 'EUR',
  pc_scores: [0.5, -0.2, 0.1],
  population_distances: { EUR: 0.05, AFR: 0.30, EAS: 0.25 },
  admixture_fractions: { EUR: 0.95, AFR: 0.02, EAS: 0.03 },
  population_ranking: [
    { population: 'EUR', distance: 0.05 },
    { population: 'AFR', distance: 0.30 },
    { population: 'EAS', distance: 0.25 },
  ],
  snps_used: 4500,
  snps_total: 5000,
  coverage_fraction: 0.9,
  projection_time_ms: 120,
  is_sufficient: true,
  evidence_level: 4,
  finding_text: 'Top population: European (95%).',
  confidence: 0.92,
  missing_aim_rate: 0.1,
  admixture_method: 'nnls',
  n_pcs_used: 10,
  nnls_fractions: { EUR: 0.95, AFR: 0.02, EAS: 0.03 },
  knn_fractions: null,
  nnls_ci_low: { EUR: 0.92, AFR: 0.01, EAS: 0.02 },
  nnls_ci_high: { EUR: 0.98, AFR: 0.04, EAS: 0.05 },
}

const PCA_PAYLOAD = {
  user: [0.5, -0.2],
  reference_samples: { EUR: [[0.4, -0.1]] },
  centroids: { EUR: [0.5, -0.2] },
  population_labels: { EUR: 'European' },
  n_components: 2,
  pc_labels: ['PC1', 'PC2'],
  top_population: 'EUR',
}

const LAI_GLOBAL_ANCESTRY = {
  EUR: { fraction: 0.92, percentage: 92.0, display_name: 'European', color: '#1f77b4' },
  AFR: { fraction: 0.04, percentage: 4.0, display_name: 'African', color: '#2ca02c' },
  EAS: { fraction: 0.04, percentage: 4.0, display_name: 'East Asian', color: '#ff7f0e' },
}

const LAI_CHROMOSOME_PAINTING = {
  chr1: [
    {
      start: 0,
      end: 249_250_621,
      n_snps: 50_000,
      hap0: 'EUR',
      hap1: 'EUR',
      hap0_color: '#1f77b4',
      hap1_color: '#1f77b4',
    },
  ],
}

const LAI_COVERAGE_TELEMETRY_V2 = {
  per_source: {
    ancestrydna: { hits: 632_500, drops: 7_500 },
  },
  total_hits: 632_500,
  total_drops: 7_500,
  drop_rate: 7_500 / (632_500 + 7_500),
  drop_rate_warning: false,
}

const APP_UPDATE_RESPONSE = {
  update_available: false,
  current_version: '0.2.0',
  latest_version: null,
  release_url: null,
  release_notes: null,
  error: null,
}

// ── Shared route mocks ──────────────────────────────────────────────────

type Scenario =
  | 'pre_v2'
  | 'post_v2'
  | 'policy_unavailable'
  | 'sample_below_minimum'
  | 'sample_cancelled'

async function setupCommonRoutes(
  page: import('@playwright/test').Page,
  scenario: Scenario,
): Promise<void> {
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
        has_samples: true,
        data_dir: '/tmp/.yeliztli',
      }),
    })
  })

  await page.route('**/api/samples', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify([ANCESTRYDNA_SAMPLE]),
    })
  })

  await page.route(`**/api/samples/${SAMPLE_ID}`, async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(ANCESTRYDNA_SAMPLE),
    })
  })

  // ── LAI status — soft-gate flag flips by scenario ───────────────────
  await page.route('**/api/analysis/ancestry/lai/status', async (route) => {
    const policyUnavailable = scenario === 'policy_unavailable'
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        bundle_downloaded: true,
        java_available: true,
        lai_available: !policyUnavailable,
        coverage_policy_available: !policyUnavailable,
        message: policyUnavailable
          ? 'Chromosome painting is unavailable because no final-confirmed policy exists.'
          : scenario === 'pre_v2'
            ? 'Chromosome painting is available (pre-v2.0.0 bundle).'
            : 'Chromosome painting is available.',
        insufficient_data_reason: policyUnavailable
          ? {
              code: 'lai_coverage_policy_unavailable',
              category: 'insufficient_validation_data',
              message:
                'Chromosome painting is unavailable because the current LAI bundle has no final-confirmed minimum-coverage policy. Tier 1 ancestry remains available.',
              retryable: false,
            }
          : null,
        // Plan §6.7 soft-gate: degraded under v1 for AncestryDNA samples,
        // absent/false under v2.0.0.
        ...(scenario === 'pre_v2' || policyUnavailable ? { degraded_coverage: true } : {}),
      }),
    })
  })

  // Ancestry endpoints used by AncestryView.
  await page.route('**/api/analysis/ancestry/findings**', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(ANCESTRY_FINDING),
    })
  })

  await page.route('**/api/analysis/ancestry/pca-coordinates**', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(PCA_PAYLOAD),
    })
  })

  await page.route('**/api/analysis/ancestry/haplogroups**', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ assignments: [] }),
    })
  })

  // App-update + update-manager surfaces the Dashboard hits — keep quiet.
  await page.route('**/api/updates/app-update', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(APP_UPDATE_RESPONSE),
    })
  })

  await page.route('**/api/updates/prompts**', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify([]),
    })
  })

  await page.route('**/api/updates/status', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify([]),
    })
  })

  await page.route('**/api/updates/check', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        available: [],
        up_to_date: [],
        errors: [],
        checked_at: '2026-05-20T12:00:00Z',
      }),
    })
  })

  // Banner-suppression: dismissal state lives in localStorage. Clear it so
  // each spec sees the banner from a clean slate.
  await page.addInitScript(() => {
    try {
      window.localStorage.removeItem('laiDegradedCoverageDismissed')
      window.localStorage.removeItem('appUpdateDismissed')
    } catch {
      // localStorage may be unavailable; safe to ignore.
    }
  })
}

async function setupLAIResultsRoutes(
  page: import('@playwright/test').Page,
  scenario: Scenario,
): Promise<{ getProgressRequestCount: () => number }> {
  let progressRequestCount = 0

  // Progress: report complete so AncestryView jumps straight to the
  // results panel without polling forever.
  await page.route(`**/api/analysis/ancestry/lai/${SAMPLE_ID}/progress`, async (route) => {
    progressRequestCount += 1
    const belowMinimum = scenario === 'sample_below_minimum'
    const cancelled = scenario === 'sample_cancelled'
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        job_id: 'job-lai-e2e',
        status: belowMinimum ? 'failed' : cancelled ? 'cancelled' : 'complete',
        progress_pct: belowMinimum ? 95 : cancelled ? 40 : 100,
        message: belowMinimum
          ? 'Insufficient data for chromosome painting'
          : cancelled
            ? 'Chromosome painting cancelled'
            : 'Chromosome painting complete',
        error: belowMinimum
          ? 'lai_insufficient_data:{"message":"Encoded scheduler value must remain hidden."}'
          : null,
        insufficient_data_reason: belowMinimum
          ? {
              code: 'lai_coverage_below_confirmed_minimum',
              category: 'insufficient_sample_coverage',
              message: 'This sample does not meet the final-confirmed minimum coverage policy.',
              retryable: false,
            }
          : null,
        ...(scenario === 'pre_v2' ? { degraded_coverage: true } : {}),
      }),
    })
  })

  // Results: under v2.0.0 the runner emits `coverage_telemetry` carrying
  // the per-source rsID hit/drop counts (Plan §6.6/§6.7). Under v1.x the
  // payload predates the telemetry, so we return null to match the legacy
  // shape and let the soft-gate banner be the sole user-visible signal.
  await page.route(`**/api/analysis/ancestry/lai/${SAMPLE_ID}/results`, async (route) => {
    if (scenario === 'sample_below_minimum' || scenario === 'sample_cancelled') {
      await route.fulfill({ status: 200, contentType: 'application/json', body: 'null' })
      return
    }
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        global_ancestry: LAI_GLOBAL_ANCESTRY,
        chromosome_painting: LAI_CHROMOSOME_PAINTING,
        metadata: { bundle_version: scenario === 'pre_v2' ? 'v1.1.0' : 'v2.0.0' },
        created_at: '2026-05-20T12:00:00Z',
        ...(scenario === 'pre_v2'
          ? { degraded_coverage: true, coverage_telemetry: null }
          : { coverage_telemetry: LAI_COVERAGE_TELEMETRY_V2 }),
      }),
    })
  })

  return {
    getProgressRequestCount: () => progressRequestCount,
  }
}

// ── Specs ───────────────────────────────────────────────────────────────

test.describe('Step 25 — AncestryDNA LAI surfaces', () => {
  test('degraded-coverage banner renders on Dashboard when lai_bundle < v2.0.0', async ({ page }) => {
    await setupCommonRoutes(page, 'pre_v2')
    await setupLAIResultsRoutes(page, 'pre_v2')

    await page.goto('/')
    await page.waitForLoadState('domcontentloaded')

    const banner = page.getByTestId('lai-degraded-coverage-banner')
    await expect(banner).toBeVisible({ timeout: 10_000 })
    await expect(banner).toContainText(
      /LAI coverage degraded for AncestryDNA — update bundle to v2\.0\.0/i,
    )

    // Banner is dismissible — clicking the X stores the dismissal flag
    // and hides the banner. Re-render proves the dismissed branch.
    await banner.getByRole('button', { name: /Dismiss LAI coverage notification/i }).click()
    await expect(banner).toBeHidden()
  })

  test('per-source dropout breakdown renders in AncestryView when lai_bundle ≥ v2.0.0', async ({
    page,
  }) => {
    await setupCommonRoutes(page, 'post_v2')
    await setupLAIResultsRoutes(page, 'post_v2')

    // Dashboard should NOT show the degraded-coverage banner.
    await page.goto('/')
    await page.waitForLoadState('domcontentloaded')
    await expect(page.getByTestId('lai-degraded-coverage-banner')).toHaveCount(0)

    // AncestryView surfaces the Step-24 telemetry panel for the AncestryDNA
    // single-source payload.
    await page.goto(`/ancestry?sample_id=${SAMPLE_ID}`)
    await page.waitForLoadState('domcontentloaded')

    const telemetry = page.getByTestId('lai-coverage-telemetry')
    await expect(telemetry).toBeVisible({ timeout: 15_000 })

    // Keep this fixture aligned with the backend's `chrN` painting keys so
    // the scenario exercises real chromosome segments instead of an empty SVG.
    await expect(
      page.getByTestId('painting-chr1').locator('rect[data-population="EUR"]'),
    ).toHaveCount(2)

    const summary = page.getByTestId('lai-coverage-summary')
    await expect(summary).toContainText('632,500 of 640,000')
    await expect(summary).toContainText('AncestryDNA rsIDs')
    await expect(summary).toContainText('1.2% dropout')

    // Single-source payload — the three-row merged breakdown table must
    // stay hidden (Plan §6.7).
    await expect(page.getByTestId('lai-coverage-merged-table')).toHaveCount(0)
  })

  test('policy-unavailable state is a no-call and suppresses legacy painting', async ({ page }) => {
    await setupCommonRoutes(page, 'policy_unavailable')
    // Deliberately return a historical result: status must still suppress it.
    await setupLAIResultsRoutes(page, 'policy_unavailable')

    await page.goto('/')
    await page.waitForLoadState('domcontentloaded')
    await expect(page.getByTestId('lai-degraded-coverage-banner')).toHaveCount(0)

    await page.goto(`/ancestry?sample_id=${SAMPLE_ID}`)
    await page.waitForLoadState('domcontentloaded')

    const noCall = page.getByTestId('lai-insufficient-data')
    await expect(noCall).toBeVisible({ timeout: 15_000 })
    await expect(noCall).toContainText('Insufficient data for chromosome painting')
    await expect(noCall).toContainText('no final-confirmed minimum-coverage policy')
    await expect(noCall).toContainText('earlier chromosome-painting output is withheld')
    await expect(page.getByText('Admixture Proportions')).toBeVisible()
    await expect(page.getByRole('button', { name: /Run Chromosome Painting Analysis/i })).toHaveCount(0)
    await expect(page.getByText('Chromosome painting complete')).toHaveCount(0)
    await expect(page.getByTestId('lai-coverage-telemetry')).toHaveCount(0)
    await expect(page.getByTestId('tier-comparison')).toHaveCount(0)
  })

  test('sample-level coverage rejection is an insufficient-data result, not a rerun', async ({
    page,
  }) => {
    await setupCommonRoutes(page, 'sample_below_minimum')
    const routes = await setupLAIResultsRoutes(page, 'sample_below_minimum')

    await page.goto(`/ancestry?sample_id=${SAMPLE_ID}`)
    await page.waitForLoadState('domcontentloaded')

    await expect(page.getByText('Insufficient data for chromosome painting')).toBeVisible({
      timeout: 15_000,
    })
    await expect(
      page.getByText('This sample does not meet the final-confirmed minimum coverage policy.'),
    ).toBeVisible()
    await expect(page.getByText(/lai_insufficient_data:/)).toHaveCount(0)
    await expect(page.getByText('Analysis failed')).toHaveCount(0)
    await expect(page.getByRole('button', { name: /Run Chromosome Painting Analysis/i })).toHaveCount(0)
    await expect(page.getByText('Chromosome painting complete')).toHaveCount(0)
    await expect(page.getByTestId('tier-comparison')).toHaveCount(0)

    const terminalRequestCount = routes.getProgressRequestCount()
    await page.waitForTimeout(3_500)
    expect(routes.getProgressRequestCount()).toBe(terminalRequestCount)
  })

  test('cancelled chromosome painting is terminal and stops progress polling', async ({ page }) => {
    await setupCommonRoutes(page, 'sample_cancelled')
    const routes = await setupLAIResultsRoutes(page, 'sample_cancelled')

    await page.goto(`/ancestry?sample_id=${SAMPLE_ID}`)
    await page.waitForLoadState('domcontentloaded')

    await expect(page.getByRole('button', { name: /Run Chromosome Painting Analysis/i })).toBeVisible({
      timeout: 15_000,
    })
    await expect.poll(routes.getProgressRequestCount).toBeGreaterThanOrEqual(1)
    const terminalRequestCount = routes.getProgressRequestCount()
    await page.waitForTimeout(3_500)
    expect(routes.getProgressRequestCount()).toBe(terminalRequestCount)
  })
})
