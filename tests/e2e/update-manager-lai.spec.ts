/**
 * Step 33 — UpdateManager "Update now" for LAI (E2E).
 *
 * Preloads an LAI bundle row whose `current_version="unknown-pre-manifest"`
 * (the backfill sentinel from `alembic/versions/007_add_auto_update_settings.py`)
 * along with an available update in /api/updates/check. Clicks "Update now"
 * in Settings > Update Manager, lets the mocked Huey job report `complete`,
 * then asserts:
 *   1. the LAI row's current version flips to the new manifest version, and
 *   2. an entry "unknown-pre-manifest → v1.1" appears in the history log.
 *
 * Implementation mirrors `setup-wizard-lai.spec.ts`: every relevant backend
 * endpoint is intercepted with `page.route()` so the spec runs deterministically
 * across Chromium / Firefox / WebKit without depending on Huey, network, or
 * any real `database_versions` state. A `stage` flag flips the mocked
 * responses from pre-update to post-update once `/api/updates/trigger` fires,
 * so the subsequent invalidations driven by `useTriggerUpdate.onSuccess`
 * pick up the new state.
 */

import { expect, test } from '@playwright/test'

// ── Fixture data ────────────────────────────────────────────────────────

const LAI_PRE_VERSION = 'unknown-pre-manifest'
const LAI_NEW_VERSION = 'v1.1'
const LAI_DISPLAY_NAME = 'LAI Bundle (Chromosome Painting)'
const LAI_NEW_SIZE = 523_801_111
const LAI_JOB_ID = 'job-lai-update-1'

const APP_UPDATE_RESPONSE = {
  update_available: false,
  current_version: '1.0.0',
  latest_version: null,
  release_url: null,
  release_notes: null,
  error: null,
}

function jsonRoute(body: unknown, status = 200) {
  return {
    status,
    contentType: 'application/json',
    body: JSON.stringify(body),
  }
}

const UPDATE_AVAILABLE_LAI = {
  db_name: 'lai_bundle',
  latest_version: LAI_NEW_VERSION,
  download_size_bytes: LAI_NEW_SIZE,
  release_date: '2026-04-07',
}

// ── Spec ────────────────────────────────────────────────────────────────

