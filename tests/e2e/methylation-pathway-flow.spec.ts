/**
 * Issue #665 — the methylation pathway SVG keeps selected-node rings thicker
 * than normal borders and starts diagonal connectors on node edges.
 */

import { test, expect } from '@playwright/test'
import { bypassSetup, waitForReactHydration } from './helpers'

test.beforeEach(async ({ page }) => {
  await bypassSetup(page)
})

const PATHWAYS = [
  {
    pathway_id: 'folate_mthfr',
    pathway_name: 'Folate & MTHFR',
    level: 'Elevated',
  },
  {
    pathway_id: 'methionine_cycle',
    pathway_name: 'Methionine Cycle',
    level: 'Moderate',
  },
  {
    pathway_id: 'transsulfuration',
    pathway_name: 'Transsulfuration',
    level: 'Standard',
  },
  {
    pathway_id: 'bh4_neurotransmitter',
    pathway_name: 'BH4 & Neurotransmitter',
    level: 'Moderate',
  },
  {
    pathway_id: 'choline_betaine',
    pathway_name: 'Choline & Betaine',
    level: 'Standard',
  },
].map((pathway) => ({
  ...pathway,
  evidence_level: 1,
  called_snps: 1,
  total_snps: 1,
  missing_snps: [],
  pmids: [],
  additive_promoted: false,
}))

test('renders selected ring and diagonal connectors without SVG class/geometry regressions', async ({
  page,
}) => {
  await page.route('**/api/analysis/methylation/pathways**', async (route) => {
    await route.fulfill({
      json: {
        items: PATHWAYS,
        total: PATHWAYS.length,
        compound_het: null,
      },
    })
  })
  await page.route('**/api/analysis/methylation/pathway/**', async (route) => {
    await route.fulfill({
      json: {
        ...PATHWAYS[0],
        snp_details: [],
      },
    })
  })

  await page.goto('/methylation?sample_id=1')
  await waitForReactHydration(page)

  const diagram = page.getByRole('img', {
    name: 'Methylation pathway flow diagram showing biochemical relationships',
  })
  await expect(diagram).toBeVisible()

  const folateNode = diagram.getByRole('button', { name: /Folate & MTHFR — Elevated/ })
  const methionineNode = diagram.getByRole('button', { name: /Methionine Cycle — Moderate/ })

  await folateNode.click()

  const selectedRect = folateNode.locator('rect')
  await expect(selectedRect).toHaveClass(/stroke-\[3\]/)
  await expect(selectedRect).not.toHaveClass(/stroke-\[2\]/)
  await expect(methionineNode.locator('rect')).toHaveClass(/stroke-\[2\]/)

  const folateToBh4 = diagram.locator('line').nth(2)
  const methionineToCholine = diagram.locator('line').nth(3)

  await expect(folateToBh4).toHaveAttribute('y1', /^88(?:\.0+)?$/)
  await expect(folateToBh4).toHaveAttribute('y2', /^152(?:\.0+)?$/)
  await expect(methionineToCholine).toHaveAttribute('y1', /^88(?:\.0+)?$/)
  await expect(methionineToCholine).toHaveAttribute('y2', /^152(?:\.0+)?$/)
})
