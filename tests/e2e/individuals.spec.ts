/**
 * Step 57 — E2E linking flow (IND-10; Plan §14.1).
 *
 * Drives the full Settings → Samples → Assign-to-individual UI flow end
 * to end against a mocked backend, then opens `/individuals/{id}` and
 * asserts the aggregated-findings panel:
 *
 *   1. Two samples (one 23andMe, one AncestryDNA) start in the
 *      `Unassigned` group on `/settings/general`.
 *   2. Sample 1's `+ Create new…` flow creates a brand-new individual
 *      "Family Tree", which immediately links sample 1.
 *   3. Sample 2's dropdown now lists "Family Tree" — selecting it links
 *      sample 2 to the same individual.
 *   4. Navigating to `/individuals/1` renders both samples in the
 *      linked-samples table and the aggregated high-confidence findings
 *      panel — a finding shared by both samples (same rsid) collapses
 *      to a single row with two provenance chips, while the
 *      unique-to-each-sample findings render once each.
 *
 * Every backend endpoint is intercepted with `page.route()` so the spec
 * is deterministic across Chromium / Firefox / WebKit. Patterned after
 * `setup-wizard-ancestrydna.spec.ts` (Step 44).
 */

import { expect, test, type Page, type Route } from '@playwright/test'

// ── Fixture data ────────────────────────────────────────────────────────

const INDIVIDUAL_ID = 1
const INDIVIDUAL_DISPLAY_NAME = 'Family Tree'
const SAMPLE_1_ID = 1
const SAMPLE_2_ID = 2

const SAMPLE_23ANDME = {
  id: SAMPLE_1_ID,
  name: '23andMe Sample',
  db_path: '/tmp/.yeliztli/samples/sample_1.db',
  file_format: '23andme_v5',
  file_hash: 'a'.repeat(64),
  notes: null,
  date_collected: '2026-05-10',
  source: '23andMe',
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
  extra: {},
  created_at: '2026-05-12T00:00:00Z',
  updated_at: '2026-05-12T00:00:00Z',
}

// One finding that appears in BOTH samples at the same rsid — must
// dedupe to a single aggregated row with two provenance chips. One
// finding unique to each sample — must each render as its own row.
const SHARED_RSID = 'rs429358'
const SAMPLE_1_UNIQUE_RSID = 'rs1801133'
const SAMPLE_2_UNIQUE_RSID = 'rs113993960'

function findingsSummaryFor(sampleId: number) {
  const isSample1 = sampleId === SAMPLE_1_ID
  const sharedFinding = {
    id: isSample1 ? 101 : 201,
    module: 'apoe',
    category: 'risk',
    evidence_level: 4,
    gene_symbol: 'APOE',
    rsid: SHARED_RSID,
    finding_text: 'APOE e4 carrier — elevated late-onset Alzheimer risk.',
    phenotype: 'Late-onset Alzheimer disease',
    conditions: null,
    zygosity: 'heterozygous',
    clinvar_significance: 'risk_factor',
    diplotype: null,
    metabolizer_status: null,
    drug: null,
    haplogroup: null,
    prs_score: null,
    prs_percentile: null,
    pathway: null,
    pathway_level: null,
    svg_path: null,
    pmid_citations: [],
    detail: null,
    created_at: '2026-05-15T00:00:00Z',
  }
  const uniqueFinding = isSample1
    ? {
        id: 102,
        module: 'nutrigenomics',
        category: 'metabolism',
        evidence_level: 3,
        gene_symbol: 'MTHFR',
        rsid: SAMPLE_1_UNIQUE_RSID,
        finding_text: 'MTHFR C677T — moderate folate-pathway impact.',
        phenotype: null,
        conditions: null,
        zygosity: 'heterozygous',
        clinvar_significance: null,
        diplotype: null,
        metabolizer_status: null,
        drug: null,
        haplogroup: null,
        prs_score: null,
        prs_percentile: null,
        pathway: 'folate',
        pathway_level: 'moderate',
        svg_path: null,
        pmid_citations: [],
        detail: null,
        created_at: '2026-05-15T00:00:00Z',
      }
    : {
        id: 202,
        module: 'carrier',
        category: 'reproductive',
        evidence_level: 4,
        gene_symbol: 'CFTR',
        rsid: SAMPLE_2_UNIQUE_RSID,
        finding_text: 'CFTR ΔF508 — heterozygous carrier (Cystic Fibrosis).',
        phenotype: 'Cystic Fibrosis',
        conditions: null,
        zygosity: 'heterozygous',
        clinvar_significance: 'pathogenic',
        diplotype: null,
        metabolizer_status: null,
        drug: null,
        haplogroup: null,
        prs_score: null,
        prs_percentile: null,
        pathway: null,
        pathway_level: null,
        svg_path: null,
        pmid_citations: [],
        detail: null,
        created_at: '2026-05-15T00:00:00Z',
      }
  return {
    total_findings: 2,
    modules: [
      {
        module: sharedFinding.module,
        count: 1,
        max_evidence_level: sharedFinding.evidence_level,
        top_finding_text: sharedFinding.finding_text,
      },
      {
        module: uniqueFinding.module,
        count: 1,
        max_evidence_level: uniqueFinding.evidence_level,
        top_finding_text: uniqueFinding.finding_text,
      },
    ],
    high_confidence_findings: [sharedFinding, uniqueFinding],
  }
}

