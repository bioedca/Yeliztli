import { test, expect, type Page } from '@playwright/test'
import { bypassSetup } from './helpers'

const SAMPLE_ID = 1

const jsonRoute = (body: unknown) => ({
  status: 200,
  contentType: 'application/json',
  body: JSON.stringify(body),
})

async function mockDashboard(page: Page) {
  await bypassSetup(page)
  await page.route('**/api/updates/app-update', (route) =>
    route.fulfill(jsonRoute({ update_available: false, latest_version: null })),
  )
  await page.route('**/api/analysis/ancestry/lai/status', (route) =>
    route.fulfill(jsonRoute({ available: false, current_version: null, degraded_coverage: false })),
  )
  await page.route('**/api/updates/prompts**', (route) => route.fulfill(jsonRoute([])))
  await page.route('**/api/updates/status', (route) => route.fulfill(jsonRoute([])))
  await page.route('**/api/updates/check', (route) =>
    route.fulfill(jsonRoute({ available: [], up_to_date: [], errors: [], checked_at: null })),
  )
  await page.route('**/api/databases', (route) =>
    route.fulfill(jsonRoute({ databases: [], total_size_bytes: 0, downloaded_count: 0, total_count: 0 })),
  )
  await page.route('**/api/samples', (route) =>
    route.fulfill(
      jsonRoute([
        {
          id: SAMPLE_ID,
          name: 'QC Fixture',
          db_path: '/tmp/qc-fixture.db',
          file_format: '23andme_v5',
          file_hash: 'abc123',
          notes: null,
          date_collected: null,
          source: null,
          extra: null,
          created_at: '2026-06-16T00:00:00Z',
          updated_at: null,
        },
      ]),
    ),
  )
  await page.route('**/api/individuals', (route) => route.fulfill(jsonRoute([])))
  await page.route('**/api/variants/count**', (route) => route.fulfill(jsonRoute({ total: 623841 })))
  await page.route('**/api/variants/qc-stats**', (route) =>
    route.fulfill(
      jsonRoute({
        total_variants: 623841,
        called_variants: 610000,
        nocall_variants: 13841,
        het_count: 210000,
        hom_count: 400000,
        call_rate: 0.977817,
        heterozygosity_rate: 0.344262,
        per_chromosome: [],
      }),
    ),
  )
  await page.route('**/api/analysis/qc/metrics**', (route) =>
    route.fulfill(
      jsonRoute({
        computed: true,
        call_rate: 0.977817,
        call_rate_pass: true,
        heterozygosity_rate: 0.344262,
        ti_tv_ratio: 2.08,
        total_variants: 623841,
        called_variants: 610000,
        nocall_variants: 13841,
        genetic_sex: 'XX',
        recorded_sex: 'XY',
        sex_check: 'discordant',
        het_outlier_z: null,
        het_outlier_status: 'insufficient_comparable_samples',
      }),
    ),
  )
  await page.route('**/api/analysis/findings/summary**', (route) =>
    route.fulfill(jsonRoute({ total_findings: 0, modules: [], high_confidence_findings: [] })),
  )
  await page.route('**/api/analysis/findings**', (route) =>
    route.fulfill(jsonRoute({ findings: [], total: 0 })),
  )
  await page.route('**/api/analysis/modules/summary**', (route) =>
    route.fulfill(jsonRoute({ modules: [] })),
  )
}

test.describe('Dashboard QC metrics (#801)', () => {
  test('surfaces het-outlier and sex-concordance statuses in Sample QC', async ({ page }) => {
    await mockDashboard(page)

    await page.goto(`/?sample_id=${SAMPLE_ID}`)

    const qcButton = page.getByRole('button', { name: /Sample QC/i })
    await expect(qcButton).toBeVisible()
    await qcButton.click()

    await expect(page.getByText('No comparable array peers')).toBeVisible()
    await expect(
      page.getByText('No other samples on the same genotyping array to compare against.'),
    ).toBeVisible()
    await expect(page.getByText('Sex concordance')).toBeVisible()
    await expect(page.getByText('Discordant', { exact: true })).toBeVisible()
    await expect(page.getByText('Recorded and inferred sex are discordant.')).toBeVisible()
    await expect(page.getByText(/Inferred: XX/)).toBeVisible()
    await expect(page.getByText(/Recorded: XY/)).toBeVisible()
    await expect(
      page.getByText('Concordance check only; not an aneuploidy assessment.'),
    ).toBeVisible()
  })
})
