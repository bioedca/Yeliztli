/**
 * Issue #781 — APOE source-discrepancy and array-reliability caveats must be
 * visible only after the APOE disclosure gate is acknowledged.
 */

import { test, expect } from '@playwright/test'
import { bypassSetup, waitForReactHydration } from './helpers'

const SAMPLE_ID = 1

const jsonRoute = (body: unknown) => ({
  status: 200,
  contentType: 'application/json',
  body: JSON.stringify(body),
})

const APOE_DISCLAIMER = {
  title: 'APOE disclosure',
  text: 'APOE results can include sensitive health information.',
  accept_label: 'Show Results',
  decline_label: 'Skip',
}

const APOE_GENOTYPE = {
  status: 'determined',
  diplotype: 'e3/e4',
  has_e4: true,
  e4_count: 1,
  has_e2: false,
  e2_count: 0,
  rs429358_genotype: 'CT',
  rs7412_genotype: 'CC',
}

const APOE_FINDINGS_WITH_CAVEATS = {
  items: [
    {
      category: 'alzheimers_risk',
      evidence_level: 4,
      finding_text: 'Late-onset Alzheimer risk context is elevated.',
      phenotype: 'APOE e4 carrier',
      conditions: "Alzheimer's disease",
      diplotype: 'e3/e4',
      pmid_citations: ['21460841'],
      detail_json: {
        risk_level: 'elevated',
        array_reliability: {
          caveat:
            'APOE ε-status here is derived from consumer genotyping-array calls at rs429358 and rs7412. These two ε-defining SNPs are a recognised array weak spot.',
          confirm_in_clia_recommended: true,
          concordance_with_direct_genotyping: '~90% ε genotype / ~93% ε4 status',
          pmids: ['24448547', '22972946'],
        },
        source_discrepancies: [
          {
            rsid: 'rs429358',
            gene: 'APOE',
            kept_genotype: 'CT',
            affects_e4_status: true,
            note:
              'Your source files disagree at rs429358 (APOE): S1 reports TT → ε3/ε3 (ε4 absent); S2 reports CT → ε3/ε4 (ε4 present). This directly changes ε4 status.',
            calls: [
              {
                source: 'S1',
                genotype: 'TT',
                implied_diplotype: 'ε3/ε3',
                e4_present: false,
              },
              {
                source: 'S2',
                genotype: 'CT',
                implied_diplotype: 'ε3/ε4',
                e4_present: true,
              },
            ],
          },
        ],
      },
    },
  ],
  total: 1,
}

test.beforeEach(async ({ page }) => {
  await bypassSetup(page)
  await page.route('**/api/analysis/apoe/disclaimer', (route) =>
    route.fulfill(jsonRoute(APOE_DISCLAIMER)),
  )
  await page.route('**/api/analysis/apoe/genotype**', (route) =>
    route.fulfill(jsonRoute(APOE_GENOTYPE)),
  )
})

test.describe('APOE caveats (#781)', () => {
  test('does not render discrepancy caveats before gate acknowledgement', async ({ page }) => {
    let findingsRequested = false

    await page.route('**/api/analysis/apoe/gate-status**', (route) =>
      route.fulfill(jsonRoute({ acknowledged: false, acknowledged_at: null })),
    )
    await page.route('**/api/analysis/apoe/findings**', (route) => {
      findingsRequested = true
      return route.fulfill(jsonRoute(APOE_FINDINGS_WITH_CAVEATS))
    })

    await page.goto(`/apoe?sample_id=${SAMPLE_ID}`)
    await waitForReactHydration(page)

    await expect(page.getByTestId('apoe-gate')).toBeVisible()
    await expect(page.getByTestId('apoe-caveats')).toHaveCount(0)
    expect(findingsRequested).toBe(false)
  })

  test('renders source discrepancy and array reliability after acknowledgement', async ({ page }) => {
    await page.route('**/api/analysis/apoe/gate-status**', (route) =>
      route.fulfill(
        jsonRoute({
          acknowledged: true,
          acknowledged_at: '2026-06-16T00:00:00Z',
        }),
      ),
    )
    await page.route('**/api/analysis/apoe/findings**', (route) =>
      route.fulfill(jsonRoute(APOE_FINDINGS_WITH_CAVEATS)),
    )

    await page.goto(`/apoe?sample_id=${SAMPLE_ID}`)
    await waitForReactHydration(page)

    await expect(page.getByTestId('apoe-caveats')).toBeVisible()
    await expect(page.getByTestId('apoe-source-discrepancy-rs429358')).toContainText(
      'Changes ε4 status',
    )
    await expect(page.getByTestId('apoe-source-discrepancy-rs429358')).toContainText('S1')
    await expect(page.getByTestId('apoe-source-discrepancy-rs429358')).toContainText('TT')
    await expect(page.getByTestId('apoe-source-discrepancy-rs429358')).toContainText('ε3/ε3')
    await expect(page.getByTestId('apoe-source-discrepancy-rs429358')).toContainText(
      'ε4 absent',
    )
    await expect(page.getByTestId('apoe-source-discrepancy-rs429358')).toContainText('S2')
    await expect(page.getByTestId('apoe-source-discrepancy-rs429358')).toContainText('CT')
    await expect(page.getByTestId('apoe-source-discrepancy-rs429358')).toContainText('ε3/ε4')
    await expect(page.getByTestId('apoe-source-discrepancy-rs429358')).toContainText(
      'ε4 present',
    )

    await expect(page.getByTestId('apoe-array-reliability')).toContainText(
      'CLIA confirmation recommended',
    )
    await expect(page.getByTestId('apoe-array-reliability')).toContainText('array weak spot')
    await expect(page.getByTestId('apoe-array-reliability')).toContainText(
      '~90% ε genotype / ~93% ε4 status',
    )
  })
})
