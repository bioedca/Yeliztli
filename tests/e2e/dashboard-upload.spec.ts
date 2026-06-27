import { test, expect, type Page } from '@playwright/test'
import { bypassSetup } from './helpers'

const jsonRoute = (body: unknown) => ({
  status: 200,
  contentType: 'application/json',
  body: JSON.stringify(body),
})

async function mockEmptyDashboard(page: Page): Promise<void> {
  await bypassSetup(page)
  await page.route('**/api/updates/app-update', (route) =>
    route.fulfill(jsonRoute({ update_available: false, latest_version: null })),
  )
  await page.route('**/api/analysis/ancestry/lai/status', (route) =>
    route.fulfill(jsonRoute({ available: false, current_version: null, degraded_coverage: false })),
  )
  await page.route('**/api/updates/prompts**', (route) => route.fulfill(jsonRoute([])))
  await page.route('**/api/updates/status', (route) => route.fulfill(jsonRoute([])))
  await page.route('**/api/updates/check', (route) =>
    route.fulfill(jsonRoute({ available: [], up_to_date: [], errors: [], checked_at: null })),
  )
  await page.route('**/api/databases', (route) =>
    route.fulfill(jsonRoute({ databases: [], total_size_bytes: 0, downloaded_count: 0, total_count: 0 })),
  )
  await page.route('**/api/samples', (route) => route.fulfill(jsonRoute([])))
  await page.route('**/api/individuals', (route) => route.fulfill(jsonRoute([])))
}

test.describe('Dashboard upload', () => {
  test('rejects ZIP archives before calling ingest', async ({ page }) => {
    await mockEmptyDashboard(page)

    let ingestRequests = 0
    await page.route('**/api/ingest', (route) => {
      ingestRequests += 1
      return route.fulfill({
        status: 500,
        contentType: 'application/json',
        body: JSON.stringify({ detail: 'ZIP should not reach ingest' }),
      })
    })

    await page.goto('/')
    await expect(page.getByRole('heading', { name: 'Dashboard' })).toBeVisible()
    await expect(page.getByText(/Drop your 23andMe or AncestryDNA file here/i)).toBeVisible()

    await page.locator('input[type="file"]').setInputFiles({
      name: 'genome_Joshua_Yoakem_v5_Full_20250127054538.zip',
      mimeType: 'application/zip',
      buffer: Buffer.from('PK\x03\x04zip bytes'),
    })

    await expect(page.getByText('Upload failed')).toBeVisible()
    await expect(page.getByText(/This looks like a ZIP archive/i)).toBeVisible()
    await expect(
      page.getByText(/Extract the raw 23andMe\/AncestryDNA \.txt file first/i),
    ).toBeVisible()
    expect(ingestRequests).toBe(0)
  })
})