// ── Stateful mock backend ───────────────────────────────────────────────

interface LinkState {
  /** sample_id → individual_id mapping for currently-linked samples. */
  sampleToIndividual: Map<number, number>
  /** id-incrementing counter that mirrors the backend's autoincrement
   *  PK behavior; in this spec only one create() is exercised. */
  nextIndividualId: number
  /** Created individuals in insertion order so list/detail can be derived. */
  individuals: Array<{ id: number; display_name: string }>
}

function buildIndividualsSummary(state: LinkState) {
  return state.individuals.map((ind) => {
    const linkedSampleIds = Array.from(state.sampleToIndividual.entries())
      .filter(([, indId]) => indId === ind.id)
      .map(([sampleId]) => sampleId)
    const vendors = new Set<string>()
    for (const sampleId of linkedSampleIds) {
      if (sampleId === SAMPLE_1_ID) vendors.add('23andme')
      if (sampleId === SAMPLE_2_ID) vendors.add('ancestrydna')
    }
    return {
      id: ind.id,
      display_name: ind.display_name,
      notes: null,
      biological_sex: null,
      created_at: '2026-05-20T00:00:00Z',
      updated_at: '2026-05-20T00:00:00Z',
      sample_count: linkedSampleIds.length,
      vendors: Array.from(vendors),
      last_activity: '2026-05-20T00:00:00Z',
    }
  })
}

function buildIndividualDetail(state: LinkState, individualId: number) {
  const ind = state.individuals.find((i) => i.id === individualId)
  if (!ind) return null
  const linked: Array<Record<string, unknown>> = []
  for (const [sampleId, indId] of state.sampleToIndividual.entries()) {
    if (indId !== individualId) continue
    const sample =
      sampleId === SAMPLE_1_ID ? SAMPLE_23ANDME : SAMPLE_ANCESTRYDNA
    const vendor = sample.file_format?.split('_', 1)[0] ?? null
    linked.push({
      id: sample.id,
      name: sample.name,
      file_format: sample.file_format,
      vendor,
      created_at: sample.created_at,
      updated_at: sample.updated_at,
    })
  }
  linked.sort((a, b) => Number(a.id) - Number(b.id))
  return {
    id: ind.id,
    display_name: ind.display_name,
    notes: null,
    biological_sex: null,
    created_at: '2026-05-20T00:00:00Z',
    updated_at: '2026-05-20T00:00:00Z',
    linked_samples: linked,
    aggregated_findings_count: 3,
  }
}

