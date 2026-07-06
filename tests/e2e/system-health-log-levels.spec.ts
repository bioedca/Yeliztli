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

const EXCEPTION_TRACEBACK = [
  'Traceback (most recent call last):',
  '  File "/home/app/backend/api/routes/genes.py", line 264, in _fetch_uniprot_from_api',
  '    resp.raise_for_status()',
  "httpx.HTTPStatusError: Client error '400 Bad Request'",
].join('\n')

const EXCEPTION_LOG = {
  id: 6,
  timestamp: '2026-03-26T12:05:00',
  level: 'EXCEPTION',
  logger: 'backend.api.routes.genes',
  message: 'uniprot_fetch_failed',
  event_data: JSON.stringify({
    gene: 'CFTR',
    exception: EXCEPTION_TRACEBACK,
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
    await expect(page.getByText('uniprot_fetch_failed')).toBeVisible()
    await expect(page.getByText('Application started')).toHaveCount(0)

    const exceptionRow = page.locator('tr', { hasText: 'uniprot_fetch_failed' })
    const levelCell = exceptionRow.locator('td').nth(1)
    await expect(levelCell).toHaveText('EXCEPTION')
    await expect(levelCell).toHaveClass(/text-red-700/)
  })

  test('renders expanded EXCEPTION tracebacks as multiline text', async ({ page }) => {
    await page.route('**/api/admin/logs**', (route) =>
      route.fulfill(
        jsonRoute({
          entries: [EXCEPTION_LOG],
          total: 1,
          page: 1,
          page_size: 50,
          has_more: false,
        }),
      ),
    )

    await page.goto('/settings/health')
    await waitForReactHydration(page)

    await page.getByText('uniprot_fetch_failed').click()

    const details = page.getByTestId('log-entry-details-6')
    await expect(details).toContainText('CFTR')
    await expect(details).toContainText('exception')
    const detailText = await details.textContent()
    expect(detailText).toContain(
      'Traceback (most recent call last):\n  File "/home/app/backend/api/routes/genes.py"',
    )
    expect(detailText).not.toContain('\\n  File "/home/app/backend/api/routes/genes.py"')
  })
})
