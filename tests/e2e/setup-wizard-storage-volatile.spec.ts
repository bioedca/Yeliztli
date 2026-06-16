/**
 * E2E — setup wizard Storage step: volatile-filesystem warning (#754).
 *
 * The Storage step shows a non-blocking "Volatile storage location" warning when
 * the backend reports the chosen/suggested data dir is on a volatile filesystem
 * (e.g. /tmp), which is wiped on reboot. The warning is driven solely by the
 * `volatile` field of GET /api/setup/storage-info and is independent of disk
 * space. Continue stays enabled (it's advisory, not a block).
 *
 * Every backend endpoint needed to reach the Storage step is intercepted with
 * `page.route()`, so the spec is hermetic.
 */
import { expect, test, type Page } from '@playwright/test'

const DATA_DIR_VOLATILE = '/tmp/pr-verify/.yeliztli'
const DATA_DIR_PERSISTENT = '/home/user/.yeliztli'

const VOLATILE_MESSAGE =
  'This location is on a volatile filesystem (e.g. /tmp) that is typically ' +
  'erased when the machine restarts. Downloaded databases could be lost, ' +
  'forcing a full re-download. Choose a persistent location (such as your ' +
  'home directory) for a permanent install.'

type StorageInfoOverrides = {
  data_dir: string
  volatile: boolean
  volatile_message: string | null
}

async function routeToStorageStep(page: Page, storage: StorageInfoOverrides): Promise<void> {
  await page.route('**/api/auth/status', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ auth_enabled: false, has_password: false, authenticated: true }),
    })
  })

  await page.route('**/api/setup/status', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        disclaimer_accepted: true,
        data_dir: storage.data_dir,
        needs_setup: true,
        has_databases: false,
        required_dbs_ready: false,
        has_samples: false,
      }),
    })
  })

  await page.route('**/api/setup/disclaimer', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        title: 'Disclaimer',
        text: 'For research / educational use only.',
        accept_label: 'I Understand and Accept',
      }),
    })
  })

  await page.route('**/api/setup/detect-existing', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        existing_found: false,
        has_config: false,
        has_samples: false,
        has_databases: false,
        data_dir: storage.data_dir,
      }),
    })
  })

  await page.route('**/api/setup/storage-info', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        data_dir: storage.data_dir,
        free_space_bytes: 100_000_000_000,
        free_space_gb: 100,
        total_space_bytes: 500_000_000_000,
        total_space_gb: 500,
        status: 'ok',
        message: '100.0 GB free — sufficient for Yeliztli.',
        path_exists: true,
        path_writable: true,
        volatile: storage.volatile,
        volatile_message: storage.volatile_message,
      }),
    })
  })

  await page.goto('/setup')
  await page.waitForLoadState('domcontentloaded')
  // Step 1 — Import from backup → "Skip — Start Fresh" lands on the Storage step.
  await page.getByRole('button', { name: /Skip — Start Fresh/i }).click()
  await expect(page.getByRole('heading', { name: /Storage Location/i })).toBeVisible()
}

test('shows the volatile-storage warning when the data dir is on a volatile filesystem', async ({
  page,
}) => {
  await routeToStorageStep(page, {
    data_dir: DATA_DIR_VOLATILE,
    volatile: true,
    volatile_message: VOLATILE_MESSAGE,
  })

  const warning = page.getByRole('alert').filter({ hasText: 'Volatile storage location' })
  await expect(warning).toBeVisible()
  await expect(warning).toContainText(/erased when the machine restarts/i)

  // Advisory only — Continue stays enabled.
  await expect(page.getByRole('button', { name: 'Continue' })).toBeEnabled()
})

test('omits the volatile-storage warning for a persistent location', async ({ page }) => {
  await routeToStorageStep(page, {
    data_dir: DATA_DIR_PERSISTENT,
    volatile: false,
    volatile_message: null,
  })

  // Storage info has loaded (disk panel rendered) but no volatile warning shows.
  await expect(page.getByText(/Disk Space OK/i)).toBeVisible()
  await expect(page.getByText('Volatile storage location')).toHaveCount(0)
})
