/** Visible manual recovery and accessibility for legacy SpliceAI imports (#2477). */
import { expect, test, type Page } from '@playwright/test'
import AxeBuilder from '@axe-core/playwright'
import { bypassSetup, waitForReactHydration } from './helpers'

const json = (body: unknown) => ({ contentType: 'application/json', body: JSON.stringify(body) })

async function setup(page: Page) {
  await page.route('**/api/**', route => {
    const path = new URL(route.request().url()).pathname
    if (!path.startsWith('/api/')) return route.continue()
    const responses: Record<string, unknown> = {
      '/api/admin/status': { version: '0.2.0', uptime_seconds: 60, data_dir: 'synthetic', active_jobs: [], total_samples: 0, auth_enabled: false, log_level: 'INFO' },
      '/api/admin/disk-usage': { data_dir: 'synthetic', total_bytes: 1000000, free_bytes: 900000, used_bytes: 100000, reference_dbs_bytes: 100000, sample_dbs_bytes: 0, logs_bytes: 0, other_bytes: 0 },
      '/api/admin/logs': { entries: [], total: 0, page: 1, page_size: 50, has_more: false },
      '/api/preferences/update-check-interval': { update_check_interval: 'off' },
      '/api/updates/app-update': { update_available: false, current_version: '0.2.0', latest_version: null, release_url: null, release_notes: null, error: null },
    }
    return route.fulfill(json(responses[path] ?? []))
  })
  await bypassSetup(page)
}

const health = (ready: boolean) => ({
  databases: [{
    name: 'spliceai', display_name: 'SpliceAI', build_mode: 'manual', required: false,
    state: ready ? 'ready' : 'unverified', present: true, version: ready ? '1.3' : 'legacy',
    downloaded_at: null, file_size_bytes: 16384, expected_size_bytes: 200000000,
    integrity_ok: ready, integrity_detail: ready ? 'ok' : 'synthetic-private-diagnostic',
    resumable: false, download_id: null, downloaded_bytes: null, total_bytes: null,
    progress_pct: null, active_job_id: null, last_error: null,
    can_clean: false, can_resume: false, can_verify: true,
  }],
})

for (const theme of ['light', 'dark'] as const) {
  test(`legacy SpliceAI stays unverified until re-ingestion in ${theme} mode`, async ({ page }) => {
    test.setTimeout(45_000)
    await page.emulateMedia({ colorScheme: theme })
    await setup(page)
    let ready = false
    let importedAt = '2026-11-09T00:00:00Z'
    let verifications = 0
    await page.route('**/api/databases/health', route => {
      const result = health(ready)
      return route.fulfill(json({databases: result.databases.map(db => ({...db, downloaded_at: importedAt}))}))
    })
    await page.route('**/api/databases/spliceai/verify', route => {
      verifications += 1
      if (verifications === 3) return route.abort('failed')
      return route.fulfill(json({ db_name: 'spliceai', ok: false, detail: 'synthetic-private-diagnostic', depth: 'deep' }))
    })
    await page.goto('/settings/health')
    await waitForReactHydration(page)
    const panel = page.getByRole('region', { name: 'Database health', exact: true })
    await expect(panel.getByText('Unverified', { exact: true })).toBeVisible()
    await expect(panel.getByText('Not verified', { exact: true })).toBeVisible()
    await expect(panel.getByText(/1 database needs attention/)).toBeVisible()
    await expect(panel.getByRole('button', { name: 'Clean', exact: true })).toHaveCount(0)
    await expect(panel.getByRole('button', { name: 'Resume', exact: true })).toHaveCount(0)
    const instructions = panel.getByRole('link', { name: 'SpliceAI re-ingestion instructions' })
    await expect(instructions).toHaveAttribute('href', 'https://bioedca.github.io/Yeliztli/install/reference-data/#manual-spliceai-re-ingestion')
    await instructions.focus()
    await expect(instructions).toBeFocused()
    const verify = panel.getByRole('button', { name: 'Verify', exact: true })
    await verify.focus()
    await page.keyboard.press('Enter')
    await expect.poll(() => verifications).toBe(1)
    await expect(panel.getByText(/Re-ingest all original hg19 files/)).toHaveCount(2)
    await expect(panel.getByText('Unverified', { exact: true })).toBeVisible()
    await expect(panel).not.toContainText('synthetic-private-diagnostic')
    await expect(panel).not.toContainText('Integrity OK')
    const accessibility = await new AxeBuilder({ page }).include('[aria-label="Database health"]')
      .withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa']).analyze()
    expect(accessibility.violations).toEqual([])

    // The API now represents a successful import, not a Verify upgrade.
    ready = true
    await panel.getByRole('button', { name: 'Refresh database health' }).click()
    await expect(panel.getByText('Ready', { exact: true })).toBeVisible()
    await expect(panel.getByText('Unverified', { exact: true })).toHaveCount(0)
    await expect(instructions).toHaveCount(0)
    await expect(panel.getByText(/Re-ingest all original hg19 files/)).toHaveCount(0)
    await expect(panel.getByText('OK', { exact: true })).toBeVisible()

    // Deep verification can fail while the structural poll still reports Ready.
    await verify.click()
    await expect.poll(() => verifications).toBe(2)
    await expect(instructions).toBeVisible()
    await expect(panel.getByText('Failed', { exact: true })).toBeVisible()
    await expect(panel.getByText('OK', { exact: true })).toHaveCount(0)
    await expect(panel).not.toContainText('clean and re-download')
    await expect(panel).not.toContainText('synthetic-private-diagnostic')
    await expect(panel.getByRole('button', { name: 'Clean', exact: true })).toHaveCount(0)
    const failedAccessibility = await new AxeBuilder({ page }).include('[aria-label="Database health"]')
      .withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa']).analyze()
    expect(failedAccessibility.violations).toEqual([])
    await verify.click()
    await expect.poll(() => verifications).toBe(3)
    await expect(panel.getByText('Integrity check failed. Please try again.')).toBeVisible()
    await expect(panel.getByText('Failed', { exact: true })).toBeVisible()
    await expect(panel.getByText('OK', { exact: true })).toHaveCount(0)
    await expect(instructions).toBeVisible()
    // A new import of the same version clears feedback for the old installation.
    importedAt = '2026-11-09T00:01:00Z'
    await panel.getByRole('button', { name: 'Refresh database health' }).click()
    await expect(instructions).toHaveCount(0)
    await expect(panel.getByText(/Re-ingest all original hg19 files/)).toHaveCount(0)
  })
}
