import AxeBuilder from '@axe-core/playwright'
import { expect, test, type Page } from '@playwright/test'
import { bypassSetup, waitForReactHydration } from './helpers'

const INDIVIDUAL_ID = 90
const GOOD_SAMPLE_ID = 71
const FAILED_SAMPLE_ID = 72
const FINDINGS_SECTION = 'section[aria-label="Aggregated high-confidence findings"]'

interface LinkedSample {
  id: number
  name: string
  file_format: string
  vendor: string
  is_merged: boolean
  created_at: string
  updated_at: null
}

const GOOD_SAMPLE: LinkedSample = {
  id: GOOD_SAMPLE_ID,
  name: 'morgan_23andme.txt',
  file_format: '23andme_v5',
  vendor: '23andme',
  is_merged: false,
  created_at: '2026-05-01T00:00:00',
  updated_at: null,
}

const FAILED_SAMPLE: LinkedSample = {
  id: FAILED_SAMPLE_ID,
  name: 'morgan_ancestry.txt',
  file_format: 'ancestrydna_v2.0',
  vendor: 'ancestrydna',
  is_merged: false,
  created_at: '2026-05-01T00:00:00',
  updated_at: null,
}

const jsonRoute = (payload: unknown, status = 200) => ({
  status,
  contentType: 'application/json',
  body: JSON.stringify(payload),
})

function findingsSummary(sampleId: number) {
  const isGoodSample = sampleId === GOOD_SAMPLE_ID
  return {
    total_findings: 1,
    modules: [],
    high_confidence_findings: [
      {
        id: isGoodSample ? 7101 : 7201,
        module: isGoodSample ? 'pharmacogenomics' : 'carrier',
        category: null,
        evidence_level: isGoodSample ? 3 : 4,
        gene_symbol: isGoodSample ? 'CYP2C9' : 'CFTR',
        rsid: isGoodSample ? 'rs1057910' : 'rs113993960',
        finding_text: isGoodSample
          ? 'CYP2C9 intermediate metabolizer'
          : 'CFTR ΔF508 carrier',
        phenotype: null,
        conditions: null,
        zygosity: null,
        clinvar_significance: null,
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
        created_at: null,
      },
    ],
  }
}

async function mockIndividual(
  page: Page,
  linkedSamples: LinkedSample[],
  failedAttemptsBeforeSuccess: number,
): Promise<Map<number, number>> {
  const summaryCalls = new Map<number, number>()

  await page.route(`**/api/individuals/${INDIVIDUAL_ID}`, (route) =>
    route.fulfill(
      jsonRoute({
        id: INDIVIDUAL_ID,
        display_name: 'Morgan',
        notes: null,
        biological_sex: 'XX',
        created_at: '2026-05-01T00:00:00',
        updated_at: null,
        linked_samples: linkedSamples,
        aggregated_findings_count: linkedSamples.length,
      }),
    ),
  )
  await page.route('**/api/variants/count**', (route) =>
    route.fulfill(jsonRoute({ total: 600_000 })),
  )
  await page.route('**/api/analysis/findings/summary**', (route) => {
    const sampleId = Number(new URL(route.request().url()).searchParams.get('sample_id'))
    const calls = (summaryCalls.get(sampleId) ?? 0) + 1
    summaryCalls.set(sampleId, calls)

    if (sampleId === FAILED_SAMPLE_ID && calls <= failedAttemptsBeforeSuccess) {
      return route.fulfill(jsonRoute({ detail: 'summary unavailable' }, 500))
    }
    return route.fulfill(jsonRoute(findingsSummary(sampleId)))
  })

  return summaryCalls
}

test.beforeEach(async ({ page }) => {
  await bypassSetup(page)
})

test('an unavailable findings summary renders an error instead of a benign empty state (#1785)', async ({
  page,
}) => {
  const calls = await mockIndividual(page, [FAILED_SAMPLE], 2)

  await page.goto(`/individuals/${INDIVIDUAL_ID}`)
  await waitForReactHydration(page)

  const section = page.locator(FINDINGS_SECTION)
  const alert = section.getByRole('alert')
  await expect(alert).toContainText("Couldn’t load high-confidence findings")
  await expect(alert).toContainText('morgan_ancestry.txt (sample #72)')
  await expect(alert.getByRole('button', { name: 'Retry' })).toBeVisible()
  await expect(section.getByText('No high-confidence findings yet')).toHaveCount(0)
  expect(calls.get(FAILED_SAMPLE_ID)).toBe(2)
})

test('partial findings stay visible and retry refreshes only the failed sample (#1785)', async ({
  page,
}) => {
  const calls = await mockIndividual(page, [GOOD_SAMPLE, FAILED_SAMPLE], 2)

  await page.goto(`/individuals/${INDIVIDUAL_ID}`)
  await waitForReactHydration(page)

  const section = page.locator(FINDINGS_SECTION)
  const alert = section.getByRole('alert')
  await expect(alert).toContainText('morgan_ancestry.txt (sample #72)')
  await expect(alert).not.toContainText('morgan_23andme.txt')
  await expect(section.getByTestId('aggregated-finding-rsid:rs1057910')).toBeVisible()
  await expect(section.getByText('1 loaded (partial)')).toBeVisible()
  await expect(section.getByText('No high-confidence findings yet')).toHaveCount(0)
  await expect(section.getByTestId('aggregated-findings-overflow')).toHaveCount(0)

  const accessibility = await new AxeBuilder({ page })
    .include(FINDINGS_SECTION)
    .withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'])
    .analyze()
  expect(
    accessibility.violations.map(({ id, impact, nodes }) => ({
      id,
      impact,
      targets: nodes.flatMap((node) => node.target),
    })),
  ).toEqual([])

  await alert.getByRole('button', { name: 'Retry' }).click()

  await expect(section.getByTestId('aggregated-finding-rsid:rs113993960')).toBeVisible()
  await expect(section.getByRole('alert')).toHaveCount(0)
  await expect(section.getByText('2 unique')).toBeVisible()
  expect(calls.get(GOOD_SAMPLE_ID)).toBe(1)
  expect(calls.get(FAILED_SAMPLE_ID)).toBe(3)
})
