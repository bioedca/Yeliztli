import { test, expect } from '@playwright/test'
import { bypassSetup } from './helpers'

const BACKEND_URL = process.env.BACKEND_URL ?? 'http://localhost:8000'

test.beforeEach(async ({ page }) => {
  await bypassSetup(page)
})

test.describe('Application smoke tests', () => {
  test('homepage loads successfully', async ({ page }) => {
    await page.goto('/')
    // The app should render without errors
    await expect(page).toHaveTitle(/Yeliztli/)
  })

  test('document head wires the brand favicon and PWA manifest', async ({ page }) => {
    await page.goto('/')
    await expect(
      page.locator('link[rel="icon"][type="image/svg+xml"]'),
    ).toHaveAttribute('href', '/favicon.svg')
    await expect(page.locator('link[rel="manifest"]')).toHaveAttribute(
      'href',
      '/manifest.webmanifest',
    )
    await expect(page.locator('link[rel="apple-touch-icon"]')).toHaveAttribute(
      'href',
      '/apple-touch-icon.png',
    )

    // The manifest is served and describes the app (incl. a maskable icon).
    const res = await page.request.get('/manifest.webmanifest')
    expect(res.ok()).toBeTruthy()
    const manifest = await res.json()
    expect(manifest.name).toBe('Yeliztli')
    expect(Array.isArray(manifest.icons)).toBeTruthy()
    expect(JSON.stringify(manifest.icons)).toContain('maskable')
  })

  test('health endpoint responds', async ({ request }) => {
    const response = await request.get(`${BACKEND_URL}/api/health`)
    expect(response.ok()).toBeTruthy()
    const body = await response.json()
    expect(body.status).toBe('ok')
  })
})
