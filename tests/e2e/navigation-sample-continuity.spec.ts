import { test, expect, type Page } from '@playwright/test'
import { bypassSetup } from './helpers'

const SAMPLE_ID = 7
const SAMPLE_NAME = 'Navigation continuity fixture'

const sample = {
  id: SAMPLE_ID,
  name: SAMPLE_NAME,
  db_path: '/tmp/navigation-continuity.db',
  file_format: '23andme_v5',
  file_hash: 'navigation-continuity',
  notes: null,
  date_collected: null,
  source: null,
  extra: null,
  created_at: '2026-07-19T00:00:00Z',
  updated_at: null,
}

async function mockNavigationData(page: Page): Promise<void> {
  await bypassSetup(page)

  await page.route('**/api/samples', (route) => route.fulfill({ json: [sample] }))
  await page.route('**/api/individuals', (route) => route.fulfill({ json: [] }))
  await page.route('**/api/preferences/theme', (route) =>
    route.fulfill({ json: { theme: 'system' } }),
  )
  await page.route('**/api/preferences/update-check-interval', (route) =>
    route.fulfill({ json: { update_check_interval: 'off' } }),
  )
  await page.route('**/api/updates/app-update', (route) =>
    route.fulfill({
      json: {
        update_available: false,
        current_version: '0.2.0',
        latest_version: null,
        release_url: null,
        release_notes: null,
        error: null,
      },
    }),
  )
  await page.route('**/api/updates/prompts**', (route) => route.fulfill({ json: [] }))
  await page.route('**/api/updates/status', (route) => route.fulfill({ json: [] }))
  await page.route('**/api/updates/check', (route) =>
    route.fulfill({
      json: { available: [], up_to_date: [], errors: [], checked_at: null },
    }),
  )
  await page.route('**/api/databases', (route) =>
    route.fulfill({
      json: {
        databases: [],
        total_size_bytes: 0,
        downloaded_count: 0,
        total_count: 0,
      },
    }),
  )
  await page.route('**/api/analysis/ancestry/lai/status', (route) =>
    route.fulfill({
      json: {
        available: false,
        current_version: null,
        degraded_coverage: false,
      },
    }),
  )
  await page.route(`**/api/annotation/active/${SAMPLE_ID}`, (route) =>
    route.fulfill({ status: 404, json: { detail: 'No active job' } }),
  )
  await page.route('**/api/variants/count**', (route) =>
    route.fulfill({ json: { total: 677_436 } }),
  )
  await page.route('**/api/variants/qc-stats**', (route) =>
    route.fulfill({
      json: {
        total_variants: 677_436,
        called_variants: 670_000,
        nocall_variants: 7_436,
        het_count: 220_000,
        hom_count: 450_000,
        call_rate: 0.989,
        heterozygosity_rate: 0.328,
        per_chromosome: [],
      },
    }),
  )
  await page.route('**/api/analysis/qc/metrics**', (route) =>
    route.fulfill({
      json: {
        computed: true,
        call_rate: 0.989,
        call_rate_pass: true,
        heterozygosity_rate: 0.328,
        ti_tv_ratio: 2.08,
        total_variants: 677_436,
        called_variants: 670_000,
        nocall_variants: 7_436,
        genetic_sex: 'XX',
        recorded_sex: null,
        sex_check: 'unknown',
        het_outlier_z: null,
        het_outlier_status: 'insufficient_comparable_samples',
      },
    }),
  )
  await page.route('**/api/analysis/findings/summary**', (route) =>
    route.fulfill({
      json: { total_findings: 0, modules: [], high_confidence_findings: [] },
    }),
  )
  await page.route('**/api/analysis/modules/summary**', (route) =>
    route.fulfill({ json: { modules: [] } }),
  )
  await page.route('**/api/variants/search**', (route) => route.fulfill({ json: [] }))

  await page.route('**/api/analysis/cancer/variants**', (route) =>
    route.fulfill({ json: { items: [], total: 0 } }),
  )
  await page.route('**/api/analysis/cancer/prs**', (route) =>
    route.fulfill({
      json: { items: [], total: 0, sufficient_count: 0, insufficient_traits: [] },
    }),
  )
  await page.route('**/api/analysis/cancer/disclaimer', (route) =>
    route.fulfill({ json: { title: 'Cancer disclaimer', text: 'Fixture disclaimer.' } }),
  )
  await page.route('**/api/analysis/cancer/absolute-risk**', (route) =>
    route.fulfill({
      json: {
        consented: false,
        opt_in_required: true,
        opt_in_prompt: 'Show optional absolute-risk context.',
        disclaimer: 'Fixture disclaimer.',
      },
    }),
  )
}

