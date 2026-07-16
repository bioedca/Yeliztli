/**
 * Big Five radar regressions: keep labels inside the SVG viewport (#650) and
 * keep unassessed dimensions visually distinct from measured Standard values
 * (#1980). Both checks render the real Traits page with route-mocked data.
 */

import { test, expect, type Page } from '@playwright/test'
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

async function mockTraitsRadar(
  page: Page,
  {
    snpDetails,
    calledSnps = snpDetails.length,
    totalSnps = calledSnps,
    missingSnps = [],
  }: {
    snpDetails: ReturnType<typeof snpDetail>[]
    calledSnps?: number
    totalSnps?: number
    missingSnps?: string[]
  },
) {
  const summary = {
    pathway_id: 'personality_big_five',
    pathway_name: 'Big Five Personality',
    level: 'Moderate',
    evidence_level: 2,
    prs_primary: false,
    called_snps: calledSnps,
    total_snps: totalSnps,
    missing_snps: missingSnps,
    no_call_snps: [],
    pmids: [],
  }

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
      jsonRoute({
        items: [summary],
        total: 1,
        cross_module: [],
        module_disclaimer: 'Research use only.',
      }),
    )
  })
  await page.route(
    '**/api/analysis/traits/pathway/personality_big_five**',
    async (route) => {
      await route.fulfill(jsonRoute({ ...summary, snp_details: snpDetails }))
    },
  )
}

test.describe('Big Five radar chart', () => {
  test('the longest axis label is fully visible in the real Traits page', async ({
    page,
  }) => {
    await mockTraitsRadar(page, {
      snpDetails: [
        snpDetail('openness'),
        snpDetail('conscientiousness', 'Elevated'),
        snpDetail('extraversion'),
        snpDetail('agreeableness'),
        snpDetail('neuroticism'),
      ],
    })

    await page.goto('/traits?sample_id=1')
    await waitForReactHydration(page)

    const radar = page.getByRole('img', {
      name: /Big Five personality trait associations/,
    })
    await expect(radar).toBeVisible()
    await expect(radar.locator('polygon[data-big-five-profile]')).toHaveCount(1)
    await expect(radar.locator('circle[data-big-five-point]')).toHaveCount(5)

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

  test('missing dimensions are not plotted as measured Standard values', async ({
    page,
  }) => {
    await mockTraitsRadar(page, {
      snpDetails: [
        snpDetail('openness', 'Moderate'),
        snpDetail('conscientiousness', 'Standard'),
      ],
      calledSnps: 2,
      totalSnps: 3,
      missingSnps: ['rs242949'],
    })

    await page.goto('/traits?sample_id=1')
    await waitForReactHydration(page)

    const radar = page.getByRole('img', {
      name: /2 of 5 dimensions assessed.*Not assessed: Extraversion, Agreeableness, Neuroticism/i,
    })
    await expect(radar).toBeVisible()

    await expect(
      radar.locator('circle[data-dimension="conscientiousness"]'),
    ).toBeVisible()
    await expect(
      radar.locator('circle[data-dimension="extraversion"]'),
    ).toHaveCount(0)
    await expect(radar.locator('polygon[data-big-five-profile]')).toHaveCount(0)

    const missingLabel = radar.locator('text[data-dimension="extraversion"]')
    await expect(missingLabel).toHaveAttribute(
      'data-assessment-state',
      'not-assessed',
    )
    await expect(missingLabel).toContainText('Not assessed')
    await expect(
      radar.locator(
        'line[data-big-five-axis][data-dimension="extraversion"]',
      ),
    ).toHaveAttribute('stroke-dasharray', '4 4')
    await expect(page.locator('[data-big-five-coverage]')).toContainText(
      '2 of 5 dimensions assessed. Not assessed: Extraversion, Agreeableness, Neuroticism.',
    )
  })
})
