/**
 * Step 86 — Merge E2E (MRG-10; Plan §15.1).
 *
 * Drives the canonical multi-source merge user journey end-to-end against
 * a fully-mocked backend:
 *
 *   1. /individuals/{id} renders two linked samples + a "Merge samples"
 *      action (Plan §10.7 visibility rule: ≥2 linked samples).
 *   2. The MergeWizard opens. Default strategy is `flag_only` (Plan §10.3
 *      "clinically safest"). Preview returns the §10.4(c) concordance
 *      summary; Confirm commits the merge and binds the returned
 *      `job_id` to the SSE annotation channel.
 *   3. A single SSE `progress` event with `status='complete'` lifts the
 *      gate; the wizard auto-navigates to `/?sample_id={merged}&post_merge=1
 *      &job_id={job}` (Plan §10.7 redirect→modal hand-off).
 *   4. Dashboard renders the merged sample + the <PostMergeRewatchModal>
 *      with re-watch candidates (Plan §10.7 "On redirect to the new
 *      sample's dashboard, …").
 *   5. /samples/{merged}/concordance renders the paginated concordance
 *      report (Plan §10.6).
 *   6. /variants?sample_id={merged} renders the merged-sample variant
 *      table with Source + Concordance filter chips (Plan §10.7 / Step 71).
 *
 * Patterned after `individuals.spec.ts` (Step 57) for stateful mocks and
 * `setup-wizard-lai.spec.ts` (Step 25) for the `text/event-stream` route
 * that ships a single terminal `progress` event.
 */

import { expect, test, type Page } from '@playwright/test'

// ── Fixture identifiers ─────────────────────────────────────────────────

const INDIVIDUAL_ID = 1
const INDIVIDUAL_NAME = 'Family Tree'
const SAMPLE_1_ID = 1
const SAMPLE_2_ID = 2
const MERGED_SAMPLE_ID = 99
const JOB_ID = 'merge-job-1'

// One discordant locus appears in the concordance report; this is what
// the variant table will surface under the `discordant` concordance chip.
const DISCORDANT_RSID = 'rs429358'
const DISCORDANT_CHROM = '19'
const DISCORDANT_POS = 45411941

// ── Sample fixtures ─────────────────────────────────────────────────────

const SAMPLE_23ANDME = {
  id: SAMPLE_1_ID,
  name: '23andMe Sample',
  db_path: '/tmp/.yeliztli/samples/sample_1.db',
  file_format: '23andme_v5',
  file_hash: 'a'.repeat(64),
  notes: null,
  date_collected: '2026-05-10',
  source: '23andMe',
  individual_id: INDIVIDUAL_ID,
  extra: {},
  created_at: '2026-05-10T00:00:00Z',
  updated_at: '2026-05-10T00:00:00Z',
}

const SAMPLE_ANCESTRYDNA = {
  id: SAMPLE_2_ID,
  name: 'AncestryDNA Sample',
  db_path: '/tmp/.yeliztli/samples/sample_2.db',
  file_format: 'ancestrydna_v2.0',
  file_hash: 'b'.repeat(64),
  notes: null,
  date_collected: '2026-05-12',
  source: 'AncestryDNA',
  individual_id: INDIVIDUAL_ID,
  extra: {},
  created_at: '2026-05-12T00:00:00Z',
  updated_at: '2026-05-12T00:00:00Z',
}

const SAMPLE_MERGED = {
  id: MERGED_SAMPLE_ID,
  name: 'Family Tree (merged)',
  db_path: `/tmp/.yeliztli/samples/sample_${MERGED_SAMPLE_ID}.db`,
  file_format: 'merged_v1',
  file_hash: 'c'.repeat(64),
  notes: null,
  date_collected: '2026-05-27',
  source: 'merged',
  individual_id: INDIVIDUAL_ID,
  extra: {},
  created_at: '2026-05-27T00:00:00Z',
  updated_at: '2026-05-27T00:00:00Z',
}

const CONCORDANCE_SUMMARY = {
  match: 612_345,
  filled_nocall: 14_220,
  discordant: 87,
  unique_S1: 18_400,
  unique_S2: 23_910,
  collapsed_rsid: 12,
}

const MIGRATE_CANDIDATES = {
  candidates: [
    {
      rsid_on_source: 'rs1801133',
      notes_on_source: 'MTHFR C677T — flagged on Mom AncestryDNA',
      sample_id: SAMPLE_2_ID,
      chrom: '1',
      pos: 11_856_378,
      rsid_on_merged_or_null: 'rs1801133',
    },
    {
      rsid_on_source: 'rs9999999',
      notes_on_source: 'Private to source — locus dropped from merge',
      sample_id: SAMPLE_1_ID,
      chrom: '2',
      pos: 60_000_000,
      rsid_on_merged_or_null: null,
    },
  ],
}