async function expectSampleLocation(page: Page, pathname: string): Promise<void> {
  await expect(page).toHaveURL((url) =>
    url.pathname === pathname && url.searchParams.get('sample_id') === String(SAMPLE_ID),
  )
  await expect(page.getByRole('button', { name: 'Switch sample' })).toContainText(
    SAMPLE_NAME,
  )
}

async function expectCancerContent(page: Page): Promise<void> {
  await expect(page.getByRole('heading', { name: 'Cancer Predisposition' })).toBeVisible()
  await expect(page.getByRole('heading', { name: 'Monogenic Findings' })).toBeVisible()
  await expect(page.getByText(/^Select a sample to view/)).toHaveCount(0)
}

test.beforeEach(async ({ page }) => {
  await mockNavigationData(page)
})

test('sample selection survives module cards, sidebar links, and the logo', async ({ page }) => {
  await page.goto('/')
  await page.getByRole('button', { name: 'Switch sample' }).click()
  await page.getByTestId(`sample-row-${SAMPLE_ID}`).click()

  await expectSampleLocation(page, '/')
  const modules = page.getByRole('region', { name: 'Analysis modules' })
  await expect(modules).toBeVisible()

  await modules.getByRole('link', { name: /^Cancer module/ }).click()
  await expectSampleLocation(page, '/cancer')
  await expectCancerContent(page)

  const mainNav = page.getByRole('navigation', { name: 'Main navigation' })
  await mainNav.getByRole('link', { name: 'Dashboard' }).click()
  await expectSampleLocation(page, '/')
  await expect(page.getByRole('region', { name: 'Analysis modules' })).toBeVisible()

  await mainNav.getByRole('link', { name: 'Cancer' }).click()
  await expectSampleLocation(page, '/cancer')
  await expectCancerContent(page)

  await page.getByRole('link', { name: 'Yeliztli' }).click()
  await expectSampleLocation(page, '/')
  await expect(page.getByRole('region', { name: 'Analysis modules' })).toBeVisible()
  await expect(page.getByText('Get Started')).toHaveCount(0)
})

test('command palette page navigation preserves the active sample', async ({ page }) => {
  await page.goto(`/?sample_id=${SAMPLE_ID}`)
  await expect(page.getByRole('region', { name: 'Analysis modules' })).toBeVisible()

  await page.getByTestId('command-palette-trigger').click()
  await page.getByTestId('command-palette-input').fill('Cancer')
  await page.getByRole('option', { name: 'Cancer' }).click()

  await expectSampleLocation(page, '/cancer')
  await expectCancerContent(page)
})

test('command palette locus navigation merges locus with the active sample', async ({ page }) => {
  await page.goto(`/?sample_id=${SAMPLE_ID}`)
  await expect(page.getByRole('region', { name: 'Analysis modules' })).toBeVisible()

  await page.getByTestId('command-palette-trigger').click()
  await page.getByTestId('command-palette-input').fill('BRCA1')
  await page.getByTestId('command-palette-igv-item').click()

  await expect(page).toHaveURL((url) =>
    url.pathname === '/genome-browser' &&
    url.searchParams.get('locus') === 'BRCA1' &&
    url.searchParams.get('sample_id') === String(SAMPLE_ID),
  )
  await expect(page.getByRole('button', { name: 'Switch sample' })).toContainText(
    SAMPLE_NAME,
  )
})
