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

  test('health endpoint responds', async ({ request }) => {
    const response = await request.get(`${BACKEND_URL}/api/health`)
    expect(response.ok()).toBeTruthy()
    const body = await response.json()
    expect(body.status).toBe('ok')
  })
})
