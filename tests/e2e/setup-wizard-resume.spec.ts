/**
 * Setup wizard — resume an interrupted download (E2E, PR-21).
 *
 * On the Databases step a download-mode DB has a resumable partial: the step
 * shows "% saved" + a Resume control (driven by `/api/databases/health`).
 * Clicking Resume re-runs the download (mocked SSE → complete); once health
 * reports the DB integrity-`ready`, the row shows the "Verified" badge.
 *
 * Fully `page.route()`-intercepted: a `stage` flag flips the health + database
 * list from "resumable partial" to "ready/verified" when the resume completes.
 */

import { expect, test } from '@playwright/test'
import { mockWizardChrome, walkWizardToDatabases } from './helpers'

type Stage = 'partial' | 'done'

const baseDb = {
  name: 'encode_ccres',
  display_name: 'ENCODE cCREs',
  description: 'Candidate cis-Regulatory Elements for IGV.js track visualization',
  filename: 'encode_ccres.db',
  expected_size_bytes: 30_000_000,
  required: false,
  phase: 3,
  build_mode: 'download',
}

function dbList(stage: Stage) {
  const encode =
    stage === 'done'
      ? { ...baseDb, downloaded: true, file_size_bytes: 30_000_000 }
      : { ...baseDb, downloaded: false, file_size_bytes: null }
  return {
    databases: [encode],
    total_size_bytes: 30_000_000,
    downloaded_count: stage === 'done' ? 1 : 0,
    total_count: 1,
  }
}

function healthList(stage: Stage) {
  const encode =
    stage === 'done'
      ? {
          name: 'encode_ccres',
          display_name: 'ENCODE cCREs',
          build_mode: 'download',
          required: false,
          state: 'ready',
          present: true,
          version: 'v1-fixture',
          integrity_ok: true,
          integrity_detail: null,
          resumable: false,
          download_id: null,
          downloaded_bytes: 30_000_000,
          total_bytes: 30_000_000,
          progress_pct: 100,
          active_job_id: null,
          last_error: null,
          can_clean: true,
          can_resume: false,
          can_verify: true,
        }
      : {
          name: 'encode_ccres',
          display_name: 'ENCODE cCREs',
          build_mode: 'download',
          required: false,
          state: 'failed',
          present: false,
          version: null,
          integrity_ok: null,
          integrity_detail: null,
          resumable: true,
          download_id: 7,
          downloaded_bytes: 10_500_000,
          total_bytes: 30_000_000,
          progress_pct: 35,
          active_job_id: null,
          last_error: 'Connection reset',
          can_clean: true,
          can_resume: true,
          can_verify: false,
        }
  return { databases: [encode] }
}

test.describe('Setup wizard — resume an interrupted download', () => {
  test('shows "% saved" + Resume, then a Verified badge after resuming', async ({
    page,
  }) => {
    let stage: Stage = 'partial'

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
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(dbList(stage)),
      }),
    )
    await page.route('**/api/databases/health', (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(healthList(stage)),
      }),
    )

    // Resume → a session; the SSE below flips `stage` to done + reports complete.
    let resumeTriggered = false
    await page.route('**/api/databases/resume', (route) => {
      resumeTriggered = true
      return route.fulfill({
        status: 202,
        contentType: 'application/json',
        body: JSON.stringify({
          session_id: 'sess-resume',
          downloads: [{ db_name: 'encode_ccres', job_id: 'job-encode-resume' }],
        }),
      })
    })

    await page.route('**/api/databases/progress/**', (route) => {
      stage = 'done'
      const payload = JSON.stringify({
        session_id: 'sess-resume',
        databases: [
          {
            db_name: 'encode_ccres',
            job_id: 'job-encode-resume',
            status: 'complete',
            progress_pct: 100,
            message: 'ENCODE cCREs download complete',
            error: null,
            total_bytes: 30_000_000,
            downloaded_bytes: 30_000_000,
            speed_bps: 0,
            eta_seconds: 0,
          },
        ],
        aggregate: {
          total_bytes: 30_000_000,
          downloaded_bytes: 30_000_000,
          remaining_bytes: 0,
          overall_pct: 100,
          speed_bps: 0,
          eta_seconds: 0,
          size_unknown_count: 0,
        },
      })
      return route.fulfill({
        status: 200,
        contentType: 'text/event-stream',
        headers: { 'Cache-Control': 'no-cache' },
        body: `event: progress\ndata: ${payload}\n\n`,
      })
    })

    await walkWizardToDatabases(page)

    // The resumable partial is surfaced with "% saved" + a Resume button.
    await expect(page.getByText(/35% saved/i)).toBeVisible({ timeout: 10_000 })
    const resumeBtn = page.getByTestId('db-resume-encode_ccres')
    await expect(resumeBtn).toBeVisible()

    await resumeBtn.click()
    expect(resumeTriggered).toBe(true)

    // After the resume completes + health reports the DB ready, it's Verified.
    await expect(page.getByTestId('db-verified-encode_ccres')).toBeVisible({
      timeout: 15_000,
    })
  })
})
