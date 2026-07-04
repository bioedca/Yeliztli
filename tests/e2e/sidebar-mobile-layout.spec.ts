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
    const main = page.locator('#main-content')
    const nav = page.getByRole('navigation', { name: 'Main navigation' })
    const mainBox = await main.boundingBox()
    const navBox = await nav.boundingBox()
    const mainPrecedesNav = await nav.evaluate((navElement) => {
      const mainElement = document.querySelector('#main-content')

      if (!mainElement) {
        return false
      }

      return Boolean(mainElement.compareDocumentPosition(navElement) & Node.DOCUMENT_POSITION_FOLLOWING)
    })

    expect(mainBox).not.toBeNull()
    expect(navBox).not.toBeNull()
    expect(mainBox!.width).toBeGreaterThanOrEqual(viewportWidth - 2)
    expect(navBox!.width).toBeGreaterThanOrEqual(viewportWidth - 2)
    expect(navBox!.height).toBeLessThan(100)
    expect(mainBox!.y + mainBox!.height).toBeLessThanOrEqual(navBox!.y + 1)
    expect(mainPrecedesNav).toBe(true)
  })
})
