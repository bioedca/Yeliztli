import { test, expect } from '@playwright/test'
import AxeBuilder from '@axe-core/playwright'
import { bypassSetup, mockFreshSampleState, waitForReactHydration } from '../../../../tests/e2e/helpers'

test.beforeEach(async ({ page }) => {
  await page.route(/^https?:\/\/(?!127\.0\.0\.1:42716(?:\/|$))/, route => route.abort())
  await bypassSetup(page)
  await mockFreshSampleState(page)
  await page.route(/\/api\/(samples|individuals|watches)(?:\?|$)/, route => route.fulfill({json: []}))
  await page.route('**/api/annotation/active/1', route => route.fulfill({status: 404}))
  const result = page.waitForResponse(response => {
    const url = new URL(response.url())
    return url.pathname === '/api/variants/rs1051266' && url.searchParams.get('sample_id') === '1'
  })
  await page.goto('/variants/rs1051266?sample_id=1')
  await waitForReactHydration(page)
  const response = await result
  expect(response.status()).toBe(200)
  expect((await response.json()).hgvs_protein).toBe('p.Arg27His')
})

test('renders the actual historical offline protein result', async ({ page }) => {
  const overview = page.getByTestId('tab-overview')
  await expect(overview.getByText('p.Arg27His', { exact: true })).toBeVisible()
  await expect(overview.getByText('p.His27Arg', { exact: true })).toHaveCount(0)
  const { violations } = await new AxeBuilder({page}).include('[data-testid="tab-overview"]').analyze()
  expect(violations.map(finding => finding.id)).toEqual([])

  const proteinTab = page.getByRole('tab', { name: /protein/i })
  await proteinTab.click()
  await expect(proteinTab).toHaveAttribute('aria-selected', 'true')
  const protein = page.getByTestId('tab-protein')
  await expect(protein).toBeVisible()
  await expect(protein.getByText('p.Arg27His', { exact: true })).toBeVisible()
  await expect(protein.getByText('p.His27Arg', { exact: true })).toHaveCount(0)
})

test('audits the Protein panel for accessibility', async ({ page }) => {
  await page.getByRole('tab', { name: /protein/i }).click()
  await expect(page.getByTestId('tab-protein')).toBeVisible()
  const { violations } = await new AxeBuilder({page}).include('[data-testid="tab-protein"]').analyze()
  expect(violations.map(({id, nodes}) => ({id, targets: nodes.map(node => node.target)}))).toEqual([])
})
