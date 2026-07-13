/**
 * Step 32 — Setup wizard with LAI bundle (E2E).
 *
 * Walks the setup wizard from the Import step through the Databases step
 * with `lai_bundle` checked, completes the (mocked) download via SSE, then
 * navigates to `/ancestry?sample_id=1` and asserts the chromosome painting
 * section shows "LAI bundle is ready".
 *
 * The backend is intercepted with `page.route()` so the spec stays
 * deterministic across Chromium / Firefox / WebKit without depending on
 * network access or the live LAI bundle (523 MB).  The mocked manifest
 * entry mirrors the on-disk fixture pattern described in
 * `docs/setup-update-plan.md` §3.1 — a tiny tarball pointed to by a
 * fixture manifest, served from the test runtime rather than GitHub.
 */

import { expect, test } from '@playwright/test'

// ── Fixture data ────────────────────────────────────────────────────────

const FIXTURE_LAI_BUNDLE = {
  version: 'v1.1-fixture',
  build_date: '2026-05-08',
  url: 'http://127.0.0.1:0/fixture/lai_bundle.tar.gz',
  sha256: '0'.repeat(64),
  size_bytes: 1024,
}

const SETUP_BASE = {
  needs_setup: true,
  disclaimer_accepted: true, // skip the disclaimer step in the wizard
  has_databases: false,
  has_samples: false,
  data_dir: '/tmp/.yeliztli',
}