test.describe('Step 33 — UpdateManager "Update now" for LAI', () => {
  test('clicking Update now upgrades LAI and records a history row', async ({
    page,
  }) => {
    // Stage advances from `pre_update` to `post_update` the moment the
    // backend is told to start the update — the polling endpoint then
    // reports `complete` and the React Query invalidations driven by
    // `useTriggerUpdate.onSuccess` fetch the post-update state.
    let stage: 'pre_update' | 'post_update' = 'pre_update'

    // Capture each db_name the frontend asked to update so we can assert
    // the click really hit the LAI row.
    const triggeredFor: string[] = []

    // ── Auth + setup status: skip both ─────────────────────────────────
    await page.route('**/api/auth/status', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          auth_enabled: false,
          has_password: false,
          authenticated: true,
        }),
      })
    })

    await page.route('**/api/setup/status', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          needs_setup: false,
          disclaimer_accepted: true,
          has_databases: true,
          has_samples: false,
          data_dir: '/tmp/.yeliztli',
        }),
      })
    })

    // ── Database statuses — flips post-update ──────────────────────────
    await page.route('**/api/updates/status', async (route) => {
      const currentVersion =
        stage === 'pre_update' ? LAI_PRE_VERSION : LAI_NEW_VERSION
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify([
          {
            db_name: 'lai_bundle',
            display_name: LAI_DISPLAY_NAME,
            current_version: currentVersion,
            version_display: currentVersion,
            downloaded_at: '2026-04-07T00:00:00Z',
            file_size_bytes: LAI_NEW_SIZE,
            auto_update: true,
            update_available: false,
            update_download_window: null,
          },
        ]),
      })
    })

    // ── Update check — LAI has an update before, none after ────────────
    await page.route('**/api/updates/check', async (route) => {
      const available =
        stage === 'pre_update' ? [UPDATE_AVAILABLE_LAI] : []
      const upToDate = stage === 'pre_update' ? [] : ['lai_bundle']
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          available,
          up_to_date: upToDate,
          errors: [],
          checked_at: '2026-05-08T12:00:00Z',
        }),
      })
    })

    // ── Update history — empty before, one row after ───────────────────
    await page.route('**/api/updates/history**', async (route) => {
      const history =
        stage === 'pre_update'
          ? []
          : [
              {
                id: 1,
                db_name: 'lai_bundle',
                previous_version: LAI_PRE_VERSION,
                new_version: LAI_NEW_VERSION,
                updated_at: '2026-05-08T12:01:00Z',
                variants_added: null,
                variants_reclassified: null,
                download_size_bytes: LAI_NEW_SIZE,
                duration_seconds: 42,
              },
            ]
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(history),
      })
    })

    // ── Re-annotation prompts: none ────────────────────────────────────
    await page.route('**/api/updates/prompts**', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify([]),
      })
    })

    // ── App-update banner endpoint: nothing to upgrade ─────────────────
    await page.route('**/api/updates/app-update', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(APP_UPDATE_RESPONSE),
      })
    })

    // ── Trigger endpoint — flip stage so subsequent fetches see the
    //    upgraded state; the polling step then reports completion.
    await page.route('**/api/updates/trigger', async (route) => {
      const body = JSON.parse(route.request().postData() ?? '{}')
      triggeredFor.push(body.db_name)
      stage = 'post_update'
      await route.fulfill({
        status: 202,
        contentType: 'application/json',
        body: JSON.stringify({
          job_id: LAI_JOB_ID,
          db_name: 'lai_bundle',
          message: 'Update queued for lai_bundle',
        }),
      })
    })

    // ── Job polling: the very first poll already reports complete so
    //    `pollJobUntilDone` returns immediately and onSuccess fires. ────
    await page.route(`**/api/updates/job/${LAI_JOB_ID}`, async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          job_id: LAI_JOB_ID,
          status: 'complete',
          progress_pct: 100,
          message: 'LAI bundle updated',
          error: null,
        }),
      })
    })

    // ── Drive the UI ──────────────────────────────────────────────────
    await page.goto('/settings/updates')
    await page.waitForLoadState('domcontentloaded')

    await expect(
      page.getByRole('heading', { name: 'Update Manager' }),
    ).toBeVisible()

    // The LAI row should render with the backfill sentinel as its current
    // version and a "v1.1" available-update marker.
    const laiRow = page
      .getByRole('row')
      .filter({ hasText: LAI_DISPLAY_NAME })
    await expect(laiRow).toBeVisible()
    await expect(laiRow).toContainText(LAI_PRE_VERSION)
    await expect(laiRow.getByText(LAI_NEW_VERSION, { exact: true })).toBeVisible()

    // History log opens to "No update history yet" prior to the click.
    const historyToggle = page.getByRole('button', { name: /Update History/i })
    await historyToggle.click()
    await expect(page.getByText(/No update history yet/i)).toBeVisible()
    await historyToggle.click() // collapse again so the post-update re-render is exercised

    // Click "Update now" inside the LAI row.
    await laiRow.getByRole('button', { name: /Update now/i }).click()

    // Once polling completes and the queries invalidate, the LAI row's
    // current version flips to v1.1 and the "Update now" button drops
    // out (no longer hasUpdate).
    await expect(
      laiRow.getByRole('button', { name: /Update now/i }),
    ).toBeHidden({ timeout: 15_000 })
    await expect(laiRow).toContainText(LAI_NEW_VERSION)
    await expect(laiRow).toContainText(/Up to date/i)

    // Verify the trigger really fired for lai_bundle.
    expect(triggeredFor).toEqual(['lai_bundle'])

    // Expand the history log and the lai_bundle section, then assert
    // the "unknown-pre-manifest → v1.1" entry appears.
    await historyToggle.click()
    await page
      .getByRole('button', { name: /^lai_bundle \(1\)$/ })
      .click()

    await expect(
      page.getByText(`${LAI_PRE_VERSION} → ${LAI_NEW_VERSION}`),
    ).toBeVisible()
  })

  test('shows a neutral reference-data staleness prompt without reclassification copy', async ({
    page,
  }) => {
    let dismissedPromptId: number | null = null

    await page.route('**/api/auth/status', async (route) => {
      await route.fulfill(
        jsonRoute({
          auth_enabled: false,
          has_password: false,
          authenticated: true,
        }),
      )
    })

    await page.route('**/api/setup/status', async (route) => {
      await route.fulfill(
        jsonRoute({
          needs_setup: false,
          disclaimer_accepted: true,
          has_databases: true,
          has_samples: true,
          data_dir: '/tmp/.yeliztli',
        }),
      )
    })

    await page.route('**/api/updates/status', async (route) => {
      await route.fulfill(
        jsonRoute([
          {
            db_name: 'gnomad',
            display_name: 'gnomAD',
            current_version: '4.1.0',
            version_display: '4.1.0',
            downloaded_at: '2026-04-07T00:00:00Z',
            file_size_bytes: 211_000_000,
            auto_update: false,
            update_available: false,
            update_download_window: null,
          },
        ]),
      )
    })

    await page.route('**/api/updates/check', async (route) => {
      await route.fulfill(
        jsonRoute({
          available: [],
          up_to_date: ['gnomad'],
          errors: [],
          checked_at: '2026-05-08T12:00:00Z',
        }),
      )
    })

    await page.route('**/api/updates/history**', async (route) => {
      await route.fulfill(jsonRoute([]))
    })

    await page.route('**/api/updates/prompts**', async (route) => {
      const url = route.request().url()
      if (route.request().method() === 'POST' && url.includes('/dismiss')) {
        dismissedPromptId = Number(url.match(/prompts\/(\d+)\/dismiss/)?.[1] ?? 0)
        await route.fulfill(jsonRoute({}))
        return
      }

      await route.fulfill(
        jsonRoute([
          {
            id: 17,
            sample_id: 101,
            db_name: 'reference_data',
            db_version: 'multiple',
            candidate_count: 0,
            watched_count: 0,
            watched_details: [],
            prompt_type: 'version_staleness',
            stale_databases: [
              {
                db_name: 'gnomad',
                recorded_version: '2.1.1',
                current_version: '4.1.0',
              },
              {
                db_name: 'lai_bundle',
                recorded_version: 'v1.0',
                current_version: 'v1.1',
              },
            ],
            created_at: '2026-05-08T12:00:00Z',
          },
        ]),
      )
    })

    await page.route('**/api/updates/finding-changes**', async (route) => {
      await route.fulfill(
        jsonRoute({
          sample_id: 101,
          available: false,
          release_deltas: [],
          changed: [],
          added: [],
          removed: [],
        }),
      )
    })

    await page.route('**/api/updates/app-update', async (route) => {
      await route.fulfill(jsonRoute(APP_UPDATE_RESPONSE))
    })

    await page.route('**/api/databases/health', async (route) => {
      await route.fulfill(jsonRoute({ databases: [] }))
    })

    await page.goto('/settings/updates')
    await page.waitForLoadState('domcontentloaded')

    const banner = page.getByRole('alert')
    await expect(banner).toBeVisible()
    await expect(banner).toContainText('Reference data updated')
    await expect(banner).toContainText(
      'Reference data is newer than 1 analysis (gnomad + lai_bundle).',
    )
    await expect(banner).toContainText('Re-annotate to refresh findings.')
    await expect(banner).not.toContainText('potential reclassification')

    await banner.getByRole('button', { name: 'Dismiss (reference data)' }).click()
    expect(dismissedPromptId).toBe(17)
  })
})
