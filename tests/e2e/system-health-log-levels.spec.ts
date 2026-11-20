import { expect, test } from '@playwright/test'
import { bypassSetup, waitForReactHydration } from './helpers'

function jsonRoute(body: unknown, status = 200) {
  return {
    status,
    contentType: 'application/json',
    body: JSON.stringify(body),
  }
}

const STATUS_RESPONSE = {
  version: '0.2.0',
  uptime_seconds: 3661,
  data_dir: '/tmp/.yeliztli',
  active_jobs: [],
  total_samples: 0,
  auth_enabled: false,
  log_level: 'INFO',
}

const DISK_RESPONSE = {
  data_dir: '/tmp/.yeliztli',
  total_bytes: 500_000_000_000,
  free_bytes: 200_000_000_000,
  used_bytes: 300_000_000_000,
  reference_dbs_bytes: 4_000_000_000,
  sample_dbs_bytes: 50_000_000,
  logs_bytes: 1_000_000,
  other_bytes: 500_000,
}

const EXCEPTION_LOG = {
  id: 6,
  timestamp: '2026-03-26T12:05:00',
  level: 'EXCEPTION',
  logger: null,
  message: 'Operation failed',
  event_data: JSON.stringify({
    reason: 'invalid_input',
  }),
}

const ERROR_LOG = {
  id: 5,
  timestamp: '2026-03-26T12:00:00',
  level: 'ERROR',
  logger: 'backend.annotation.engine',
  message: 'Annotation batch failed',
  event_data: '{"batch_size":1000}',
}

const INFO_LOG = {
  id: 4,
  timestamp: '2026-03-26T11:55:00',
  level: 'INFO',
  logger: 'backend.main',
  message: 'Application started',
  event_data: null,
}

test.describe('System Health log level filtering', () => {
  test.beforeEach(async ({ page }) => {
    await bypassSetup(page)
    await page.route('**/api/preferences/update-check-interval', (route) =>
      route.fulfill(jsonRoute({ update_check_interval: 'off' })),
    )
    await page.route('**/api/updates/app-update', (route) =>
      route.fulfill(
        jsonRoute({
          update_available: false,
          current_version: '0.2.0',
          latest_version: null,
          release_url: null,
          release_notes: null,
          error: null,
        }),
      ),
    )
    await page.route('**/api/samples', (route) => route.fulfill(jsonRoute([])))
    await page.route('**/api/individuals', (route) => route.fulfill(jsonRoute([])))
    await page.route('**/api/admin/status', (route) => route.fulfill(jsonRoute(STATUS_RESPONSE)))
    await page.route('**/api/admin/disk-usage', (route) =>
      route.fulfill(jsonRoute(DISK_RESPONSE)),
    )
    await page.route('**/api/admin/db-stats', (route) => route.fulfill(jsonRoute([])))
    await page.route('**/api/admin/sample-stats', (route) => route.fulfill(jsonRoute([])))
    await page.route('**/api/databases/health', (route) =>
      route.fulfill(jsonRoute({ databases: [] })),
    )
  })

  test('exposes EXCEPTION as a filterable log level', async ({ page }) => {
    const requestedLogLevels: Array<string | null> = []

    await page.route('**/api/admin/logs**', (route) => {
      const url = new URL(route.request().url())
      const level = url.searchParams.get('level')
      requestedLogLevels.push(level)
      const entries = level === 'EXCEPTION'
        ? [EXCEPTION_LOG]
        : [EXCEPTION_LOG, ERROR_LOG, INFO_LOG]

      return route.fulfill(
        jsonRoute({
          entries,
          presentation_policy_version: 3,
          total: entries.length,
          page: 1,
          page_size: 50,
          has_more: false,
        }),
      )
    })

    await page.goto('/settings/health')
    await waitForReactHydration(page)

    const levelFilter = page.getByLabel('Filter by log level')
    await expect(levelFilter.locator('option[value="EXCEPTION"]')).toHaveText('EXCEPTION')
    await expect(page.getByText('Application started')).toBeVisible()

    await levelFilter.selectOption('EXCEPTION')

    await expect
      .poll(() => requestedLogLevels.includes('EXCEPTION'))
      .toBe(true)
    await expect(page.getByText('Operation failed')).toBeVisible()
    await expect(page.getByText('Application started')).toHaveCount(0)

    const exceptionRow = page.locator('tr', { hasText: 'Operation failed' })
    const levelCell = exceptionRow.locator('td').nth(1)
    await expect(levelCell).toHaveText('EXCEPTION')
    await expect(levelCell).toHaveClass(/text-red-700/)
  })

  test('shows the approved reason in expanded EXCEPTION details', async ({ page }) => {
    await page.route('**/api/admin/logs**', (route) =>
      route.fulfill(
        jsonRoute({
          entries: [EXCEPTION_LOG],
          presentation_policy_version: 3,
          total: 1,
          page: 1,
          page_size: 50,
          has_more: false,
        }),
      ),
    )

    await page.goto('/settings/health')
    await waitForReactHydration(page)

    await page.getByText('Operation failed').click()

    const details = page.getByTestId('log-entry-details-6')
    await expect(details).toContainText('reason')
    await expect(details).toContainText('invalid_input')
    await expect(details).not.toContainText('Traceback')
  })

  test('withholds a delayed prior-revision response before displaying its rows', async ({ page }) => {
    let release!: () => void
    const delay = new Promise<void>((resolve) => { release = resolve })
    await page.route('**/api/admin/logs**', async (route) => {
      await delay
      await route.fulfill(jsonRoute({
        entries: [{ ...EXCEPTION_LOG, message: 'old fixture diagnostic' }],
        presentation_policy_version: 2, total: 1, page: 1, page_size: 50, has_more: false,
      }))
    })
    await page.goto('/settings/health')
    await expect(page.getByLabel('Filter by log level')).toBeVisible()
    await expect(page.getByText('old fixture diagnostic')).toHaveCount(0)
    release()
    await expect(page.getByText(/waiting for a verified service upgrade/)).toBeVisible({ timeout: 20000 })
    await expect(page.getByText('old fixture diagnostic')).toHaveCount(0)

  })

  test('does not show a recovery warning for a ready database with a successful detail', async ({ page }) => {
    // Routes registered later take precedence, so this replaces the empty
    // health response installed by beforeEach for this one browser flow.
    await page.route('**/api/databases/health', (route) =>
      route.fulfill(
        jsonRoute({
          databases: [
            {
              name: 'clinvar',
              display_name: 'ClinVar',
              build_mode: 'download',
              required: true,
              state: 'ready',
              present: true,
              version: '20260315',
              downloaded_at: '2026-03-15T00:00:00',
              file_size_bytes: 250_000_000,
              expected_size_bytes: 250_000_000,
              integrity_ok: true,
              integrity_detail: 'ok',
              resumable: false,
              download_id: null,
              downloaded_bytes: null,
              total_bytes: null,
              progress_pct: null,
              active_job_id: null,
              last_error: null,
              can_clean: false,
              can_resume: false,
              can_verify: true,
            },
          ],
        }),
      ),
    )

    await page.goto('/settings/health')
    await waitForReactHydration(page)

    await expect(page.getByText('ClinVar')).toBeVisible()
    await expect(
      page.getByText(
        'Database integrity needs attention. Re-check or clean and re-download the database.',
      ),
    ).toHaveCount(0)
  })
})