const DB_LIST_INITIAL = {
  databases: [
    {
      name: 'clinvar',
      display_name: 'ClinVar',
      description: 'Clinical variant interpretations from NCBI ClinVar',
      filename: 'clinvar.db',
      expected_size_bytes: 250_000_000,
      required: true,
      phase: 1,
      downloaded: true, // pretend required DBs were already downloaded
      file_size_bytes: 250_000_000,
      build_mode: 'pipeline',
    },
    {
      name: 'gnomad',
      display_name: 'gnomAD',
      description: 'Population allele frequencies',
      filename: 'gnomad_af.db',
      expected_size_bytes: 2_000_000_000,
      required: true,
      phase: 2,
      downloaded: true,
      file_size_bytes: 2_000_000_000,
      build_mode: 'pipeline',
    },
    {
      name: 'lai_bundle',
      display_name: 'LAI Bundle (Chromosome Painting)',
      description: 'Local ancestry inference models for chromosome-level ancestry painting.',
      filename: 'lai_bundle.tar.gz',
      expected_size_bytes: FIXTURE_LAI_BUNDLE.size_bytes,
      required: false,
      phase: 3,
      downloaded: false,
      file_size_bytes: null,
      build_mode: 'download',
    },
  ],
  total_size_bytes: 2_250_001_024,
  downloaded_count: 2,
  total_count: 3,
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

// ── Spec ────────────────────────────────────────────────────────────────

test.describe('Step 32 — Setup wizard with LAI bundle', () => {
  test('LAI is downloaded via wizard and AncestryView reports "LAI bundle is ready"', async ({
    page,
  }) => {
    // Wizard state advances through these stages as the test progresses:
    //   "wizard"      — needs_setup=true, LAI not yet downloaded
    //   "lai_ready"   — LAI marked downloaded after the mocked SSE completes
    //   "post_setup"  — needs_setup=false so /ancestry no longer redirects
    let stage: 'wizard' | 'lai_ready' | 'post_setup' = 'wizard'

    // Capture every (db_name, status) the backend was told to download so we
    // can assert at the end that the user actually selected lai_bundle.
    const downloadRequests: string[][] = []

    // ── Auth + setup status ──────────────────────────────────────────
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
      const needsSetup = stage !== 'post_setup'
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          ...SETUP_BASE,
          needs_setup: needsSetup,
          has_databases: stage !== 'wizard',
        }),
      })
    })

    await page.route('**/api/setup/disclaimer', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          title: 'Disclaimer',
          text: 'For research / educational use only.',
          accept_label: 'I Understand and Accept',
        }),
      })
    })

    await page.route('**/api/setup/detect-existing', async (route) => {
      // Force the "Skip — Start Fresh" branch in ImportBackupStep.
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          existing_found: false,
          has_config: false,
          has_samples: false,
          has_databases: false,
          data_dir: SETUP_BASE.data_dir,
        }),
      })
    })

    await page.route('**/api/setup/storage-info', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          data_dir: SETUP_BASE.data_dir,
          free_space_bytes: 100_000_000_000,
          free_space_gb: 100,
          total_space_bytes: 500_000_000_000,
          total_space_gb: 500,
          status: 'ok',
          message: 'Storage looks good.',
          path_exists: true,
          path_writable: true,
        }),
      })
    })

    await page.route('**/api/setup/set-storage-path', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          success: true,
          data_dir: SETUP_BASE.data_dir,
          free_space_gb: 100,
          status: 'ok',
          message: 'Storage path saved.',
        }),
      })
    })

    await page.route('**/api/setup/credentials', async (route) => {
      if (route.request().method() === 'POST') {
        await route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify({ success: true, message: 'Saved.' }),
        })
        return
      }
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          pubmed_email: '',
          ncbi_api_key: '',
          omim_api_key: '',
        }),
      })
    })

    // ── Databases listing — flips when LAI completes ──────────────────
    await page.route('**/api/databases', async (route) => {
      const databases = DB_LIST_INITIAL.databases.map((db) =>
        db.name === 'lai_bundle' && stage !== 'wizard'
          ? { ...db, downloaded: true, file_size_bytes: FIXTURE_LAI_BUNDLE.size_bytes }
          : db,
      )
      const downloaded_count = databases.filter((d) => d.downloaded).length
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          databases,
          total_size_bytes: DB_LIST_INITIAL.total_size_bytes,
          downloaded_count,
          total_count: databases.length,
        }),
      })
    })

    // ── Trigger download — record what was asked for, return a session
    await page.route('**/api/databases/download', async (route) => {
      const body = JSON.parse(route.request().postData() ?? '{}')
      downloadRequests.push(body.databases ?? [])
      await route.fulfill({
        status: 202,
        contentType: 'application/json',
        body: JSON.stringify({
          session_id: 'sess-lai-fixture',
          downloads: [{ db_name: 'lai_bundle', job_id: 'job-lai-1' }],
        }),
      })
    })

    // ── SSE progress — single "complete" event ───────────────────────
    // The DatabasesStep handler closes the EventSource as soon as every DB
    // reports a terminal status, so one event is enough.  We flip `stage`
    // here so the subsequent /api/databases refetch reports LAI downloaded.
    await page.route('**/api/databases/progress/**', async (route) => {
      stage = 'lai_ready'
      const payload = JSON.stringify({
        session_id: 'sess-lai-fixture',
        databases: [
          {
            db_name: 'lai_bundle',
            job_id: 'job-lai-1',
            status: 'complete',
            progress_pct: 100,
            message: 'LAI bundle ready',
            error: null,
          },
        ],
      })
      await route.fulfill({
        status: 200,
        contentType: 'text/event-stream',
        headers: { 'Cache-Control': 'no-cache' },
        body: `event: progress\ndata: ${payload}\n\n`,
      })
    })

    // ── Ancestry endpoints (used after navigating to /ancestry) ──────
    await page.route('**/api/analysis/ancestry/lai/status', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          bundle_downloaded: true,
          java_available: true,
          lai_available: true,
          message: 'Chromosome painting is available.',
        }),
      })
    })

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
        body: JSON.stringify({
          user: [0.5, -0.2],
          reference_samples: { EUR: [[0.4, -0.1]] },
          centroids: { EUR: [0.5, -0.2] },
          population_labels: { EUR: 'European' },
          n_components: 2,
          pc_labels: ['PC1', 'PC2'],
          top_population: 'EUR',
        }),
      })
    })

    await page.route('**/api/analysis/ancestry/haplogroups**', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ assignments: [] }),
      })
    })

    // No results yet — null body keeps useLAIResults().data falsy so the
    // "Run chromosome painting" CTA (which carries the target text) renders.
    await page.route('**/api/analysis/ancestry/lai/*/results', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: 'null',
      })
    })

    await page.route('**/api/analysis/ancestry/lai/*/progress', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: 'null',
      })
    })

    // ── Walk through the wizard ──────────────────────────────────────
    await page.goto('/setup')
    await page.waitForLoadState('domcontentloaded')

    // Step 1 — Import from backup → "Skip — Start Fresh"
    await page.getByRole('button', { name: /Skip — Start Fresh/i }).click()

    // Step 2 — Storage → "Continue"
    await expect(page.getByRole('heading', { name: /Storage Location/i })).toBeVisible()
    await page.getByRole('button', { name: 'Continue' }).click()

    // Step 3 — Credentials → fill required email + Continue
    await expect(page.getByRole('heading', { name: 'External Services' })).toBeVisible()
    await page.getByLabel(/PubMed email address/i).fill('e2e@example.com')
    await page.getByRole('button', { name: 'Continue' }).click()

    // Step 4 — Databases. LAI is opt-in. Select it and download.
    await expect(page.getByRole('heading', { name: /Reference Databases/i })).toBeVisible()
    const laiCheckbox = page.getByTestId('db-checkbox-lai_bundle')
    await expect(laiCheckbox).not.toBeChecked()
    await laiCheckbox.check()
    await expect(laiCheckbox).toBeChecked()
    await page.getByRole('button', { name: /Download Selected/i }).click()

    // Mocked SSE returns "complete" immediately → DatabasesStep refetches
    // the database list. After completion `lai_bundle.downloaded=true`, so
    // the LAI row is no longer selectable and its checkbox disappears.
    await expect(laiCheckbox).toBeHidden({ timeout: 10_000 })
    expect(downloadRequests.at(-1)).toContain('lai_bundle')

    // ── Navigate to AncestryView and verify the LAI-ready CTA ────────
    stage = 'post_setup'
    await page.goto('/ancestry?sample_id=1')
    await page.waitForLoadState('domcontentloaded')

    await expect(
      page.getByText(/LAI bundle is ready\./i),
    ).toBeVisible({ timeout: 15_000 })
  })
})
