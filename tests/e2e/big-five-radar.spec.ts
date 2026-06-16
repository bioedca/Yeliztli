/**
 * Issue #650 — the Big Five radar label "Conscientiousness" was centered near
 * the SVG right edge and clipped to "Conscientiousn". Render the real Traits
 * page and compare each SVG text label's browser `getBBox()` to the SVG
 * viewBox so label overflow cannot regress silently.
 */

import { test, expect } from '@playwright/test'
import { bypassSetup, waitForReactHydration } from './helpers'

test.beforeEach(async ({ page }) => {
  await bypassSetup(page)
})

function jsonRoute(payload: unknown, status = 200) {
  return { status, contentType: 'application/json', body: JSON.stringify(payload) }
}

const BIG_FIVE_LABELS = [
  'Openness',
  'Conscientiousness',
  'Extraversion',
  'Agreeableness',
  'Neuroticism',
]

function snpDetail(traitDomain: string, category = 'Moderate') {
  return {
    rsid: `rs-${traitDomain}`,
    gene: 'GWAS',
    variant_name: traitDomain,
    genotype: 'AA',
    category,
    effect_summary: `${traitDomain} association`,
    evidence_level: 2,
    trait_domain: traitDomain,
    recommendation: null,
    pmids: [],
    coverage_note: null,
    cross_module: null,
  }
}

test.describe('Big Five radar labels stay within the SVG viewport (#650)', () => {
  test('the longest axis label is fully visible in the real Traits page', async ({
    page,
  }) => {
    await page.route('**/api/analysis/traits/disclaimer', async (route) => {
      await route.fulfill(
        jsonRoute({
          disclaimer: 'Research use only.',
          evidence_cap: 2,
          research_use_only: true,
        }),
      )
    })
    await page.route('**/api/analysis/traits/prs**', async (route) => {
      await route.fulfill(jsonRoute({ items: [], total: 0, module_disclaimer: '' }))
    })
    await page.route('**/api/analysis/traits/pathways**', async (route) => {
      await route.fulfill(
        jsonRoute({ items: [], total: 0, cross_module: [], module_disclaimer: '' }),
      )
    })
    await page.route('**/api/analysis/traits/pathway/personality_big_five**', async (route) => {
      await route.fulfill(
        jsonRoute({
          pathway_id: 'personality_big_five',
          pathway_name: 'Big Five Personality',
          level: 'Moderate',
          evidence_level: 2,
          prs_primary: false,
          called_snps: 5,
          total_snps: 5,
          missing_snps: [],
          pmids: [],
          snp_details: [
            snpDetail('openness'),
            snpDetail('conscientiousness', 'Elevated'),
            snpDetail('extraversion'),
            snpDetail('agreeableness'),
            snpDetail('neuroticism'),
          ],
        }),
      )
    })

    await page.goto('/traits?sample_id=1')
    await waitForReactHydration(page)

    const radar = page.getByRole('img', {
      name: /Big Five personality trait associations/,
    })
    await expect(radar).toBeVisible()

    expect(BIG_FIVE_LABELS).toHaveLength(5)
    for (const label of BIG_FIVE_LABELS) {
      const labelNode = radar.locator('text').filter({ hasText: label })
      await expect(labelNode).toBeVisible()

      const bounds = await labelNode.evaluate((node) => {
        const text = node as SVGTextElement
        const svg = text.ownerSVGElement
        if (!svg) {
          throw new Error('Big Five label is not inside an SVG')
        }

        const box = text.getBBox()
        const viewBox = svg.viewBox.baseVal
        return {
          left: box.x,
          right: box.x + box.width,
          top: box.y,
          bottom: box.y + box.height,
          viewLeft: viewBox.x,
          viewRight: viewBox.x + viewBox.width,
          viewTop: viewBox.y,
          viewBottom: viewBox.y + viewBox.height,
        }
      })

      expect(bounds.left, `${label} overflows the SVG left edge`).toBeGreaterThanOrEqual(
        bounds.viewLeft - 0.5,
      )
      expect(bounds.right, `${label} overflows the SVG right edge`).toBeLessThanOrEqual(
        bounds.viewRight + 0.5,
      )
      expect(bounds.top, `${label} overflows the SVG top edge`).toBeGreaterThanOrEqual(
        bounds.viewTop - 0.5,
      )
      expect(bounds.bottom, `${label} overflows the SVG bottom edge`).toBeLessThanOrEqual(
        bounds.viewBottom + 0.5,
      )
    }
  })
})
