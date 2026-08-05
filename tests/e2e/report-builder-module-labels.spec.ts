/**
 * Issue #1497 — Report Builder used its local display-name map and then
 * title-cased unknown module keys, so panel-only acronyms that already exist in
 * MODULE_META rendered as "Amd" instead of "AMD".
 */

import { test, expect } from '@playwright/test'
import { bypassSetup, waitForReactHydration } from './helpers'

test.beforeEach(async ({ page }) => {
  await bypassSetup(page)
  // #2236 moved /reports under StaleSampleRouteGate, so StaleSampleGate now
  // probes sample freshness with GET /api/variants/count before rendering any
  // child. Unstubbed, that probe reaches the real backend, which has no seeded
  // sample: the gate renders its "Unable to verify sample freshness" panel (or
  // nothing at all while the first fetch is in flight) instead of the Report
  // Builder, and every assertion below fails for a reason unrelated to reports.
  // Only 200 and 404 let the gate render children; 200 keeps the sample fresh.
  await page.route('**/api/variants/count**', (route) =>
    route.fulfill({ json: { total: 1, filtered: false } }),
  )
})

const SUMMARY = {
  total_findings: 3,
  modules: [
    {
      module: 'amd',
      count: 1,
      max_evidence_level: 2,
      top_finding_text: 'AMD risk',
    },
    {
      module: 'research_panel',
      count: 2,
      max_evidence_level: 2,
      top_finding_text: 'Research risk',
    },
  ],
  high_confidence_findings: [],
}

const LARGE_REPORT_SUMMARY = {
  total_findings: 66771,
  modules: [
    {
      module: 'rare_variants',
      count: 66770,
      max_evidence_level: 1,
      top_finding_text: 'Large rare-variant inventory',
    },
    {
      module: 'carrier',
      count: 1,
      max_evidence_level: 3,
      top_finding_text: 'Carrier finding',
    },
  ],
  high_confidence_findings: [],
}

test.describe('Report Builder module labels and export guard (#1497, #1559, #1990)', () => {
  test('uses canonical registry labels before humanizing module keys', async ({ page }) => {
    await page.route('**/api/analysis/findings/summary**', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(SUMMARY),
      })
    })

    await page.goto('/reports?sample_id=1')
    await waitForReactHydration(page)

    await expect(page.getByRole('button', { name: 'AMD: 1 findings' })).toBeVisible()
    await expect(page.getByText('AMD', { exact: true })).toBeVisible()
    await expect(page.getByText('Amd', { exact: true })).toHaveCount(0)

    await expect(page.getByRole('button', { name: 'Research Panel: 2 findings' })).toBeVisible()
  })

  test('blocks every report action until a large default selection is reduced', async ({ page }) => {
    let previewRequests = 0
    let pdfRequests = 0
    let fhirRequests = 0
    await page.route('**/api/analysis/findings/summary**', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(LARGE_REPORT_SUMMARY),
      })
    })
    await page.route('**/api/export/fhir/eligibility**', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          exportable: false,
          max_observations: 1000,
          observation_count: null,
          reason: 'too_large',
        }),
      })
    })

    await page.route('**/api/reports/preview', async (route) => {
      previewRequests += 1
      await route.fulfill({
        status: 200,
        contentType: 'text/html',
        body: '<html><body>Should not render</body></html>',
      })
    })
    await page.route('**/api/reports/generate', async (route) => {
      pdfRequests += 1
      await route.fulfill({ status: 200, contentType: 'application/pdf', body: 'not reached' })
    })
    await page.route('**/api/export/fhir', async (route) => {
      fhirRequests += 1
      await route.fulfill({ status: 200, contentType: 'application/fhir+json', body: '{}' })
    })

    await page.goto('/reports?sample_id=1')
    await waitForReactHydration(page)

    await expect(page.getByRole('button', { name: /Rare Variant Finder: 66,?770 findings/ })).toBeVisible()
    await expect(page.getByRole('button', { name: 'Carrier Status: 1 findings' })).toBeVisible()

    await expect(page.getByRole('button', { name: 'Preview report' })).toBeDisabled()
    await expect(page.getByText(/Report actions are disabled for selections with more than/)).toBeVisible()
    await expect(page.getByRole('button', { name: 'Download PDF report' })).toBeDisabled()
    await expect(page.getByRole('button', { name: 'Export FHIR R4 Bundle' })).toBeDisabled()
    await expect(page.locator('iframe[title="Report preview"]')).toHaveCount(0)
    expect(previewRequests).toBe(0)
    expect(pdfRequests).toBe(0)
    expect(fhirRequests).toBe(0)

    await page.getByRole('button', { name: /Rare Variant Finder: 66,?770 findings/ }).click()

    await expect(page.getByText(/Report actions are disabled for selections with more than/)).toHaveCount(0)
    await expect(page.getByRole('button', { name: 'Preview report' })).toBeEnabled()
    await expect(page.getByRole('button', { name: 'Download PDF report' })).toBeEnabled()
    await expect(page.getByRole('button', { name: 'Export FHIR R4 Bundle' })).toBeDisabled()
    await expect(page.getByText(/FHIR export is disabled because it would create more than/)).toBeVisible()
  })
})
