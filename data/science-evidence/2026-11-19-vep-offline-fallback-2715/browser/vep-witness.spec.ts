import { test, expect } from '@playwright/test'
import AxeBuilder from '@axe-core/playwright'
import { bypassSetup, mockFreshSampleState, waitForReactHydration } from '../../../../tests/e2e/helpers'

test('renders the actual historical offline protein result', async ({ page }) => {
  await page.route(/^https?:\/\/(?!127\.0\.0\.1:42716(?:\/|$))/, route => route.abort())
  await bypassSetup(page)
  await mockFreshSampleState(page)
  await page.route(/\/api\/(samples|individuals|watches)(?:\?|$)/, route => route.fulfill({json: []}))
  await page.route('**/api/annotation/active/1', route => route.fulfill({status: 404}))
  const result = page.waitForResponse(r => new URL(r.url()).pathname === '/api/variants/rs1051266')
  await page.goto('/variants/rs1051266?sample_id=1')
  await waitForReactHydration(page)
  const returned = await result
  expect(returned.status()).toBe(200)
  const body = await returned.json()
  expect(body.hgvs_protein).toBe('p.Arg27His')
  const overview = page.getByTestId('tab-overview')
  await expect(overview.getByText('p.Arg27His', { exact: true })).toBeVisible()
  await expect(overview.getByText('p.His27Arg', { exact: true })).toHaveCount(0)
  const { violations } = await new AxeBuilder({page}).include('[data-testid="tab-overview"]').analyze()
  expect(violations.map(finding => finding.id)).toEqual([])
  await page.getByRole('tab', { name: /protein/i }).click()
  await expect(page.getByText('p.Arg27His', { exact: true })).toBeVisible()
})
