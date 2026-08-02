/**
 * Issue #2164 — paired UniProt disulfide endpoints remain paired through the
 * Gene Detail browser flow and use Nightingale's non-continuous bridge shape.
 */

import { expect, test } from '@playwright/test'
import { bypassSetup, waitForReactHydration } from './helpers'

test.beforeEach(async ({ page }) => {
  await bypassSetup(page)
})

const INSULIN_GENE_DETAIL = {
  gene_symbol: 'INS',
  uniprot: {
    accession: 'P01308',
    gene_symbol: 'INS',
    sequence_length: 110,
    domains: [],
    features: [
      {
        type: 'Disulfide bond',
        description: 'Interchain (between B and A chains)',
        position: null,
        start: 31,
        end: 96,
        start_modifier: 'EXACT',
        end_modifier: 'EXACT',
      },
      {
        type: 'Disulfide bond',
        description: 'Interchain (between B and A chains)',
        position: null,
        start: 43,
        end: 109,
        start_modifier: 'EXACT',
        end_modifier: 'EXACT',
      },
      {
        type: 'Disulfide bond',
        description: '',
        position: null,
        start: 95,
        end: 100,
        start_modifier: 'EXACT',
        end_modifier: 'EXACT',
      },
    ],
    fetched_at: '2026-08-02T00:00:00Z',
    is_cached: false,
  },
  uniprot_error: null,
  phenotypes: [],
  literature: [],
  literature_errors: [],
  variants: [],
  population_af: [],
}

test('renders known P01308 disulfide pairs as labeled Nightingale bridges', async ({ page }) => {
  await page.route('**/api/genes/INS**', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(INSULIN_GENE_DETAIL),
    }),
  )

  await page.goto('/genes/INS?sample_id=1')
  await waitForReactHydration(page)

  await expect(page.getByText('Features', { exact: true })).toBeVisible()
  const featureTrack = page.locator('nightingale-track[aria-label="Protein features"]')
  await expect(featureTrack).toBeVisible()

  const data = await featureTrack.evaluate((element) => {
    const value = (element as HTMLElement & { data?: unknown }).data
    return Array.isArray(value) ? value : []
  })

  expect(data).toHaveLength(3)
  expect(data).toMatchObject([
    {
      type: 'Disulfide bond',
      shape: 'bridge',
      start: 31,
      end: 96,
      tooltipContent: 'Disulfide bond: Interchain (between B and A chains) (31–96)',
      locations: [{ fragments: [{ start: 31, end: 96 }] }],
    },
    {
      type: 'Disulfide bond',
      shape: 'bridge',
      start: 43,
      end: 109,
      tooltipContent: 'Disulfide bond: Interchain (between B and A chains) (43–109)',
      locations: [{ fragments: [{ start: 43, end: 109 }] }],
    },
    {
      type: 'Disulfide bond',
      shape: 'bridge',
      start: 95,
      end: 100,
      tooltipContent: 'Disulfide bond (95–100)',
      locations: [{ fragments: [{ start: 95, end: 100 }] }],
    },
  ])
})
