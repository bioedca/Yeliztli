import { test, expect } from '@playwright/test'
import { bypassSetup, waitForReactHydration } from './helpers'

test.beforeEach(async ({ page }) => {
  await bypassSetup(page)
  await page.route('**/api/samples', (route) =>
    route.fulfill({ status: 200, contentType: 'application/json', body: '[]' }),
  )
  await page.route('**/api/individuals', (route) =>
    route.fulfill({ status: 200, contentType: 'application/json', body: '[]' }),
  )
})

test.describe('mobile app navigation layout', () => {
  test('keeps the main content full width on phone viewports (#1515)', async ({ page }) => {
    await page.setViewportSize({ width: 375, height: 800 })
    await page.goto('/not-a-real-route')
    await waitForReactHydration(page)

    const viewportWidth = page.viewportSize()?.width ?? 375
    const mainBox = await page.locator('#main-content').boundingBox()
    const navBox = await page.getByRole('navigation', { name: 'Main navigation' }).boundingBox()

    expect(mainBox).not.toBeNull()
    expect(navBox).not.toBeNull()
    expect(mainBox!.width).toBeGreaterThanOrEqual(viewportWidth - 2)
    expect(navBox!.width).toBeGreaterThanOrEqual(viewportWidth - 2)
    expect(navBox!.height).toBeLessThan(100)
  })
})
