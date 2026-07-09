/**
 * Setup wizard — download observability (E2E, PR-21).
 *
 * Drives the wizard to the Databases step, triggers a download, and asserts the
 * aggregate progress bar surfaces the backend's SSE roll-up end-to-end: overall
 * percent, a non-zero transfer rate, and an ETA — plus the per-DB byte/rate
 * line. The backend is fully intercepted with `page.route()` so the spec is
 * deterministic across browsers and needs no real download.
 *
 * The mocked SSE event is `running` (non-terminal), so DatabasesStep keeps the
 * stream open and the aggregate bar stays visible to assert against.
 */

import { expect, test } from '@playwright/test'
import { mockWizardChrome, walkWizardToDatabases } from './helpers'

const DB_LIST = {
  databases: [
    {
      name: 'clinvar',
      display_name: 'ClinVar',
      description: 'Clinical variant interpretations from NCBI ClinVar',
      filename: 'clinvar.db',
      expected_size_bytes: 250_000_000,
      required: true,
      phase: 1,
      downloaded: true,
      file_size_bytes: 250_000_000,
      build_mode: 'pipeline',
    },
    {
      name: 'encode_ccres',
      display_name: 'ENCODE cCREs',
      description: 'Candidate cis-Regulatory Elements for IGV.js track visualization',
      filename: 'encode_ccres.db',
      expected_size_bytes: 30_000_000,
      required: false,
      phase: 3,
      downloaded: false,
      file_size_bytes: null,
      build_mode: 'download',
    },
  ],
  total_size_bytes: 280_000_000,
  downloaded_count: 1,
  total_count: 2,
}

// A mid-flight (non-terminal) progress frame: 70% saved, 8 MB/s, 90s to go.
const PROGRESS_EVENT = {
  session_id: 'sess-progress',
  databases: [
    {
      db_name: 'encode_ccres',
      job_id: 'job-encode-1',
      status: 'running',
      progress_pct: 70,
      message: 'Downloading ENCODE cCREs…',
      error: null,
      total_bytes: 30_000_000,
      downloaded_bytes: 21_000_000,
      speed_bps: 8_000_000,
      eta_seconds: 90,
    },
  ],
  aggregate: {
    total_bytes: 30_000_000,
    downloaded_bytes: 21_000_000,
    remaining_bytes: 9_000_000,
    overall_pct: 70,
    speed_bps: 8_000_000,
    eta_seconds: 90,
    size_unknown_count: 0,
  },
}

