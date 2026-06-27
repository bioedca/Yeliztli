/**
 * Issue #1078 — heel eBMD is an inverse-direction PRS: a higher percentile
 * means higher genetically predicted bone density and lower fracture-risk
 * context. The eBMD view must state this direction explicitly.
 */

import { test, expect } from '@playwright/test'
import { bypassSetup, waitForReactHydration } from './helpers'

test.beforeEach(async ({ page }) => {
  await bypassSetup(page)
})

const jsonRoute = (payload: unknown, status = 200) => ({
  status,
  contentType: 'application/json',
  body: JSON.stringify(payload),
})

test.describe('eBMD PRS direction copy (#1078)', () => {
  test('states that lower eBMD percentile means higher fracture-risk context', async ({ page }) => {
    await page.route('**/api/analysis/ebmd/run**', async (route) => {
      await route.fulfill(jsonRoute({ findings_count: 0, prs_computed: false }))
    })
    await page.route('**/api/analysis/ebmd/prs**', async (route) => {
      await route.fulfill(
        jsonRoute({
          available: false,
          recommended_pgs_id: 'PGS000657',
          prs: null,
          context: {
            not_a_substitute:
              'This estimated bone-mineral-density polygenic score is research-grade.',
            direction:
              'Direction: a lower heel eBMD percentile indicates lower genetically predicted bone mineral density and higher fracture-risk context; a high percentile indicates higher genetically predicted bone density and is protective.',
            utility: 'A bone-density polygenic score can refine fracture-risk screening.',
            byo: 'Fetch PGS000657 into the local score database to enable scoring.',
            disclaimer: 'Research Use Only.',
          },
          research_use_only: true,
        }),
      )
    })

    await page.goto('/ebmd?sample_id=1')
    await waitForReactHydration(page)

    const context = page.getByTestId('ebmd-context')
    await expect(context).toBeVisible()
    await expect(context).toContainText('lower heel eBMD percentile')
    await expect(context).toContainText('higher fracture-risk context')
    await expect(context).toContainText('high percentile')
    await expect(context).toContainText('protective')
  })
})