// VariantPage shape from `frontend/src/types/variants.ts` (P1-14):
// `{ items, next_cursor_chrom, next_cursor_pos, has_more, limit }`.
const VARIANT_ROWS = {
  items: [
    {
      rsid: DISCORDANT_RSID,
      chrom: DISCORDANT_CHROM,
      pos: DISCORDANT_POS,
      genotype: '??',
      ref: 'T',
      alt: 'C',
      zygosity: null,
      gene_symbol: 'APOE',
      consequence: 'missense_variant',
      clinvar_significance: 'risk_factor',
      clinvar_review_stars: null,
      gnomad_af_global: null,
      rare_flag: false,
      cadd_phred: null,
      sift_score: null,
      sift_pred: null,
      polyphen2_hsvar_score: null,
      polyphen2_hsvar_pred: null,
      revel: null,
      annotation_coverage: 63,
      evidence_conflict: false,
      ensemble_pathogenic: false,
      chrom_grch38: null,
      pos_grch38: null,
      tags: [],
      source: 'both',
      concordance: 'discordant',
      alt_rsid: '',
    },
  ],
  next_cursor_chrom: null,
  next_cursor_pos: null,
  has_more: false,
  limit: 100,
}

// ── Helpers ─────────────────────────────────────────────────────────────

function jsonRoute(payload: unknown, status = 200) {
  return {
    status,
    contentType: 'application/json',
    body: JSON.stringify(payload),
  }
}

interface MergeState {
  /** Flips to true once `POST /api/individuals/{id}/merge` resolves. */
  committed: boolean
}