async function setupRoutes(page: Page, state: LinkState): Promise<void> {
  // ── App-shell quiet mocks ────────────────────────────────────────
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
        disclaimer_accepted: true,
        data_dir: '/tmp/.yeliztli',
        needs_setup: false,
        has_databases: true,
        has_samples: true,
      }),
    })
  })

  await page.route('**/api/updates/app-update', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        update_available: false,
        current_version: '0.2.0',
        latest_version: null,
        release_url: null,
        release_notes: null,
        error: null,
      }),
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

  // ── Domain mocks ─────────────────────────────────────────────────
  await page.route('**/api/samples', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify([SAMPLE_23ANDME, SAMPLE_ANCESTRYDNA]),
    })
  })

  await page.route(/\/api\/samples\/\d+$/, async (route) => {
    const url = route.request().url()
    const id = Number(url.split('/').pop())
    const sample = id === SAMPLE_1_ID ? SAMPLE_23ANDME : SAMPLE_ANCESTRYDNA
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(sample),
    })
  })

  await page.route('**/api/variants/count**', async (route) => {
    // IndividualDetail's per-sample LinkedSampleRow probes this; any
    // positive number is enough to land the page on its "Ready" state.
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ total: 600_000 }),
    })
  })

  await page.route('**/api/analysis/findings/summary**', async (route) => {
    const url = new URL(route.request().url())
    const sampleId = Number(url.searchParams.get('sample_id'))
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(findingsSummaryFor(sampleId)),
    })
  })

  // ── Individuals routes (stateful) ────────────────────────────────
  await page.route('**/api/individuals', async (route) => {
    const method = route.request().method()
    if (method === 'POST') {
      const body = JSON.parse(route.request().postData() ?? '{}') as {
        display_name?: string
      }
      const created = {
        id: state.nextIndividualId,
        display_name: body.display_name ?? 'Untitled',
      }
      state.nextIndividualId += 1
      state.individuals.push(created)
      const detail = buildIndividualDetail(state, created.id)!
      await route.fulfill({
        status: 201,
        contentType: 'application/json',
        body: JSON.stringify(detail),
      })
      return
    }
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(buildIndividualsSummary(state)),
    })
  })

  await page.route(/\/api\/individuals\/\d+$/, async (route) => {
    const url = route.request().url()
    const id = Number(url.split('/').pop())
    const detail = buildIndividualDetail(state, id)
    if (!detail) {
      await route.fulfill({
        status: 404,
        contentType: 'application/json',
        body: JSON.stringify({ detail: `Individual ${id} not found` }),
      })
      return
    }
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(detail),
    })
  })

  const linkHandler = async (route: Route) => {
    const url = route.request().url()
    const match = url.match(/\/api\/individuals\/(\d+)\/(link|unlink)-sample$/)
    if (!match) {
      await route.continue()
      return
    }
    const individualId = Number(match[1])
    const action = match[2] as 'link' | 'unlink'
    const body = JSON.parse(route.request().postData() ?? '{}') as {
      sample_id?: number
    }
    const sampleId = Number(body.sample_id)
    if (action === 'link') {
      const existingOwner = state.sampleToIndividual.get(sampleId)
      if (existingOwner != null && existingOwner !== individualId) {
        const owner = state.individuals.find((i) => i.id === existingOwner)!
        await route.fulfill({
          status: 409,
          contentType: 'application/json',
          body: JSON.stringify({
            detail: {
              sample_id: sampleId,
              individual_id: existingOwner,
              individual_display_name: owner.display_name,
              message: `Sample ${sampleId} is already linked to ${owner.display_name}.`,
            },
          }),
        })
        return
      }
      state.sampleToIndividual.set(sampleId, individualId)
    } else {
      if (state.sampleToIndividual.get(sampleId) === individualId) {
        state.sampleToIndividual.delete(sampleId)
      }
    }
    const detail = buildIndividualDetail(state, individualId)!
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(detail),
    })
  }
  await page.route(/\/api\/individuals\/\d+\/link-sample$/, linkHandler)
  await page.route(/\/api\/individuals\/\d+\/unlink-sample$/, linkHandler)
}

// ── Spec ───────────────────────────────────────────────────────────────

