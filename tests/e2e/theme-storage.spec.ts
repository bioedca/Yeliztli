/** Theme controls remain usable when optional browser storage fails (#2670). */
import { expect, test, type Page } from '@playwright/test'
import AxeBuilder from '@axe-core/playwright'
import { bypassSetup, waitForReactHydration } from './helpers'

type Theme = 'light' | 'dark' | 'system'
type StorageMode = 'access' | 'read' | 'write' | 'normal'
interface StorageCalls {
  mode: StorageMode
  read: number
  write: number
  access: number
}

declare global {
  interface Window {
    __themeStorageCalls: StorageCalls
  }
}

const json = (body: unknown) => ({ contentType: 'application/json', body: JSON.stringify(body) })

async function mockApp(page: Page) {
  const unexpected: string[] = []
  const errors: string[] = []
  page.on('pageerror', error => errors.push(error.message))
  await page.route('**/api/**', route => {
    const request = route.request()
    const path = new URL(request.url()).pathname
    // The Vite development server also serves modules under /src/api/.
    if (!path.startsWith('/api/')) return route.continue()
    if (path === '/api/preferences/theme' && request.method() === 'PUT') {
      return route.fulfill(json(request.postDataJSON()))
    }
    const responses: Record<string, unknown> = {
      '/api/individuals': [],
      '/api/samples': [],
      '/api/admin/status': { version: '0.2.0', uptime_seconds: 60, data_dir: 'synthetic', active_jobs: [], total_samples: 0, auth_enabled: false, log_level: 'INFO' },
      '/api/admin/disk-usage': { data_dir: 'synthetic', total_bytes: 1000000, free_bytes: 900000, used_bytes: 100000, reference_dbs_bytes: 100000, sample_dbs_bytes: 0, logs_bytes: 0, other_bytes: 0 },
      '/api/admin/logs': { entries: [], total: 0, page: 1, page_size: 50, has_more: false },
      '/api/admin/db-stats': [],
      '/api/admin/sample-stats': [],
      '/api/preferences/update-check-interval': { update_check_interval: 'off' },
      '/api/updates/app-update': { update_available: false, current_version: '0.2.0', latest_version: null, release_url: null, release_notes: null, error: null },
      '/api/updates/status': [],
      '/api/updates/check': { available: [], up_to_date: [], errors: [], checked_at: null },
      '/api/databases/health': { databases: [] },
    }
    if (request.method() === 'GET' && Object.hasOwn(responses, path)) {
      return route.fulfill(json(responses[path]))
    }
    unexpected.push(`${request.method()} ${path}`)
    return route.fulfill({ status: 501, ...json({ detail: 'Synthetic endpoint not configured' }) })
  })
  await bypassSetup(page)
  return { unexpected, errors }
}

async function injectStorageFailure(page: Page, mode: StorageMode) {
  await page.addInitScript(mode => {
    const storage = window.localStorage
    const calls: StorageCalls = { mode, read: 0, write: 0, access: 0 }
    window.__themeStorageCalls = calls
    storage.setItem('gi-theme', mode === 'write' ? 'light' : 'dark')
    if (mode === 'access') {
      Object.defineProperty(window, 'localStorage', {
        configurable: true,
        get() {
          calls.access += 1
          throw new DOMException('Synthetic storage access denial', 'SecurityError')
        },
      })
    } else if (mode === 'read' || mode === 'write') {
      const method = mode === 'read' ? 'getItem' : 'setItem'
      const prototype = Object.getPrototypeOf(storage)
      const original = prototype[method]
      Object.defineProperty(prototype, method, {
        configurable: true,
        writable: true,
        value(this: Storage, key: string, value?: string) {
          if (key === 'gi-theme') {
            calls[mode] += 1
            throw new DOMException('Synthetic theme-storage failure', mode === 'read' ? 'SecurityError' : 'QuotaExceededError')
          }
          return original.call(this, key, value)
        },
      })
    }
  }, mode)
}

async function expectDark(page: Page, dark: boolean) {
  if (dark) await expect(page.locator('html')).toHaveClass(/\bdark\b/)
  else await expect(page.locator('html')).not.toHaveClass(/\bdark\b/)
}

const scenarios: { mode: StorageMode, label: string, initial: Theme, changes: Theme[] }[] = [
  { mode: 'access', label: 'storage access denied', initial: 'system', changes: ['light', 'dark'] },
  { mode: 'read', label: 'theme reads denied', initial: 'system', changes: ['light', 'dark'] },
  { mode: 'write', label: 'theme writes denied', initial: 'light', changes: ['dark', 'system'] },
  { mode: 'normal', label: 'browser storage available', initial: 'dark', changes: ['system', 'light'] },
]
if (scenarios.length === 0) throw new Error('Theme storage coverage must include scenarios')

for (const { mode, label, initial, changes } of scenarios) {
  test(`theme controls and backend requests work with ${label}`, async ({ page }) => {
    const { unexpected, errors } = await mockApp(page)
    await injectStorageFailure(page, mode)
    await page.emulateMedia({ colorScheme: 'light' })
    await page.goto('/settings/health')
    await waitForReactHydration(page)
    await expect(page.getByRole('heading', { name: 'System Health', exact: true })).toBeVisible()
    await expect(page.getByRole('region', { name: 'Database health', exact: true })
      .getByText('No databases registered.', { exact: true })).toBeVisible()
    const toggle = page.getByTestId('theme-toggle')
    await expect(toggle).toHaveAccessibleName(`Theme: ${initial}`)
    await expectDark(page, initial === 'dark')
    const initialCalls = await page.evaluate(() => window.__themeStorageCalls)
    expect(initialCalls.mode).toBe(mode)
    if (mode !== 'normal') expect(initialCalls[mode]).toBeGreaterThan(0)

    if (initial === 'system') {
      await page.emulateMedia({ colorScheme: 'dark' })
      await expectDark(page, true)
      await page.emulateMedia({ colorScheme: 'light' })
      await expectDark(page, false)
    }
    expect(changes).toHaveLength(2)
    for (const theme of changes) {
      await toggle.focus()
      await expect(toggle).toBeFocused()
      const responsePromise = page.waitForResponse(response =>
        new URL(response.url()).pathname === '/api/preferences/theme'
        && response.request().method() === 'PUT')
      await page.keyboard.press('Enter')
      const response = await responsePromise
      expect(response.request().headers()['content-type']).toContain('application/json')
      expect(response.request().postDataJSON()).toEqual({ theme })
      expect(response.status()).toBe(200)
      expect(await response.json()).toEqual({ theme })
      await expect(toggle).toHaveAccessibleName(`Theme: ${theme}`)
      await expectDark(page, theme === 'dark')
      if (mode === 'write' || mode === 'normal') {
        expect(await page.evaluate(() => localStorage.getItem('gi-theme')))
          .toBe(mode === 'write' ? 'light' : theme)
      }
    }
    const finalCalls = await page.evaluate(() => window.__themeStorageCalls)
    if (mode === 'write') expect(finalCalls.write).toBeGreaterThan(initialCalls.write)
    await expect(page.getByRole('banner')).toBeVisible()
    const accessibility = await new AxeBuilder({ page }).include('header')
      .withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa']).analyze()
    expect(accessibility.passes.length).toBeGreaterThan(0)
    expect(accessibility.violations).toEqual([])
    expect(unexpected).toEqual([])
    expect(errors).toEqual([])
  })
}