async function setupRoutes(page: Page, state: MergeState): Promise<void> {
  // ── App-shell mocks ────────────────────────────────────────────────
  await page.route('**/api/auth/status', async (route) => {
    await route.fulfill(
      jsonRoute({
        auth_enabled: false,
        has_password: false,
        authenticated: true,
      }),
    )
  })

  await page.route('**/api/setup/status', async (route) => {
    await route.fulfill(
      jsonRoute({
        disclaimer_accepted: true,
        data_dir: '/tmp/.yeliztli',
        needs_setup: false,
        has_databases: true,
        has_samples: true,
      }),
    )
  })

  await page.route('**/api/updates/app-update', async (route) => {
    await route.fulfill(
      jsonRoute({
        update_available: false,
        current_version: '0.2.0',
        latest_version: null,
        release_url: null,
        release_notes: null,
        error: null,
      }),
    )
  })

  await page.route('**/api/updates/prompts**', async (route) => {
    await route.fulfill(jsonRoute([]))
  })
  await page.route('**/api/updates/status', async (route) => {
    await route.fulfill(jsonRoute([]))
  })

  // ── Samples ────────────────────────────────────────────────────────
  await page.route('**/api/samples', async (route) => {
    const samples = state.committed
      ? [SAMPLE_23ANDME, SAMPLE_ANCESTRYDNA, SAMPLE_MERGED]
      : [SAMPLE_23ANDME, SAMPLE_ANCESTRYDNA]
    await route.fulfill(jsonRoute(samples))
  })

  await page.route(/\/api\/samples\/\d+$/, async (route) => {
    const id = Number(route.request().url().split('/').pop())
    if (id === SAMPLE_1_ID) return route.fulfill(jsonRoute(SAMPLE_23ANDME))
    if (id === SAMPLE_2_ID) return route.fulfill(jsonRoute(SAMPLE_ANCESTRYDNA))
    if (id === MERGED_SAMPLE_ID) return route.fulfill(jsonRoute(SAMPLE_MERGED))
    await route.fulfill({ status: 404, body: '{}' })
  })

  // Settings → Samples row affordances (not under test here).
  await page.route('**/api/samples/*/merged-children', async (route) => {
    await route.fulfill(jsonRoute([]))
  })

  // ── Annotation status (used by AnnotationPanel + SSE) ──────────────
  // No active background job; AnnotationPanel renders its "Run" button.
  await page.route('**/api/annotation/active/**', async (route) => {
    await route.fulfill(jsonRoute({ job_id: null, status: null }))
  })

  // Single terminal `progress` event flips both the wizard's gate and
  // the post-merge modal's gate to `complete`. The pattern is borrowed
  // from setup-wizard-lai.spec.ts (Step 25).
  await page.route(`**/api/annotation/status/${JOB_ID}`, async (route) => {
    const payload = JSON.stringify({
      job_id: JOB_ID,
      status: 'complete',
      progress_pct: 100,
      message: 'Annotation complete',
      error: null,
    })
    await route.fulfill({
      status: 200,
      contentType: 'text/event-stream',
      headers: { 'Cache-Control': 'no-cache' },
      body: `event: progress\ndata: ${payload}\n\n`,
    })
  })

  // ── Variants (Dashboard + VariantExplorer) ─────────────────────────
  await page.route('**/api/variants/count**', async (route) => {
    await route.fulfill(jsonRoute({ total: 712_000 }))
  })
  await page.route('**/api/variants/qc-stats**', async (route) => {
    await route.fulfill(
      jsonRoute({
        homozygous: 100,
        heterozygous: 50,
        nocall: 5,
        total: 155,
        call_rate: 0.97,
        ti_tv_ratio: 2.1,
        chromosome_counts: { '1': 50, '19': 10 },
      }),
    )
  })
  // Use regex anchors so we don't accidentally intercept Vite source-module
  // fetches like `/src/api/variants.ts` — the `**/api/variants?**` glob
  // would otherwise greedy-match those `.ts` URLs and return JSON, which
  // breaks the dev-server module loader.
  await page.route(/\/api\/variants(\?[^/]*)?$/, async (route) => {
    await route.fulfill(jsonRoute(VARIANT_ROWS))
  })
  await page.route(/\/api\/variants\/chromosomes(\?|$)/, async (route) => {
    // Endpoint returns `ChromosomeSummary[]` directly (`{ chrom, count }`).
    await route.fulfill(
      jsonRoute([
        { chrom: '1', count: 50 },
        { chrom: '2', count: 12 },
        { chrom: DISCORDANT_CHROM, count: 1 },
      ]),
    )
  })

  // ── Findings + module summaries (Dashboard module grid) ────────────
  await page.route('**/api/analysis/findings/summary**', async (route) => {
    await route.fulfill(
      jsonRoute({
        total_findings: 0,
        modules: [],
        high_confidence_findings: [],
      }),
    )
  })
  await page.route('**/api/analysis/findings**', async (route) => {
    await route.fulfill(jsonRoute({ findings: [], total: 0 }))
  })
  await page.route('**/api/analysis/modules/summary**', async (route) => {
    await route.fulfill(jsonRoute({ modules: [] }))
  })

  // ── Individuals ────────────────────────────────────────────────────
  const individualDetail = {
    id: INDIVIDUAL_ID,
    display_name: INDIVIDUAL_NAME,
    notes: null,
    biological_sex: null,
    created_at: '2026-05-20T00:00:00Z',
    updated_at: '2026-05-20T00:00:00Z',
    linked_samples: [
      {
        id: SAMPLE_1_ID,
        name: SAMPLE_23ANDME.name,
        file_format: SAMPLE_23ANDME.file_format,
        vendor: '23andme',
        created_at: SAMPLE_23ANDME.created_at,
        updated_at: SAMPLE_23ANDME.updated_at,
      },
      {
        id: SAMPLE_2_ID,
        name: SAMPLE_ANCESTRYDNA.name,
        file_format: SAMPLE_ANCESTRYDNA.file_format,
        vendor: 'ancestrydna',
        created_at: SAMPLE_ANCESTRYDNA.created_at,
        updated_at: SAMPLE_ANCESTRYDNA.updated_at,
      },
    ],
    aggregated_findings_count: 0,
  }
  const mergedLinkedSample = {
    id: MERGED_SAMPLE_ID,
    name: SAMPLE_MERGED.name,
    file_format: SAMPLE_MERGED.file_format,
    vendor: 'merged',
    created_at: SAMPLE_MERGED.created_at,
    updated_at: SAMPLE_MERGED.updated_at,
  }

  await page.route('**/api/individuals', async (route) => {
    await route.fulfill(
      jsonRoute([
        {
          id: INDIVIDUAL_ID,
          display_name: INDIVIDUAL_NAME,
          notes: null,
          biological_sex: null,
          created_at: individualDetail.created_at,
          updated_at: individualDetail.updated_at,
          sample_count: 2,
          vendors: ['23andme', 'ancestrydna'],
          last_activity: individualDetail.updated_at,
        },
      ]),
    )
  })

  await page.route(/\/api\/individuals\/\d+$/, async (route) => {
    await route.fulfill(
      jsonRoute({
        ...individualDetail,
        linked_samples: state.committed
          ? [...individualDetail.linked_samples, mergedLinkedSample]
          : individualDetail.linked_samples,
      }),
    )
  })

  // Merge preview + commit (Plan §10.6).
  await page.route(
    `**/api/individuals/${INDIVIDUAL_ID}/merge/preview`,
    async (route) => {
      await route.fulfill(
        jsonRoute({
          concordance_summary: CONCORDANCE_SUMMARY,
          est_duration_seconds: 24,
        }),
      )
    },
  )
  await page.route(
    `**/api/individuals/${INDIVIDUAL_ID}/merge`,
    async (route) => {
      if (route.request().method() !== 'POST') return route.continue()
      state.committed = true
      await route.fulfill(
        jsonRoute(
          { merged_sample_id: MERGED_SAMPLE_ID, job_id: JOB_ID },
          201,
        ),
      )
    },
  )

  // ── Merge provenance + concordance report + migrate-from-sources ──
  const provenance = {
    merged_at: '2026-05-27T00:00:00Z',
    strategy: 'flag_only',
    source_sample_ids: [SAMPLE_1_ID, SAMPLE_2_ID],
    source_file_hashes: [SAMPLE_23ANDME.file_hash, SAMPLE_ANCESTRYDNA.file_hash],
    concordance_summary: CONCORDANCE_SUMMARY,
  }
  await page.route(
    `**/api/samples/${MERGED_SAMPLE_ID}/merge-provenance`,
    async (route) => {
      await route.fulfill(jsonRoute(provenance))
    },
  )
  // Unmerged sources return 404 for merge-provenance (Plan §10.6 contract).
  await page.route(/\/api\/samples\/[12]\/merge-provenance$/, async (route) => {
    await route.fulfill({
      status: 404,
      contentType: 'application/json',
      body: JSON.stringify({ detail: 'Sample is not a merged sample' }),
    })
  })

  await page.route(
    `**/api/samples/${MERGED_SAMPLE_ID}/concordance-report**`,
    async (route) => {
      await route.fulfill(
        jsonRoute({
          concordance_summary: CONCORDANCE_SUMMARY,
          total_discordant: 1,
          limit: 50,
          offset: 0,
          discordant_loci: [
            {
              rsid: DISCORDANT_RSID,
              chrom: DISCORDANT_CHROM,
              pos: DISCORDANT_POS,
              genotype: '??',
              discordant_alt_genotype: 'S1=CT;S2=CC',
              alt_rsid: '',
              gene_symbol: 'APOE',
              consequence: 'missense_variant',
              clinvar_significance: 'risk_factor',
            },
          ],
        }),
      )
    },
  )

  await page.route(
    `**/api/samples/${MERGED_SAMPLE_ID}/watched-variants/migrate-from-sources`,
    async (route) => {
      await route.fulfill(jsonRoute(MIGRATE_CANDIDATES))
    },
  )

  // Watched variants — Re-watch button POSTs here. Use a regex with `(\?|$)`
  // so the route fires for `/api/watches` and `/api/watches?…` but NOT for
  // Vite source-module URLs like `/src/api/watches.ts`.
  await page.route(/\/api\/watches(\?|$)/, async (route) => {
    if (route.request().method() === 'POST') {
      await route.fulfill(jsonRoute({ ok: true }, 201))
      return
    }
    await route.fulfill(jsonRoute([]))
  })

  // VariantTable side-fetches — keep them quiet so the table renders.
  // `useColumnPresets` reads `data.presets`, not the top-level array.
  await page.route(/\/api\/column-presets(\?|\/|$)/, async (route) => {
    await route.fulfill(jsonRoute({ presets: [] }))
  })
  await page.route(/\/api\/variants\/density(\?|$)/, async (route) => {
    await route.fulfill(jsonRoute({ chromosomes: [] }))
  })
  await page.route(/\/api\/variants\/consequence-summary(\?|$)/, async (route) => {
    await route.fulfill(jsonRoute([]))
  })
  await page.route(/\/api\/variants\/clinvar-summary(\?|$)/, async (route) => {
    await route.fulfill(jsonRoute([]))
  })
  await page.route(/\/api\/variants\/search(\?|$)/, async (route) => {
    await route.fulfill(jsonRoute([]))
  })
  await page.route(/\/api\/tags(\?|$)/, async (route) => {
    await route.fulfill(jsonRoute([]))
  })
}