test.describe('Step 57 — Individuals linking E2E', () => {
  test('link two samples → aggregated dashboard dedupes shared rsid', async ({
    page,
  }) => {
    const state: LinkState = {
      sampleToIndividual: new Map(),
      nextIndividualId: INDIVIDUAL_ID,
      individuals: [],
    }
    await setupRoutes(page, state)

    // 1) Settings → General lands the SampleMetadataEditor.
    await page.goto('/settings/general')
    await expect(
      page.getByRole('heading', { name: 'General Settings' }),
    ).toBeVisible()

    const editor = page.getByTestId('sample-metadata-editor')
    await expect(editor).toBeVisible()

    // Sample 1: + Create new… → type display name → Create & link.
    const select1 = page.getByTestId(`assign-individual-select-${SAMPLE_1_ID}`)
    await expect(select1).toBeVisible()
    await select1.selectOption('create')

    const newNameInput = page.getByTestId(`assign-new-name-${SAMPLE_1_ID}`)
    await expect(newNameInput).toBeVisible()
    await newNameInput.fill(INDIVIDUAL_DISPLAY_NAME)
    await page.getByTestId(`assign-create-confirm-${SAMPLE_1_ID}`).click()

    // Once the create+link chain resolves, the select reappears with the
    // newly-created individual already selected — that's the visible
    // signal that the cache has been invalidated and re-populated.
    await expect(select1).toBeVisible()
    await expect(select1).toHaveValue(String(INDIVIDUAL_ID))
    expect(state.sampleToIndividual.get(SAMPLE_1_ID)).toBe(INDIVIDUAL_ID)

    // Sample 2: dropdown now lists "Family Tree" — select it.
    const select2 = page.getByTestId(`assign-individual-select-${SAMPLE_2_ID}`)
    await expect(select2).toBeVisible()
    await select2.selectOption(String(INDIVIDUAL_ID))

    await expect(select2).toHaveValue(String(INDIVIDUAL_ID))
    await expect.poll(() => state.sampleToIndividual.get(SAMPLE_2_ID)).toBe(
      INDIVIDUAL_ID,
    )

    // 2) Navigate to the aggregated /individuals/{id} page.
    await page.goto(`/individuals/${INDIVIDUAL_ID}`)
    await expect(page.getByTestId('individual-detail-page')).toBeVisible()
    await expect(
      page.getByRole('heading', { name: INDIVIDUAL_DISPLAY_NAME }),
    ).toBeVisible()

    // Both linked-samples table rows render.
    const sample1Row = page.getByTestId(`linked-sample-row-${SAMPLE_1_ID}`)
    const sample2Row = page.getByTestId(`linked-sample-row-${SAMPLE_2_ID}`)
    await expect(sample1Row).toBeVisible()
    await expect(sample1Row).toContainText('23andMe v5')
    await expect(sample2Row).toBeVisible()
    await expect(sample2Row).toContainText('AncestryDNA v2.0')

    // Aggregated findings: shared-rsid row dedupes to ONE row with TWO
    // provenance chips; unique-rsid rows render once each with one chip.
    const sharedRow = page.getByTestId(`aggregated-finding-rsid:${SHARED_RSID}`)
    await expect(sharedRow).toBeVisible()
    await expect(sharedRow).toContainText('APOE')
    await expect(
      sharedRow.getByTestId(`aggregated-finding-module-rsid:${SHARED_RSID}`),
    ).toHaveText('APOE')
    await expect(sharedRow).toContainText(SHARED_RSID)
    await expect(
      page.getByTestId(
        `provenance-chip-rsid:${SHARED_RSID}-${SAMPLE_1_ID}`,
      ),
    ).toBeVisible()
    await expect(
      page.getByTestId(
        `provenance-chip-rsid:${SHARED_RSID}-${SAMPLE_2_ID}`,
      ),
    ).toBeVisible()

    const sample1UniqueRow = page.getByTestId(
      `aggregated-finding-rsid:${SAMPLE_1_UNIQUE_RSID}`,
    )
    await expect(sample1UniqueRow).toBeVisible()
    await expect(sample1UniqueRow).toContainText('MTHFR')
    await expect(
      page.getByTestId(
        `provenance-chip-rsid:${SAMPLE_1_UNIQUE_RSID}-${SAMPLE_1_ID}`,
      ),
    ).toBeVisible()
    await expect(
      page.getByTestId(
        `provenance-chip-rsid:${SAMPLE_1_UNIQUE_RSID}-${SAMPLE_2_ID}`,
      ),
    ).toHaveCount(0)

    const sample2UniqueRow = page.getByTestId(
      `aggregated-finding-rsid:${SAMPLE_2_UNIQUE_RSID}`,
    )
    await expect(sample2UniqueRow).toBeVisible()
    await expect(sample2UniqueRow).toContainText('CFTR')
    await expect(
      sample2UniqueRow.getByTestId(
        `aggregated-finding-module-rsid:${SAMPLE_2_UNIQUE_RSID}`,
      ),
    ).toHaveText('Carrier Status')
    await expect(
      page.getByTestId(
        `provenance-chip-rsid:${SAMPLE_2_UNIQUE_RSID}-${SAMPLE_2_ID}`,
      ),
    ).toBeVisible()
    await expect(
      page.getByTestId(
        `provenance-chip-rsid:${SAMPLE_2_UNIQUE_RSID}-${SAMPLE_1_ID}`,
      ),
    ).toHaveCount(0)

    // Exactly three aggregated rows — one per unique rsid.
    await expect(
      page.locator('[data-testid^="aggregated-finding-rsid:"]'),
    ).toHaveCount(3)

    // The metadata header's linked-sample count reflects both linked rows.
    await expect(
      page.getByText('Linked samples', { exact: true }),
    ).toBeVisible()
    await expect(page.locator('dd').filter({ hasText: /^2$/ })).toBeVisible()
  })
})