test.describe('Setup wizard — download progress observability', () => {
  test('aggregate bar shows overall %, speed and ETA from the SSE stream', async ({
    page,
  }) => {
    await mockWizardChrome(page)

    await page.route('**/api/setup/status', (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          needs_setup: true,
          disclaimer_accepted: true,
          has_databases: true,
          required_dbs_ready: false,
          db_readiness: [],
          has_samples: false,
          data_dir: '/tmp/.yeliztli',
        }),
      }),
    )

    await page.route('**/api/databases', (route) =>
      route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(DB_LIST) }),
    )
    // Health is polled on the Databases step; nothing in flight at load.
    await page.route('**/api/databases/health', (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ databases: [] }),
      }),
    )

    let downloadTriggered = false
    await page.route('**/api/databases/download', (route) => {
      downloadTriggered = true
      return route.fulfill({
        status: 202,
        contentType: 'application/json',
        body: JSON.stringify({
          session_id: 'sess-progress',
          downloads: [{ db_name: 'encode_ccres', job_id: 'job-encode-1' }],
        }),
      })
    })

    // Replace EventSource with a fake that delivers one mid-flight progress
    // frame and STAYS OPEN. A real download streams events and keeps the
    // connection alive, so the bar persists; a route.fulfill()-mocked SSE would
    // close immediately, firing DatabasesStep's error handler (isDownloading →
    // false) and hiding the bar before we can assert on it.
    await page.addInitScript((event) => {
      class FakeEventSource {
        url: string
        private listeners: Record<string, ((e: MessageEvent) => void)[]> = {}
        constructor(url: string) {
          this.url = url
          setTimeout(() => {
            for (const fn of this.listeners['progress'] ?? [])
              fn({ data: JSON.stringify(event) } as MessageEvent)
          }, 100)
        }
        addEventListener(type: string, fn: (e: MessageEvent) => void) {
          ;(this.listeners[type] ??= []).push(fn)
        }
        removeEventListener(type: string, fn: (e: MessageEvent) => void) {
          this.listeners[type] = (this.listeners[type] ?? []).filter((f) => f !== fn)
        }
        close() {}
      }
      ;(window as unknown as { EventSource: unknown }).EventSource = FakeEventSource
    }, PROGRESS_EVENT)

    await walkWizardToDatabases(page)

    // encode_ccres is default-selected; trigger the download.
    await expect(page.getByTestId('db-checkbox-encode_ccres')).toBeChecked()
    await page.getByRole('button', { name: /Download Selected/i }).click()
    expect(downloadTriggered).toBe(true)

    // The aggregate bar reflects the SSE roll-up.
    const aggregate = page.getByTestId('download-aggregate')
    await expect(aggregate).toBeVisible({ timeout: 10_000 })
    await expect(page.getByTestId('aggregate-pct')).toHaveText('70%')
    await expect(aggregate).toContainText('8.0 MB/s')
    await expect(page.getByTestId('aggregate-eta')).toContainText('~1m 30s')

    // The per-DB rate line carries the byte counts + speed.
    await expect(page.getByTestId('db-rate-encode_ccres')).toContainText('8.0 MB/s')
  })

  test('missing required bundled databases are selected for download', async ({
    page,
  }) => {
    await mockWizardChrome(page)

    await page.route('**/api/setup/status', (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          needs_setup: true,
          disclaimer_accepted: true,
          has_databases: true,
          required_dbs_ready: false,
          db_readiness: [
            { name: 'gnomad', state: 'not_installed', ready: false, build_mode: 'bundled' },
          ],
          has_samples: false,
          data_dir: '/tmp/.yeliztli',
        }),
      }),
    )

    await page.route('**/api/databases', (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          databases: [
            {
              name: 'clinvar',
              display_name: 'ClinVar',
              description: 'Clinical variant interpretations from NCBI ClinVar',
              filename: 'clinvar.db',
              expected_size_bytes: 250_000_000,
              required: true,
              phase: 1,
              downloaded: true,
              file_size_bytes: 250_000_000,
              build_mode: 'pipeline',
            },
            {
              name: 'gnomad',
              display_name: 'gnomAD',
              description: 'Population allele frequencies',
              filename: 'gnomad_af.db',
              expected_size_bytes: 1_301_509_755,
              required: true,
              phase: 2,
              downloaded: false,
              file_size_bytes: null,
              build_mode: 'bundled',
            },
          ],
          total_size_bytes: 1_551_509_755,
          downloaded_count: 1,
          total_count: 2,
        }),
      }),
    )
    await page.route('**/api/databases/health', (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ databases: [] }),
      }),
    )

    let requestedDatabases: string[] = []
    await page.route('**/api/databases/download', async (route) => {
      const body = await route.request().postDataJSON()
      requestedDatabases = body.databases ?? []
      return route.fulfill({
        status: 202,
        contentType: 'application/json',
        body: JSON.stringify({
          session_id: 'sess-gnomad',
          downloads: [{ db_name: 'gnomad', job_id: 'job-gnomad-1' }],
        }),
      })
    })

    await page.addInitScript(() => {
      class FakeEventSource {
        addEventListener() {}
        close() {}
      }
      ;(window as unknown as { EventSource: unknown }).EventSource = FakeEventSource
    })

    await walkWizardToDatabases(page)

    await expect(page.getByTestId('db-checkbox-gnomad')).toBeChecked()
    await expect(page.getByTestId('db-checkbox-gnomad')).toBeDisabled()
    await expect(page.getByText('Download required').first()).toBeVisible()

    await page.getByRole('button', { name: /Download Selected/i }).click()
    expect(requestedDatabases).toEqual(['gnomad'])
  })

  test('never-downloaded reference DBs with empty tables show download required', async ({
    page,
  }) => {
    await mockWizardChrome(page)

    await page.route('**/api/setup/status', (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          needs_setup: true,
          disclaimer_accepted: true,
          has_databases: true,
          required_dbs_ready: false,
          db_readiness: [
            { name: 'clinvar', state: 'not_installed', ready: false, build_mode: 'bundled' },
          ],
          has_samples: false,
          data_dir: '/tmp/.yeliztli',
        }),
      }),
    )

    await page.route('**/api/databases', (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          databases: [
            {
              name: 'clinvar',
              display_name: 'ClinVar',
              description: 'Clinical variant interpretations from NCBI ClinVar',
              filename: 'clinvar.db',
              expected_size_bytes: 250_000_000,
              required: true,
              phase: 1,
              downloaded: false,
              file_size_bytes: null,
              build_mode: 'pipeline',
            },
          ],
          total_size_bytes: 250_000_000,
          downloaded_count: 0,
          total_count: 1,
        }),
      }),
    )
    await page.route('**/api/databases/health', (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          databases: [
            {
              name: 'clinvar',
              state: 'not_installed',
              integrity_ok: false,
              integrity_detail: "table 'clinvar_variants' is empty",
              can_clean: false,
              can_verify: false,
            },
          ],
        }),
      }),
    )

    await walkWizardToDatabases(page)

    await expect(page.getByText('Download required')).toBeVisible()
    await expect(page.getByTestId('db-integrity-failed-clinvar')).toHaveCount(0)
    await expect(page.getByText(/Integrity failed/i)).toHaveCount(0)
  })

  test('corrupt bundled databases expose clean recovery controls', async ({
    page,
  }) => {
    await mockWizardChrome(page)

    await page.route('**/api/setup/status', (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          needs_setup: true,
          disclaimer_accepted: true,
          has_databases: true,
          required_dbs_ready: false,
          db_readiness: [
            { name: 'gnomad', state: 'corrupt', ready: false, build_mode: 'bundled' },
          ],
          has_samples: false,
          data_dir: '/tmp/.yeliztli',
        }),
      }),
    )

    await page.route('**/api/databases', (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          databases: [
            {
              name: 'gnomad',
              display_name: 'gnomAD',
              description: 'Population allele frequencies',
              filename: 'gnomad_af.db',
              expected_size_bytes: 1_301_509_755,
              required: true,
              phase: 2,
              downloaded: true,
              file_size_bytes: 1_301_509_755,
              build_mode: 'bundled',
            },
          ],
          total_size_bytes: 1_301_509_755,
          downloaded_count: 1,
          total_count: 1,
        }),
      }),
    )
    await page.route('**/api/databases/health', (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          databases: [
            {
              name: 'gnomad',
              state: 'corrupt',
              integrity_ok: false,
              integrity_detail: 'gnomad_af table is empty',
              can_clean: true,
              can_verify: true,
            },
          ],
        }),
      }),
    )

    await walkWizardToDatabases(page)

    await expect(page.getByTestId('db-integrity-failed-gnomad')).toContainText(
      'gnomad_af table is empty',
    )
    await expect(page.getByTestId('db-clean-gnomad')).toBeVisible()
    await expect(page.getByText('Included')).toHaveCount(0)
    await expect(page.getByText('Download required')).toHaveCount(0)
  })
})