// ── Spec ────────────────────────────────────────────────────────────────

test.describe('Step 86 — Merge samples E2E', () => {
  test('link → merge → merged dashboard → concordance report → variant filter chips → re-watch modal', async ({
    page,
  }) => {
    const state: MergeState = { committed: false }
    await setupRoutes(page, state)

    // ── 1. /individuals/{id} renders both linked samples + the Merge CTA.
    await page.goto(`/individuals/${INDIVIDUAL_ID}`)
    await expect(page.getByTestId('individual-detail-page')).toBeVisible()
    await expect(
      page.getByRole('heading', { name: INDIVIDUAL_NAME }),
    ).toBeVisible()
    await expect(page.getByTestId(`linked-sample-row-${SAMPLE_1_ID}`)).toBeVisible()
    await expect(page.getByTestId(`linked-sample-row-${SAMPLE_2_ID}`)).toBeVisible()

    const mergeButton = page.getByTestId('merge-samples-button')
    await expect(mergeButton).toBeVisible()

    // ── 2. Wizard opens — strategy step + flag_only default.
    await mergeButton.click()
    await expect(page.getByTestId('merge-wizard-overlay')).toBeVisible()
    await expect(page.getByTestId('merge-source-pair')).toContainText(
      SAMPLE_23ANDME.name,
    )
    await expect(
      page.getByRole('radio', { name: /Flag discordant calls/i }),
    ).toBeChecked()

    // Preview returns concordance bucket counts (Plan §10.4(c)).
    await page.getByRole('button', { name: /^Preview$/ }).click()
    await expect(page.getByTestId('merge-preview-summary')).toBeVisible()
    await expect(page.getByTestId('concordance-match')).toHaveText(
      CONCORDANCE_SUMMARY.match.toLocaleString(),
    )
    await expect(page.getByTestId('concordance-discordant')).toHaveText(
      CONCORDANCE_SUMMARY.discordant.toLocaleString(),
    )

    // Confirm → commit → SSE complete → auto-redirect.
    await page.getByRole('button', { name: /^Continue$/ }).click()
    await page.getByRole('button', { name: /^Merge$/ }).click()
    expect(state.committed).toBe(true)

    // Wait for the wizard's SSE bind + auto-navigate. The wizard appends
    // `post_merge=1` + `job_id` per Plan §10.7 redirect→modal hand-off.
    await page.waitForURL(
      (url) =>
        url.pathname === '/' &&
        url.searchParams.get('sample_id') === String(MERGED_SAMPLE_ID) &&
        url.searchParams.get('post_merge') === '1' &&
        url.searchParams.get('job_id') === JOB_ID,
    )

    // ── 3. Merged-sample dashboard renders + post-merge modal opens.
    // StatusBar surfaces the merged sample's name (the Dashboard header).
    await expect(page.getByText(SAMPLE_MERGED.name).first()).toBeVisible()

    // The modal subscribes to the same SSE channel and lifts its own
    // gate when `status='complete'` arrives. Once the gate lifts the
    // candidate table renders.
    const candidateTable = page.getByTestId('rewatch-modal-candidate-table')
    await expect(candidateTable).toBeVisible({ timeout: 10_000 })
    await expect(candidateTable).toContainText('rs1801133')
    // Source-private locus is listed but its Re-watch button is disabled
    // (per PostMergeRewatchModal `canRewatch` rule).
    await expect(
      page.getByTestId(`rewatch-row-${SAMPLE_2_ID}:rs1801133-button`),
    ).toBeEnabled()
    await expect(
      page.getByTestId(`rewatch-row-${SAMPLE_1_ID}:rs9999999-button`),
    ).toBeDisabled()

    // Dismiss the modal — URL hand-off params get cleared.
    await page.getByTestId('rewatch-modal-dismiss').click()
    await expect(page.getByTestId('rewatch-modal-overlay')).toHaveCount(0)
    await expect(page).toHaveURL((url) => {
      return (
        url.searchParams.get('post_merge') === null &&
        url.searchParams.get('job_id') === null
      )
    })

    // ── 4. Concordance report page (Plan §10.6).
    await page.goto(`/samples/${MERGED_SAMPLE_ID}/concordance`)
    await expect(
      page.getByRole('heading', { name: /Concordance Report/i }),
    ).toBeVisible()
    await expect(page.getByText('Match', { exact: true })).toBeVisible()
    // The single discordant locus from the report fixture renders with
    // its gene-context join (APOE).
    await expect(page.getByText(DISCORDANT_RSID)).toBeVisible()
    await expect(page.getByText('APOE').first()).toBeVisible()

    // ── 5. Variant Explorer renders Source + Concordance filter chips.
    await page.goto(`/variants?sample_id=${MERGED_SAMPLE_ID}`)
    await expect(
      page.getByRole('button', { name: /Filter by source/i }),
    ).toBeVisible()
    await expect(
      page.getByRole('button', { name: /Filter by concordance/i }),
    ).toBeVisible()
  })
})
