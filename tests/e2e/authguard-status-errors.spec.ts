import { expect, test } from '@playwright/test'

const READY_SETUP_STATUS = {
  needs_setup: false,
  disclaimer_accepted: true,
  has_databases: true,
  required_dbs_ready: true,
  db_readiness: [],
  has_samples: true,
  data_dir: '/tmp/.yeliztli',
}

const AUTH_DISABLED_STATUS = {
  auth_enabled: false,
  has_password: false,
  authenticated: true,
}

test('protected routes fail closed when setup status is unavailable, then recover on retry', async ({
  page,
}) => {
  let setupCalls = 0

  await page.route('**/api/auth/status', async (route) => {
    await route.fulfill({ json: AUTH_DISABLED_STATUS })
  })
  await page.route('**/api/setup/status', async (route) => {
    setupCalls += 1
    await route.fulfill(
      setupCalls <= 2
        ? { status: 503, json: { detail: 'setup status unavailable' } }
        : { json: READY_SETUP_STATUS },
    )
  })

  await page.goto('/')

  const alert = page.getByRole('alert')
  await expect(alert).toContainText("Can't reach the Yeliztli backend")
  await expect(page.getByRole('button', { name: 'Retry' })).toBeVisible()
  await expect(page.getByRole('navigation', { name: 'Main navigation' })).toHaveCount(0)

  await page.getByRole('button', { name: 'Retry' }).click()

  await expect(page.getByRole('navigation', { name: 'Main navigation' })).toBeVisible()
  await expect(page.getByRole('alert')).toHaveCount(0)
  expect(setupCalls).toBe(3)
})

test('protected routes fail closed when auth status is unavailable', async ({ page }) => {
  await page.route('**/api/auth/status', async (route) => {
    await route.fulfill({ status: 503, json: { detail: 'auth status unavailable' } })
  })
  await page.route('**/api/setup/status', async (route) => {
    await route.fulfill({ json: READY_SETUP_STATUS })
  })

  await page.goto('/')

  await expect(page.getByRole('alert')).toContainText(
    "Can't reach the Yeliztli backend",
  )
  await expect(page.getByRole('navigation', { name: 'Main navigation' })).toHaveCount(0)
})
