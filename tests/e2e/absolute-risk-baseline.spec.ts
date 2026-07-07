/**
 * Issue #1657 - breast absolute-risk overlay renders the current SEER baseline.
 */

import { expect, test } from '@playwright/test'
import { bypassSetup, waitForReactHydration } from './helpers'

const jsonRoute = (payload: unknown, status = 200) => ({
  status,
  contentType: 'application/json',
  body: JSON.stringify(payload),
})

test.beforeEach(async ({ page }) => {
  await bypassSetup(page)
})

test('Cancer page renders the current SEER population baseline after opt-in (#1657)', async ({
  page,
}) => {
  await page.route('**/api/analysis/cancer/variants**', (route) =>
    route.fulfill(jsonRoute({ items: [], total: 0 })),
  )
  await page.route('**/api/analysis/cancer/prs**', (route) =>
    route.fulfill(
      jsonRoute({
        items: [],
        total: 0,
        sufficient_count: 0,
        insufficient_traits: [],
      }),
    ),
  )
  await page.route('**/api/analysis/cancer/disclaimer', (route) =>
    route.fulfill(
      jsonRoute({
        title: 'Cancer module disclaimer',
        text: 'For research and educational use only.',
      }),
    ),
  )
  await page.route('**/api/analysis/cancer/absolute-risk**', (route) =>
    route.fulfill(
      jsonRoute({
        consented: true,
        opt_in_required: false,
        inferred_sex: 'XX',
        sex_context: 'female',
        sex_note: 'Figures shown are female-specific (biological sex XX).',
        population_baseline: {
          lifetime_risk_pct: 13.0,
          data_years: '2021-2023',
          source: 'NCI SEER Cancer Stat Facts: Female Breast Cancer',
          source_url: 'https://seer.cancer.gov/statfacts/html/breast.html',
          note: 'About 1 in 8 US women are diagnosed with breast cancer over their lifetime (SEER 2021-2023 data).',
        },
        has_monogenic: false,
        monogenic: [],
        prs_note: 'A personalized polygenic absolute risk is not shown here.',
        canrisk: {
          tool: 'CanRisk / BOADICEA',
          url: 'https://www.canrisk.org',
          pmid: '30643217',
          note: 'Validated multifactorial model.',
        },
        disclaimer: 'For research and educational use only.',
      }),
    ),
  )

  await page.goto('/cancer?sample_id=1')
  await waitForReactHydration(page)

  const overlay = page.getByTestId('absolute-risk-overlay')
  await expect(overlay).toBeVisible()
  await expect(overlay).toContainText('Population lifetime risk: 13%')
  await expect(overlay).toContainText('SEER 2021-2023 data')
  await expect(overlay).toContainText('NCI SEER Cancer Stat Facts: Female Breast Cancer')
})
